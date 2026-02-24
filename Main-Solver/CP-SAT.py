import sys
from pathlib import Path

# Add project root to path so packages are found when run from Main-Solver
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import math
from ortools.sat.python import cp_model
from courses_and_year_standings.courses import PREREQ_ALIASES, YEAR_STANDING_RULES, courses
import courses_and_year_standings.units_and_year_standings as u
from helpers_and_optimizations.pos_helpers import (
    _first_semester,
    _sem_label,
    _normalize_code,
    _get_req_list,
    _log_infeasible,
)


# === High-level sub-functions (pure refactor, no logic change) ===

def gather_input_and_grades():
    """Prompt for current semester and past-course grades."""
    print("--- BatStateU POS Generator: Irregular Student Logic ---")

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
        input(
            "No courses from past semesters to grade (e.g. you're in semester 1). "
            "Press Enter to continue."
        )
    else:
        sem_nums = sorted(set(_first_semester(info) for _, info in past_sem_courses))
        print(f"Courses to grade: {len(past_sem_courses)} from semester(s) {sem_nums}\n")
        last_orig_sem = None
        for code, info in past_sem_courses:
            orig_sem = _first_semester(info)
            if last_orig_sem is None or orig_sem != last_orig_sem:
                print(f"--- Semester {orig_sem} ---")
                last_orig_sem = orig_sem
            grade_input = input(
                f"  Grade for {code} ({info.get('course_title', 'Course')}): "
            ).strip().upper()
            if not grade_input:
                continue
            try:
                if grade_input in ["INC", "5.0", "5.00"] or float(grade_input) == 5.00:
                    failed_courses.append(code)
                elif 1.00 <= float(grade_input) <= 3.00:
                    passed_courses.append(code)
            except ValueError:
                pass

    return current_sem, passed_courses, failed_courses


def check_probation(current_sem, passed_courses, failed_courses):
    """
    Check per-semester failure rate. Returns None if OK to generate POS;
    otherwise returns the message to display (no POS generated).
    - >75% failed in any past semester: cannot generate.
    - >50% failed in any past semester: proceed to Department Head.
    """
    over_75 = []   # list of (sem_index, label, failed_count, total, pct)
    over_50 = []
    for s in range(1, current_sem):
        in_sem = [c for c in courses if _first_semester(courses.get(c, {})) == s]
        if not in_sem:
            continue
        graded = [c for c in in_sem if c in passed_courses or c in failed_courses]
        if not graded:
            continue
        failed_in_sem = sum(1 for c in graded if c in failed_courses)
        total = len(graded)
        ratio = failed_in_sem / total
        pct = round(ratio * 100)
        label = _sem_label(s)
        entry = (s, label, failed_in_sem, total, pct)
        if ratio > 0.75:
            over_75.append(entry)
        elif ratio > 0.50:
            over_50.append(entry)
    if over_75:
        lines = [
            "--- Probation: Program of Study cannot be generated ---",
            "",
            "You have failed too many units in at least one past semester.",
            "University policy does not allow automatic POS generation in this case.",
            "",
            "Semester(s) with failure rate above 75%:",
        ]
        for _, label, failed, total, pct in over_75:
            lines.append(f"  • {label}: {pct}% failed ({failed} of {total} courses graded)")
        lines.extend([
            "",
            "Program of Study cannot be generated because you have failed too many units.",
            "Please contact your Department Head or academic advisor for manual evaluation.",
        ])
        return "\n".join(lines)
    if over_50:
        lines = [
            "--- Probation: Manual evaluation required ---",
            "",
            "You have a high failure rate in at least one past semester (above 50%).",
            "Automatic POS generation is not available; your case must be reviewed in person.",
            "",
            "Semester(s) with failure rate above 50%:",
        ]
        for _, label, failed, total, pct in over_50:
            lines.append(f"  • {label}: {pct}% failed ({failed} of {total} courses graded)")
        lines.extend([
            "",
            "Please proceed to the Department Head for Manual Evaluation.",
        ])
        return "\n".join(lines)
    return None


