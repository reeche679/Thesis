import sys
import importlib.util
from pathlib import Path

# Add project root to path so shared modules can be imported
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from courses_and_year_standings.courses import courses
import courses_and_year_standings.units_and_year_standings as u
from helpers_and_optimizations.pos_helpers import _first_semester, _sem_label, _normalize_code

# Map of normalized course code -> canonical course code from the curriculum.
# Normalization is case-insensitive and collapses whitespace, but the sequence
# of letters and numbers must match exactly some curriculum code.
COURSE_CODE_INDEX = {_normalize_code(code): code for code in courses}


def _grade_is_pass(grade_str: str) -> bool:
    """True if grade indicates pass (1.00–3.00). False for fail or unclear."""
    if not grade_str or not grade_str.strip():
        return False
    g = grade_str.strip().upper()
    if g == "INC":
        return False
    try:
        x = float(g)
        return 1.00 <= x <= 3.00
    except ValueError:
        return False


def _grade_is_fail(grade_str: str) -> bool:
    """True if grade indicates fail (5.00, INC, or > 5)."""
    if not grade_str or not grade_str.strip():
        return False
    g = grade_str.strip().upper()
    if g in ("5.0", "5.00", "INC"):
        return True
    try:
        x = float(g)
        return x > 5.0
    except ValueError:
        return False


def classify_attempts_to_passed_failed(
    attempts_by_sem: dict[int, list[tuple[str, str]]],
) -> tuple[list[str], list[str]]:
    """
    Phase 2: From attempts_by_sem, derive passed_courses and failed_courses.
    - Pass: grade in 1.00–3.00.
    - Fail: 5.00, INC, or > 5.
    - Repeats: if a course has multiple attempts, any PASS → passed; else → failed.
    """
    # Collect all attempts per course: code -> list of (sem, grade_str) in order of sem
    by_code: dict[str, list[tuple[int, str]]] = {}
    for s in sorted(attempts_by_sem.keys()):
        for code, grade_str in attempts_by_sem[s]:
            by_code.setdefault(code, []).append((s, grade_str))

    passed_courses: list[str] = []
    failed_courses: list[str] = []
    for code, attempts in by_code.items():
        any_pass = any(_grade_is_pass(g) for _, g in attempts)
        if any_pass:
            passed_courses.append(code)
        else:
            failed_courses.append(code)

    return passed_courses, failed_courses


def _run_static_model():
    """
    Dynamically import and run the main static CP-SAT solver (CP-SAT.py).
    Used when the dynamic mode scenario is effectively identical to static mode.
    """
    cp_sat_path = Path(__file__).with_name("CP-SAT.py")
    spec = importlib.util.spec_from_file_location("cp_sat_main", cp_sat_path)
    if spec is None or spec.loader is None:
        print("Unable to load CP-SAT.py for static solver delegation.")
        return
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if hasattr(module, "run_pos_generator"):
        module.run_pos_generator()
    else:
        print("CP-SAT.py does not define run_pos_generator; cannot delegate.")


def _run_dynamic_model(current_sem: int, passed_courses: list[str], failed_courses: list[str], with_petition: bool) -> None:
    """
    Import and run the shared solver pipeline using a precomputed state
    (current_sem, passed_courses, failed_courses) from dynamic mode.
    """
    cp_sat_path = Path(__file__).with_name("CP-SAT.py")
    spec = importlib.util.spec_from_file_location("cp_sat_main", cp_sat_path)
    if spec is None or spec.loader is None:
        print("Unable to load CP-SAT.py for dynamic solver delegation.")
        return
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if hasattr(module, "run_pos_with_state"):
        module.run_pos_with_state(current_sem, passed_courses, failed_courses, with_petition)
    else:
        print("CP-SAT.py does not define run_pos_with_state; cannot delegate dynamic mode.")


