"""
Helper functions for the CP-SAT Program of Study generator.
Separated from the main solver flow for clarity and reuse.
"""
from ortools.sat.python import cp_model
from courses_and_year_standings.units_and_year_standings import year_and_term_from_sem 


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
    year_level, term_in_year = year_and_term_from_sem(sem)
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