def build_year_aggregates(passed_courses):
    """Pre-calculate year_units, year_courses_list, earned_units_by_year."""
    year_units = {}
    year_courses_list = {}
    earned_units_by_year = {1: 0, 2: 0, 3: 0, 4: 0}
    for code, info in courses.items():
        yl = info.get("year_level")
        units = info.get("units", 0)
        if yl:
            year_units[yl] = year_units.get(yl, 0) + units
            year_courses_list.setdefault(yl, []).append(code)
            if code in passed_courses:
                earned_units_by_year[yl] += units

    return year_units, year_courses_list, earned_units_by_year


def build_normalized_maps():
    """Build maps used for prereq and year-standing resolution."""
    course_code_index = {_normalize_code(code): code for code in courses}
    prereq_alias_map = {_normalize_code(k): v for k, v in PREREQ_ALIASES.items()}
    year_standing_map = {_normalize_code(k): v for k, v in YEAR_STANDING_RULES.items()}

    return course_code_index, prereq_alias_map, year_standing_map


def create_semester_variables(model, current_sem, passed_courses):
    """Create semester variables for all courses."""
    semester = {}
    for code in courses:
        if code in passed_courses:
            semester[code] = model.NewIntVar(0, 0, f"sem_{code}")
        else:
            semester[code] = model.NewIntVar(current_sem, u.MAX_SEMESTER, f"sem_{code}")

    return semester


def add_offering_constraints(model, semester, passed_courses, current_sem):
    """Add offering-window constraints (Rigid/Flexible/Bottleneck)."""
    for c, info in courses.items():
        if c in passed_courses:
            continue
        orig = _first_semester(info) or 1
        cat = info.get("category", "Flexible")
        valid_sems = []
        for s in range(current_sem, u.MAX_SEMESTER + 1):
            sem_type, orig_type = s % 3, orig % 3  # 1:1st, 2:2nd, 0:Summer
            if cat == "Rigid" and sem_type == orig_type:
                valid_sems.append(s)
            elif cat == "Flexible" and (
                (orig_type in [1, 2] and sem_type in [1, 2])
                or (orig_type == 0 and sem_type == 0)
            ):
                valid_sems.append(s)
            elif cat == "Bottleneck" and (sem_type == orig_type or sem_type == 0 or sem_type == 2):
                valid_sems.append(s)
        if valid_sems:
            model.AddAllowedAssignments([semester[c]], [[s] for s in valid_sems])


def add_unit_constraints(model, semester, passed_courses, current_sem):
    """Add per-semester unit load constraints."""
    for s in range(current_sem, u.MAX_SEMESTER + 1):
        active_in_sem = []
        for c, info in courses.items():
            if c in passed_courses:
                continue
            b = model.NewBoolVar(f"{c}_in_sem_{s}")
            model.Add(semester[c] == s).OnlyEnforceIf(b)
            model.Add(semester[c] != s).OnlyEnforceIf(b.Not())
            active_in_sem.append(b * info.get("units", 0))
        model.Add(sum(active_in_sem) <= u.max_units_for_sem(s))


def add_prereq_year_coreq_constraints(
    model,
    semester,
    passed_courses,
    year_units,
    year_courses_list,
    earned_units_by_year,
    course_code_index,
    prereq_alias_map,
    year_standing_map,
):
    """Add prerequisite, year-standing, and co-requisite constraints."""

    def resolve_course(code: str):
        if not isinstance(code, str):
            return None
        return course_code_index.get(_normalize_code(code))

    def resolve_year_standing(code: str):
        if not isinstance(code, str):
            return None
        return year_standing_map.get(_normalize_code(code))

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

    # --- C. Prerequisite & Year Standing Constraints ---
    for c, info in courses.items():
        if c in passed_courses:
            continue
        for prereq in _get_req_list(
            info, ("pre-requisites", "pre_requisites", "prerequisites", "pre-reqs")
        ):
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
                    print(
                        f"Warning: {c} has prereq alternatives but none match known courses/standing."
                    )
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
        if c in passed_courses:
            continue
        for coreq in _get_req_list(
            info, ("co-requisites", "co_requisites", "corequisites", "co-reqs")
        ):
            coreq_course = resolve_course(coreq)
            if coreq_course:
                model.Add(semester[c] >= semester[coreq_course])
            else:
                print(f"Warning: {c} has unknown co-req '{coreq}'.")