def gather_irregular_history():
    """
    Phase 1 (Dynamic mode, Option A):
    Terminal wizard for collecting an irregular POS history semester by semester.

    This function does NOT yet:
    - validate course codes against the curriculum
    - classify PASS/FAIL/INC

    It only gathers raw user input into a structured form:
      - current_sem (int)
      - irregular_start_sem (int)
      - attempts_by_sem: {sem_index: [(raw_code, raw_grade_str), ...]}

    Later phases will normalize and validate this structure and
    convert it into passed/failed sets for the solver.
    """
    print("--- BatStateU POS Generator: Dynamic / Irregular Mode ---")

    # Ask for current semester (the semester the student is STARTING)
    while True:
        try:
            raw = input(f"Enter current semester index (1-{u.MAX_SEMESTER}): ").strip()
            current_sem = int(raw)
            if 1 <= current_sem <= u.MAX_SEMESTER:
                break
        except ValueError:
            pass
        print(f"  Invalid input. Please enter an integer between 1 and {u.MAX_SEMESTER}.")

    # Ask when the student first became irregular
    print(
        "\nFrom which semester did you start deviating from the standard curriculum?\n"
        "(Example: if you became irregular in 2nd Year 1st Sem, use its global index.)"
    )
    while True:
        try:
            raw = input(f"Enter irregular-start semester index (1-{current_sem}): ").strip()
            irregular_start_sem = int(raw)
            if 1 <= irregular_start_sem <= current_sem:
                break
        except ValueError:
            pass
        print(f"  Invalid input. Please enter an integer between 1 and {current_sem}.")

    # Special case: current_sem=2 and irregular_start_sem=1
    # This is effectively the same as the static solver behavior:
    # only semester 1 is in the past and follows the base curriculum.
    # In this case, delegate directly to the main static solver and exit.
    if current_sem == 2 and irregular_start_sem == 1:
        print(
            "\nNote: You are starting Semester 2 and became irregular in Semester 1.\n"
            "This is equivalent to the static mode case where only Semester 1 is\n"
            "completed. The system will therefore just collect grades for all\n"
            "1st Year 1st Sem curriculum courses using the main static solver.\n"
        )
        _run_static_model()
        return current_sem, irregular_start_sem, {}, [], []

    # Special case: current_sem == irregular_start_sem
    # All completed semesters are before the irregular semester, so we only need
    # curriculum-based grades for 1..current_sem-1 — same as static mode.
    if current_sem == irregular_start_sem:
        print(
            "\nNote: Your current semester is the same as when you became irregular.\n"
            "All completed semesters follow the base curriculum. The system will\n"
            "use the main static solver to collect grades and generate your POS.\n"
        )
        _run_static_model()
        return current_sem, irregular_start_sem, {}, [], []

    attempts_by_sem: dict[int, list[tuple[str, str]]] = {}

    # --- Part 1: Semesters BEFORE irregular_start_sem (follow curriculum structure) ---
    if irregular_start_sem > 1:
        print(
            "\nFirst, we will record grades for semesters BEFORE you became irregular,\n"
            "following the base curriculum's list of courses for those semesters.\n"
        )
        for s in range(1, irregular_start_sem):
            label = _sem_label(s)
            sem_courses = [
                (code, info)
                for code, info in courses.items()
                if _first_semester(info) == s
            ]
            if not sem_courses:
                continue

            print(f"\n=== {label} (curriculum-based, global index {s}) ===")
            for code, info in sorted(sem_courses, key=lambda x: x[0]):
                title = info.get("course_title", "Course")
                grade_input = input(
                    f"  Grade for {code} ({title}) "
                    "(1.00-3.00: Pass | 5.00/INC: Fail | Enter: Not Taken): "
                ).strip().upper()
                if not grade_input:
                    continue
                attempts_by_sem.setdefault(s, []).append((code, grade_input))

    # --- Part 2: Semesters FROM irregular_start_sem up to last completed (dynamic list) ---
    if irregular_start_sem < current_sem:
        print(
            "\nNow, for each semester FROM the one where you became irregular\n"
            "up to the last COMPLETED semester (one before your current semester),\n"
            "enter the courses you actually took and their outcomes.\n"
        )
        for s in range(irregular_start_sem, current_sem):
            label = _sem_label(s)
            print(f"\n=== {label} (global index {s}) ===")
            raw_codes = input(
                "Enter course codes taken this semester (comma-separated), "
                "or press Enter if none: "
            ).strip()
            if not raw_codes:
                continue

            codes = [c.strip() for c in raw_codes.split(",") if c.strip()]
            for raw_code in codes:
                norm = _normalize_code(raw_code)
                canonical = COURSE_CODE_INDEX.get(norm)
                if canonical is None:
                    print(
                        f"  ! Skipping unknown course code '{raw_code}'. "
                        "Please ensure it matches a curriculum code (case-insensitive, letters/numbers exact)."
                    )
                    continue
                grade_str = input(
                    f"  Outcome/grade for {canonical} "
                    "(e.g., 1.75, 3.00, 5.00, INC; leave blank if unsure): "
                ).strip().upper()
                attempts_by_sem.setdefault(s, []).append((canonical, grade_str))
    else:
        print(
            "\nNote: Irregular-start semester is the same as your current semester.\n"
            "There are no COMPLETED irregular semesters to encode; only the\n"
            "curriculum-based history before this point applies.\n"
            "Please proceed to static POS solver."
        )

    print("\n--- Summary of entered irregular history (normalized codes) ---")
    for s in sorted(attempts_by_sem.keys()):
        label = _sem_label(s)
        print(f"{label} (index {s}):")
        for code, grade in attempts_by_sem[s]:
            if grade:
                print(f"  • {code} — raw grade: {grade}")
            else:
                print(f"  • {code} — raw grade: (not specified)")

    # Phase 2: classify grades -> passed_courses, failed_courses
    passed_courses, failed_courses = classify_attempts_to_passed_failed(attempts_by_sem)
    print("\n--- Classification (for solver) ---")
    print(f"  Passed: {len(passed_courses)} courses")
    print(f"  Failed (to reschedule): {len(failed_courses)} courses")

    return current_sem, irregular_start_sem, attempts_by_sem, passed_courses, failed_courses


if __name__ == "__main__":
    # Dynamic-mode entry point: gather history, classify, then run solver.
    (
        current_sem,
        irregular_start_sem,
        attempts_by_sem,
        passed_courses,
        failed_courses,
    ) = gather_irregular_history()

    # If we delegated to static mode, there is nothing more to do here.
    if not attempts_by_sem and not passed_courses and not failed_courses:
        sys.exit(0)

    use_petition_input = input("Use petition mode for bottleneck subjects? (Y/N): ").strip().upper()
    with_petition = use_petition_input == "Y"

    _run_dynamic_model(current_sem, passed_courses, failed_courses, with_petition)

