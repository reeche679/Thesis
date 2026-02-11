    import math
    from ortools.sat.python import cp_model
    # Assuming these local modules exist based on your environment
    from courses import PREREQ_ALIASES, YEAR_STANDING_RULES, courses
    import units_and_year_standings as u

    def _first_semester(info):
        """Get the first semester this course appears in. Supports orig_sem or offered_sems (int or list)."""
        if "orig_sem" in info:
            return info["orig_sem"]
        if "offered_sems" in info:
            o = info["offered_sems"]
            return min(o) if isinstance(o, (list, tuple)) else o
        return None


    def _sem_label(sem: int) -> str:
        """Convert semester index to '1st Year 1st Sem', '2nd Year 2nd Sem', '1st Year Summer', etc."""
        year_level, term_in_year = u.year_and_term_from_sem(sem)
        year_ord = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th", 6: "6th"}.get(year_level, f"{year_level}th")
        term_label = {1: "1st Sem", 2: "2nd Sem", 3: "Summer"}.get(term_in_year, f"Term {term_in_year}")
        return f"{year_ord} Year {term_label}"


    def _normalize_code(code: str) -> str:
        """Normalize course/year-standing codes for robust matching."""
        return " ".join(code.strip().split()).upper()


    def _get_req_list(info: dict, keys: tuple[str, ...]) -> list:
        """Get a list of requirement entries, accepting multiple key spellings."""
        for key in keys:
            if key in info:
                val = info.get(key)
                if val is None:
                    return []
                if isinstance(val, (list, tuple, set)):
                    return list(val)
                if isinstance(val, str):
                    return [val]
                return []
        return []


    def _log_infeasible(solver, status, current_sem, failed_courses, passed_courses, courses, units_module):
        """Print why the solver returned INFEASIBLE (or other non-success status)."""
        status_names = {
            cp_model.OPTIMAL: "OPTIMAL",
            cp_model.FEASIBLE: "FEASIBLE",
            cp_model.INFEASIBLE: "INFEASIBLE",
            cp_model.MODEL_INVALID: "MODEL_INVALID",
            cp_model.UNKNOWN: "UNKNOWN",
        }
        status_str = status_names.get(status, f"STATUS_{status}")

        print("\n" + "=" * 60)
        print("NO VALID SCHEDULE (Infeasible)")
        print("=" * 60)
        print(f"Solver status: {status_str}")
        if failed_courses:
            print(f"Failed courses (must be retaken): {', '.join(failed_courses)}")
        print()

        # Solver stats (can help with debugging)
        try:
            stats = solver.ResponseStats()
            if stats:
                print("Solver stats:")
                for line in stats.strip().split("\n"):
                    print(f"  {line}")
                print()
        except Exception:
            pass

        print("Possible reasons (check your curriculum and grades):")
        print("  1. Unit cap: Some semester has a unit limit (see units_and_year_standings).")
        print("     Failed + remaining courses may exceed the cap in one term (e.g. summer has 0 or low units).")
        print("  2. Offering window: Failed courses are only allowed in certain term types (Rigid/Bottleneck/Flexible).")
        print("     If too many must retake in the same narrow window, they can't all fit.")
        print("  3. Prerequisites: A failed course must be retaken before courses that need it.")
        print("     Offering windows + prereq order might leave no valid semester for some course.")
        print("  4. Year standing: Some courses require e.g. 90% of Year 1 units.")
        print("     Many failed year-1 courses can make it impossible to satisfy that in time.")
        print("  5. Co-requisites: Course A must be taken in same or later semester than B.")
        print("     Combined with other constraints, this can over-constrain the schedule.")
        print()

        # Per-semester unit caps for reference
        print("Unit caps by semester (from current onward):")
        for s in range(current_sem, units_module.MAX_SEMESTER + 1):
            cap = units_module.max_units_for_sem(s)
            print(f"  Semester {s}: max {cap} units")
        print("=" * 60)


    def run_pos_generator():
        print("--- BatStateU POS Generator: Irregular Student Logic ---")

        # 1. Input & Data Handling
        try:
            current_sem = int(input(f"Enter current semester index (1-{u.MAX_SEMESTER}): "))
        except ValueError:
            current_sem = 1

        passed_courses = []
        failed_courses = []

        # Only courses from past semesters: first_sem < current_sem (supports orig_sem or offered_sems)
        past_sem_courses = []
        for code, info in courses.items():
            first_sem = _first_semester(info)
            if first_sem is not None and first_sem < current_sem:
                past_sem_courses.append((code, info))
        past_sem_courses.sort(key=lambda x: (_first_semester(x[1]), x[0]))

        print(f"\nYou're in semester {current_sem}. Enter grades for semesters 1 to {current_sem - 1} only.")
        print("(1.00-3.00: Pass | 5.00/INC: Fail | Enter: Not Taken)")
        if not past_sem_courses:
            input("No courses from past semesters to grade (e.g. you're in semester 1). Press Enter to continue.")
        else:
            sem_nums = sorted(set(_first_semester(info) for _, info in past_sem_courses))
            print(f"Courses to grade: {len(past_sem_courses)} from semester(s) {sem_nums}\n")
            last_orig_sem = None
            for code, info in past_sem_courses:
                orig_sem = _first_semester(info)
                if last_orig_sem is None or orig_sem != last_orig_sem:
                    print(f"--- Semester {orig_sem} ---")
                    last_orig_sem = orig_sem
                grade_input = input(f"  Grade for {code} ({info.get('course_title', 'Course')}): ").strip().upper()
                if not grade_input:
                    continue
                try:
                    if grade_input in ["INC", "5.0", "5.00"] or float(grade_input) == 5.00:
                        failed_courses.append(code)
                    elif 1.00 <= float(grade_input) <= 3.00:
                        passed_courses.append(code)
                except ValueError:
                    pass

        # 2. Model Initialization
        model = cp_model.CpModel()
        
        # Pre-calculate Year Standing Requirements
        year_units = {}
        year_courses_list = {}
        earned_units_by_year = {1: 0, 2: 0, 3: 0, 4: 0}
        for code, info in courses.items():
            yl = info.get("year_level")
            units = info.get("units", 0)
            if yl:
                year_units[yl] = year_units.get(yl, 0) + units
                year_courses_list.setdefault(yl, []).append(code)
                if code in passed_courses: earned_units_by_year[yl] += units

        # Normalize/resolve helpers for robust prereq/co-req matching
        course_code_index = {_normalize_code(code): code for code in courses}
        prereq_alias_map = {_normalize_code(k): v for k, v in PREREQ_ALIASES.items()}
        year_standing_map = {_normalize_code(k): v for k, v in YEAR_STANDING_RULES.items()}

        def resolve_course(code: str):
            if not isinstance(code, str):
                return None
            return course_code_index.get(_normalize_code(code))

        def resolve_year_standing(code: str):
            if not isinstance(code, str):
                return None
            return year_standing_map.get(_normalize_code(code))

        # 3. Variable Initialization
        semester = {}
        for code in courses:
            if code in passed_courses:
                semester[code] = model.NewIntVar(0, 0, f"sem_{code}")
            else:
                semester[code] = model.NewIntVar(current_sem, u.MAX_SEMESTER, f"sem_{code}")

        # 4. Constraints
        # --- A. Offering Constraints (Modular Arithmetic) ---
        for c, info in courses.items():
            if c in passed_courses: continue
            orig = _first_semester(info) or 1
            cat = info.get("category", "Flexible")
            valid_sems = []
            for s in range(current_sem, u.MAX_SEMESTER + 1):
                sem_type, orig_type = s % 3, orig % 3 # 1:1st, 2:2nd, 0:Summer
                if cat == "Rigid" and sem_type == orig_type:
                    valid_sems.append(s)
                elif cat == "Flexible" and ((orig_type in [1,2] and sem_type in [1,2]) or (orig_type == 0 and sem_type == 0)):
                    valid_sems.append(s)
                elif cat == "Bottleneck" and (sem_type == orig_type or sem_type == 0):
                    valid_sems.append(s)
            if valid_sems:
                model.AddAllowedAssignments([semester[c]], [[s] for s in valid_sems])

        # --- B. Unit Load Constraints ---
        for s in range(current_sem, u.MAX_SEMESTER + 1):
            active_in_sem = []
            for c, info in courses.items():
                if c in passed_courses: continue
                b = model.NewBoolVar(f"{c}_in_sem_{s}")
                model.Add(semester[c] == s).OnlyEnforceIf(b)
                model.Add(semester[c] != s).OnlyEnforceIf(b.Not())
                active_in_sem.append(b * info.get("units", 0))
            model.Add(sum(active_in_sem) <= u.max_units_for_sem(s))

        # --- C. Prerequisite & Year Standing Constraints ---
        def add_year_standing(course, req_year, enforce_if=None):
            req_units = math.ceil(0.9 * year_units.get(req_year, 0))
            if req_units <= 0:
                return
            earned = earned_units_by_year.get(req_year, 0)
            if earned >= req_units:
                return
            before_vars = []
            for yc in year_courses_list.get(req_year, []):
                b = model.NewBoolVar(f"{yc}_before_{course}")
                model.Add(semester[yc] < semester[course]).OnlyEnforceIf(b)
                model.Add(semester[yc] >= semester[course]).OnlyEnforceIf(b.Not())
                before_vars.append(b * courses[yc].get("units", 0))
            if enforce_if:
                model.Add(sum(before_vars) >= req_units).OnlyEnforceIf(enforce_if)
            else:
                model.Add(sum(before_vars) >= req_units)

        for c, info in courses.items():
            if c in passed_courses: continue
            for prereq in _get_req_list(info, ("pre-requisites", "pre_requisites", "prerequisites", "pre-reqs")):
                if isinstance(prereq, str):
                    alias = prereq_alias_map.get(_normalize_code(prereq))
                    if alias is not None:
                        prereq = alias
                if isinstance(prereq, (list, tuple)):
                    choices = []
                    for alt in prereq:
                        if not alt:
                            continue
                        alt_course = resolve_course(alt)
                        alt_year = resolve_year_standing(alt)
                        if alt_course or alt_year:
                            b = model.NewBoolVar(f"{c}_pre_{alt}")
                            if alt_course:
                                model.Add(semester[c] > semester[alt_course]).OnlyEnforceIf(b)
                            else:
                                add_year_standing(c, alt_year, b)
                            choices.append(b)
                    if choices:
                        model.Add(sum(choices) >= 1)
                    else:
                        print(f"Warning: {c} has prereq alternatives but none match known courses/standing.")
                else:
                    prereq_course = resolve_course(prereq)
                    prereq_year = resolve_year_standing(prereq)
                    if prereq_course:
                        model.Add(semester[c] > semester[prereq_course])
                    elif prereq_year:
                        add_year_standing(c, prereq_year)
                    else:
                        print(f"Warning: {c} has unknown prereq '{prereq}'.")

        # --- D. Co-requisites ---
        for c, info in courses.items():
            if c in passed_courses: continue
            for coreq in _get_req_list(info, ("co-requisites", "co_requisites", "corequisites", "co-reqs")):
                coreq_course = resolve_course(coreq)
                if coreq_course:
                    model.Add(semester[c] >= semester[coreq_course])
                else:
                    print(f"Warning: {c} has unknown co-req '{coreq}'.")

        # 5. Objective: Prefer finishing by semester 12; use 13+ only when needed.
        # Minimize the latest semester used so the schedule ends as early as possible.
        max_sem_used = model.NewIntVar(current_sem, u.MAX_SEMESTER, "max_sem_used")
        for c in courses:
            if c not in passed_courses:
                model.Add(max_sem_used >= semester[c])
        # Secondary: among same max semester, prefer earlier retakes for failed courses
        if failed_courses:
            model.Minimize(max_sem_used * 1000 + sum(semester[c] for c in failed_courses))
        else:
            model.Minimize(max_sem_used)

        # 6. Solver
        solver = cp_model.CpSolver()
        status = solver.Solve(model)

        if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            lines = ["--- GENERATED PROGRAM OF STUDY ---"]
            for s in range(current_sem, u.MAX_SEMESTER + 1):
                sem_list = [c for c in courses if c not in passed_courses and solver.Value(semester[c]) == s]
                if sem_list:
                    total_u = sum(courses[c].get("units", 0) for c in sem_list)
                    label = _sem_label(s)
                    lines.append(f"{label} ({total_u} units)")
                    for c in sorted(sem_list):
                        title = courses[c].get("course_title", c)
                        units = courses[c].get("units", 0)
                        lines.append(f"  • {c} — {title} ({units} units)")
            print("\n" + "\n".join(lines))
        else:
            _log_infeasible(solver, status, current_sem, failed_courses, passed_courses, courses, u)

    if __name__ == "__main__":
        run_pos_generator()