def add_objective(model, semester, passed_courses, failed_courses, current_sem):
    """Set objective: finish as early as possible, fill from current semester first."""
    # 5. Objective: Minimize latest semester used, then prefer filling earlier semesters
    # so that the current semester is used when possible (avoids skipping to Summer/sem 3).
    max_sem_used = model.NewIntVar(current_sem, u.MAX_SEMESTER, "max_sem_used")
    for c in courses:
        if c not in passed_courses:
            model.Add(max_sem_used >= semester[c])
    # Scale so we first minimize max_sem_used, then minimize sum of semesters (pack current first)
    scale = u.MAX_SEMESTER * u.MAX_SEMESTER + 1
    model.Minimize(
        max_sem_used * scale
        + sum(semester[c] for c in courses if c not in passed_courses)
    )


def solve_and_print(model, semester, passed_courses, failed_courses, current_sem):
    """Solve model and print generated POS or infeasibility info."""
    # 6. Solver
    solver = cp_model.CpSolver()
    status = solver.Solve(model)

    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        lines = ["--- GENERATED PROGRAM OF STUDY ---"]
        for s in range(current_sem, u.MAX_SEMESTER + 1):
            sem_list = [
                c
                for c in courses
                if c not in passed_courses and solver.Value(semester[c]) == s
            ]
            label = _sem_label(s)
            if sem_list:
                total_u = sum(courses[c].get("units", 0) for c in sem_list)
                lines.append(f"{label} ({total_u} units)")
                for c in sorted(sem_list):
                    title = courses[c].get("course_title", c)
                    units = courses[c].get("units", 0)
                    lines.append(f"  • {c} — {title} ({units} units)")
            else:
                lines.append(f"{label} (0 units)")
        print("\n" + "\n".join(lines))
    else:
        _log_infeasible(
            solver, status, current_sem, failed_courses, passed_courses, courses, u
        )



def run_pos_generator():
    # 1. Input & Data Handling
    current_sem, passed_courses, failed_courses = gather_input_and_grades()

def run_pos_with_state(current_sem, passed_courses, failed_courses):
    """
    Core solver pipeline shared by static and dynamic modes.
    Expects current_sem, passed_courses, failed_courses to be precomputed.
    """

    # 1b. Probation / failed-units check (per semester)
    probation_msg = check_probation(current_sem, passed_courses, failed_courses)
    if probation_msg is not None:
        print(f"\n{probation_msg}")
        return

    # 2. Model Initialization
    model = cp_model.CpModel()
    year_units, year_courses_list, earned_units_by_year = build_year_aggregates(
        passed_courses
    )
    course_code_index, prereq_alias_map, year_standing_map = build_normalized_maps()

    # 3. Variable Initialization
    semester = create_semester_variables(model, current_sem, passed_courses)

    # 4. Constraints
    add_offering_constraints(model, semester, passed_courses, current_sem)
    add_unit_constraints(model, semester, passed_courses, current_sem)
    add_prereq_year_coreq_constraints(
        model,
        semester,
        passed_courses,
        year_units,
        year_courses_list,
        earned_units_by_year,
        course_code_index,
        prereq_alias_map,
        year_standing_map,
    )

    # 5. Objective
    add_objective(model, semester, passed_courses, failed_courses, current_sem)

    # 6. Solver + output
    solve_and_print(model, semester, passed_courses, failed_courses, current_sem)



def run_pos_generator():
    # 1. Input & Data Handling (static mode)
    current_sem, passed_courses, failed_courses = gather_input_and_grades()
    # Delegate to shared pipeline
    run_pos_with_state(current_sem, passed_courses, failed_courses)


if __name__ == "__main__":
    run_pos_generator()