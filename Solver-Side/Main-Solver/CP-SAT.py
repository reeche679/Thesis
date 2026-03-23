import sys
import os
import subprocess
import importlib.util
from pathlib import Path

# Add import roots so modules are found both when run directly and via runpy.
_main_solver_dir = Path(__file__).resolve().parent
_root = _main_solver_dir.parent
if str(_main_solver_dir) not in sys.path:
    sys.path.insert(0, str(_main_solver_dir))
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import bisect
import math
import random
import json
import hashlib
import time
from ortools.sat.python import cp_model
from courses_and_year_standings.courses import PREREQ_ALIASES, YEAR_STANDING_RULES, courses
import courses_and_year_standings.units_and_year_standings as u
from algorithms_and_helpers.pos_helpers import (
    _first_semester,
    _sem_label,
    _normalize_code,
    _get_req_list,
    _log_infeasible,
)
from algorithms_and_helpers.Configurations import (
    CONFIGURATIONS,
    DEFAULT_CONFIGURATION,
)
try:
    from solver_settings import (
        CONFIGURATION,
        ALTERNATIVE_ALGORITHM,
        ENFORCE_GRADUATION_TRIO_ONLY_SEMESTER,
    )
except ModuleNotFoundError:
    _settings_path = Path(__file__).resolve().with_name("solver_settings.py")
    _settings_spec = importlib.util.spec_from_file_location("solver_settings", _settings_path)
    if _settings_spec is None or _settings_spec.loader is None:
        raise
    _settings_module = importlib.util.module_from_spec(_settings_spec)
    _settings_spec.loader.exec_module(_settings_module)
    CONFIGURATION = _settings_module.CONFIGURATION
    ALTERNATIVE_ALGORITHM = _settings_module.ALTERNATIVE_ALGORITHM
    ENFORCE_GRADUATION_TRIO_ONLY_SEMESTER = _settings_module.ENFORCE_GRADUATION_TRIO_ONLY_SEMESTER
from pos_terminal_display import export_generated_pos_pdf
from algorithms_and_helpers.alternative_algorithms import (
    run_firefly_search,
    run_fuzzy_logic_search,
    run_local_search,
    run_tabu_search,
)


_DEBUG_LOG_PATH = Path(__file__).resolve().parents[2] / "debug-7593d3.log"
_AB886B_LOG_PATH = Path(__file__).resolve().parents[2] / "debug-ab886b.log"


def _ab886b_log(hypothesis_id: str, location: str, message: str, data: dict, run_id: str = "run1"):
    # #region agent log
    try:
        with _AB886B_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "sessionId": "ab886b",
                        "runId": run_id,
                        "hypothesisId": hypothesis_id,
                        "location": location,
                        "message": message,
                        "data": data,
                        "timestamp": int(__import__("time").time() * 1000),
                    },
                    ensure_ascii=True,
                )
                + "\n"
            )
    except Exception:
        pass
    # #endregion


def _assignment_fingerprint(state: dict, remaining: list) -> str:
    payload = json.dumps(
        {str(c): int(state.get(c, -1)) for c in sorted(remaining, key=str)},
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _debug_log(hypothesis_id: str, location: str, message: str, data: dict, run_id: str = "run1"):
    # #region agent log
    try:
        with _DEBUG_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "sessionId": "7593d3",
                        "runId": run_id,
                        "hypothesisId": hypothesis_id,
                        "location": location,
                        "message": message,
                        "data": data,
                        "timestamp": int(__import__("time").time() * 1000),
                    },
                    ensure_ascii=True,
                )
                + "\n"
            )
    except Exception:
        pass
    # #endregion


def _open_file_with_default_app(file_path: Path):
    """Open a file using the OS default application."""
    try:
        if os.name == "nt":
            os.startfile(str(file_path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(file_path)])
        else:
            subprocess.Popen(["xdg-open", str(file_path)])
    except Exception as e:
        print(f"Could not auto-open PDF: {e}")


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
        # Treat exactly-75% cases as the 75% bucket too.
        if ratio >= 0.75:
            over_75.append(entry)
        elif ratio >= 0.50:
            over_50.append(entry)
    if over_75:
        lines = [
            "Probation: POS cannot be generated.",
            "Failure rate is 75% or higher in:",
        ]
        for _, label, failed, total, pct in over_75:
            lines.append(f"  • {label}: {pct}% failed ({failed} of {total} courses graded)")
        lines.append("Please proceed to the Department Head for manual evaluation.")
        return "\n".join(lines)
    if over_50:
        lines = [
            "Probation: Manual evaluation required.",
            "Failure rate is 50% or higher in:",
        ]
        for _, label, failed, total, pct in over_50:
            lines.append(f"  • {label}: {pct}% failed ({failed} of {total} courses graded)")
        lines.append("Please proceed to the Department Head for manual evaluation.")
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


# Single configuration selection is centralized in solver_settings.py.
# CP-SAT configuration keys come from algorithms_and_helpers.Configurations.CONFIGURATIONS.

ALTERNATIVE_ALGORITHMS = {
    "local_search": run_local_search,
    "fuzzy_logic": run_fuzzy_logic_search,
    "tabu_search": run_tabu_search,
    "firefly": run_firefly_search,
}

# Distinct streams for rng.choice() inside neighbor generation so greedy algorithms
# that share score_fn (local vs tabu) do not replay identical neighbor lists.
_ALT_NEIGHBOR_RNG_SALT = {
    "local_search": 0,
    "tabu_search": 10_007,
    "fuzzy_logic": 20_011,
    "firefly": 30_017,
}



def add_offering_constraints(model, semester, passed_courses, current_sem, with_petition: bool):
    """Add offering-window constraints using the selected configuration."""
    spec = CONFIGURATIONS[CONFIGURATION]
    spec.offering_fn(
        model,
        semester,
        courses,
        passed_courses,
        current_sem,
        with_petition,
        _first_semester,
        u.MAX_SEMESTER,
    )


# Graduation semester: only these three courses may appear in the final semester.
GRADUATION_TRIO = ("CpE 405", "CpE 406", "ENGG 117")

# Graduation trio enforcement toggle is centralized in solver_settings.py.


def add_graduation_semester_constraint(model, semester, passed_courses):
    """
    Enforce that the last semester contains ONLY CpE Practice 2, Compre, and OJT.
    - The three must be in the same semester.
    - No other course may be in that semester.
    """
    trio = [c for c in GRADUATION_TRIO if c in courses and c not in passed_courses]
    if len(trio) < 2:
        return  # Not enough to enforce (e.g. all passed or only one left)
    ref = trio[0]
    # All trio members in same semester
    for c in trio[1:]:
        model.Add(semester[c] == semester[ref])
    # No other course may share that semester
    for c, info in courses.items():
        if c in passed_courses or c in GRADUATION_TRIO:
            continue
        model.Add(semester[c] != semester[ref])


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

    def add_year_standing(course, _req_year_ignored, enforce_if=None):
        """
        Enforce year standing as: by the time `course` is taken, the student must
        have at least 90% of the units from ALL curriculum courses whose original
        semester comes strictly BEFORE this course's original semester.

        This is a semester-prefix rule, not tied to a specific year bucket.
        """
        info_c = courses.get(course)
        orig_sem_c = _first_semester(info_c)
        if orig_sem_c is None:
            return

        # All curriculum courses that appear before this course in the POS template.
        prefix_courses = []
        total_units = 0
        earned_fixed = 0
        for code, info in courses.items():
            orig_sem = _first_semester(info)
            if orig_sem is None or orig_sem >= orig_sem_c:
                continue
            units = info.get("units", 0)
            prefix_courses.append((code, units))
            total_units += units
            if code in passed_courses:
                earned_fixed += units

        if total_units <= 0:
            return

        req_units = math.ceil(0.9 * total_units)
        # Units still needed from scheduled courses (beyond already-passed ones).
        remaining_needed = req_units - earned_fixed

        if remaining_needed <= 0:
            # Standing already satisfied from passed courses alone.
            return

        before_vars = []
        for code, units in prefix_courses:
            if code in passed_courses:
                # Already counted in earned_fixed at sem 0.
                continue
            b = model.NewBoolVar(f"{code}_before_{course}")
            model.Add(semester[code] < semester[course]).OnlyEnforceIf(b)
            model.Add(semester[code] >= semester[course]).OnlyEnforceIf(b.Not())
            before_vars.append(b * units)

        if not before_vars:
            # No remaining courses can contribute extra units; leave as-is.
            return

        if enforce_if:
            model.Add(sum(before_vars) >= remaining_needed).OnlyEnforceIf(enforce_if)
        else:
            model.Add(sum(before_vars) >= remaining_needed)

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
    """Set objective using the selected configuration."""
    spec = CONFIGURATIONS[CONFIGURATION]
    spec.objective_fn(
        model,
        semester,
        courses,
        passed_courses,
        current_sem,
        u.MAX_SEMESTER,
        weights=spec.weights,
    )


def _solve_and_collect(model, semester, passed_courses, failed_courses, current_sem):
    """
    Internal helper: solve model and return a structured POS result.

    Returns a dict:
      - status: "ok" | "infeasible"
      - message: optional string (mainly for infeasible cases)
      - pos: list of per-semester dicts when status == "ok"
    """
    solver = cp_model.CpSolver()
    status = solver.Solve(model)

    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        pos = []
        for s in range(current_sem, u.MAX_SEMESTER + 1):
            sem_list = [
                c
                for c in courses
                if c not in passed_courses and solver.Value(semester[c]) == s
            ]
            label = _sem_label(s)
            total_u = sum(courses[c].get("units", 0) for c in sem_list) if sem_list else 0
            pos.append(
                {
                    "semester_index": s,
                    "label": label,
                    "total_units": total_u,
                    "courses": [
                        {
                            "code": c,
                            "title": courses[c].get("course_title", c),
                            "units": courses[c].get("units", 0),
                        }
                        for c in sorted(sem_list)
                    ],
                }
            )
        return {"status": "ok", "message": None, "pos": pos}
    else:
        import io
        import contextlib

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _log_infeasible(
                solver, status, current_sem, failed_courses, passed_courses, courses, u
            )
        return {"status": "infeasible", "message": buf.getvalue().strip(), "pos": []}


def _apply_forced_semester_assignments(model, semester, passed_courses, forced_semester_map):
    """
    If provided, force specific courses to be scheduled in specific semesters.

    forced_semester_map: { "<course_code>": <semester_index_int> }
    """
    if not forced_semester_map or not isinstance(forced_semester_map, dict):
        return

    for code, forced_sem in forced_semester_map.items():
        if not isinstance(code, str):
            continue
        if code in passed_courses:
            continue
        if code not in semester:
            continue
        try:
            si = int(forced_sem)
        except (ValueError, TypeError):
            continue
        model.Add(semester[code] == si)


def _valid_semesters_for_course(code, info, current_sem, with_petition: bool):
    """Compute allowed semesters for a course under the active configuration."""
    max_sem = u.MAX_SEMESTER
    if CONFIGURATION == "config3_weightedtriocap":
        max_sem = min(max_sem, 14)

    graduation_trio = {"ENGG 117", "CpE 405", "CpE 406"}
    if CONFIGURATION == "config3_weightedtriocap" and code in graduation_trio:
        return [max_sem]

    if CONFIGURATION == "config0_no_modulo3_baseline":
        orig = _first_semester(info) or 1
        cat = info.get("category", "Rigid")
        lo = max(current_sem, orig)
        strict = [s for s in range(current_sem, max_sem + 1) if s >= lo]
        if cat == "Bottleneck" and with_petition:
            return list(range(current_sem, max_sem + 1))
        if cat == "Flexible" and with_petition:
            return list(range(current_sem, max_sem + 1))
        return strict

    orig = _first_semester(info) or 1
    cat = info.get("category", "Rigid")

    valid = []
    for s in range(current_sem, max_sem + 1):
        sem_type, orig_type = s % 3, orig % 3
        if cat == "Rigid":
            if sem_type == orig_type:
                valid.append(s)
        elif cat == "Flexible":
            if (orig_type in [1, 2] and sem_type in [1, 2]) or (orig_type == 0 and sem_type == 0):
                valid.append(s)
        elif cat == "Bottleneck":
            if CONFIGURATION == "config3_weightedtriocap":
                valid.append(s)
            elif with_petition or sem_type == orig_type:
                valid.append(s)
        else:
            valid.append(s)
    return valid


def _build_pos_from_state(state, current_sem, passed_courses):
    pos = []
    for s in range(current_sem, u.MAX_SEMESTER + 1):
        sem_list = [
            c for c in courses
            if c not in passed_courses and state.get(c) == s
        ]
        label = _sem_label(s)
        total_u = sum(courses[c].get("units", 0) for c in sem_list) if sem_list else 0
        pos.append(
            {
                "semester_index": s,
                "label": label,
                "total_units": total_u,
                "courses": [
                    {
                        "code": c,
                        "title": courses[c].get("course_title", c),
                        "units": courses[c].get("units", 0),
                    }
                    for c in sorted(sem_list)
                ],
            }
        )
    return pos


def _state_violation_summary(state, current_sem, passed_courses, with_petition: bool):
    """
    Fast in-Python constraint check/penalty model for alternative algorithms.
    Returns dict with violation counts and summary features.
    """
    violation_count = 0
    prereq_violations = 0
    load_by_sem = {s: 0 for s in range(current_sem, u.MAX_SEMESTER + 1)}

    # 1) Domain/offering checks and per-semester units
    for c, info in courses.items():
        if c in passed_courses:
            continue
        s = state.get(c)
        if s is None:
            violation_count += 1
            continue
        valid = _valid_semesters_for_course(c, info, current_sem, with_petition)
        if s not in valid:
            violation_count += 1
        if s in load_by_sem:
            load_by_sem[s] += info.get("units", 0)

    for s, units in load_by_sem.items():
        max_u = u.max_units_for_sem(s)
        if units > max_u:
            violation_count += (units - max_u)

    # 2) Graduation trio-only semester rule
    if ENFORCE_GRADUATION_TRIO_ONLY_SEMESTER:
        trio = [c for c in GRADUATION_TRIO if c in courses and c not in passed_courses]
        if len(trio) >= 2:
            ref_s = state.get(trio[0])
            if ref_s is None:
                violation_count += 1
            else:
                for c in trio[1:]:
                    if state.get(c) != ref_s:
                        violation_count += 1
                for c in courses:
                    if c in passed_courses or c in GRADUATION_TRIO:
                        continue
                    if state.get(c) == ref_s:
                        violation_count += 1

    # 3) Prerequisite/co-requisite checks (year-standing approximated via ordering)
    course_code_index = {_normalize_code(code): code for code in courses}
    prereq_alias_map = {_normalize_code(k): v for k, v in PREREQ_ALIASES.items()}

    def resolve_course(code: str):
        if not isinstance(code, str):
            return None
        return course_code_index.get(_normalize_code(code))

    for c, info in courses.items():
        if c in passed_courses:
            continue
        sem_c = state.get(c, current_sem)

        # prerequisites
        for prereq in _get_req_list(
            info, ("pre-requisites", "pre_requisites", "prerequisites", "pre-reqs")
        ):
            if isinstance(prereq, str):
                alias = prereq_alias_map.get(_normalize_code(prereq))
                if alias is not None:
                    prereq = alias

            satisfied = False
            if isinstance(prereq, (list, tuple)):
                for alt in prereq:
                    alt_course = resolve_course(alt)
                    if alt_course and (alt_course in passed_courses or state.get(alt_course, 10**9) < sem_c):
                        satisfied = True
                        break
            else:
                pre_course = resolve_course(prereq)
                if pre_course:
                    satisfied = pre_course in passed_courses or state.get(pre_course, 10**9) < sem_c

            if not satisfied:
                violation_count += 1
                prereq_violations += 1

        # co-requisites
        for coreq in _get_req_list(
            info, ("co-requisites", "co_requisites", "corequisites", "co-reqs")
        ):
            co_course = resolve_course(coreq)
            if co_course and co_course not in passed_courses:
                if state.get(co_course, -1) > sem_c:
                    violation_count += 1

    remaining = [c for c in courses if c not in passed_courses]
    if remaining:
        max_sem = max(state.get(c, current_sem) for c in remaining)
        sum_sem = sum(state.get(c, current_sem) for c in remaining)
    else:
        max_sem = current_sem
        sum_sem = 0

    used_loads = [v for v in load_by_sem.values() if v > 0]
    if len(used_loads) >= 2:
        avg = sum(used_loads) / len(used_loads)
        spread = (sum((x - avg) ** 2 for x in used_loads) / len(used_loads)) ** 0.5
        load_balance = spread / max(1.0, avg)
    else:
        load_balance = 0.0

    return {
        "violations": float(violation_count),
        "prereq_violations": float(prereq_violations),
        "max_sem": float(max_sem),
        "sum_sem": float(sum_sem),
        "load_balance": float(load_balance),
    }


def _run_alternative_core(current_sem, passed_courses, failed_courses, with_petition: bool):
    """
    Run one selected alternative algorithm end-to-end and return POS payload.

    Course semester domains (modulo-3 / term-type offering, petition handling,
    config3 cap & trio rules) come from _valid_semesters_for_course, which is
    kept consistent with the active CONFIGURATION's offering_fn in
    Configurations.py — not a separate offering rule.
    """
    _debug_log(
        "H1",
        "CP-SAT.py:_run_alternative_core:start",
        "alternative_core_enter",
        {
            "algorithm": ALTERNATIVE_ALGORITHM,
            "configuration": CONFIGURATION,
            "current_sem": current_sem,
            "remaining_count": len([c for c in courses if c not in passed_courses]),
        },
    )
    if ALTERNATIVE_ALGORITHM not in ALTERNATIVE_ALGORITHMS:
        return {
            "status": "invalid_alternative_algorithm",
            "message": f"Unknown alternative algorithm '{ALTERNATIVE_ALGORITHM}'.",
            "pos": [],
        }

    remaining = [c for c in courses if c not in passed_courses]
    if not remaining:
        return {"status": "ok", "message": None, "pos": _build_pos_from_state({}, current_sem, passed_courses)}

    # Per-course allowed semesters from CONFIGURATION (modulo-3 / config0 / config3, categories,
    # petition). Heuristic search never leaves these domains — same offering reduction as CP-SAT.
    valid_map = {
        c: tuple(_valid_semesters_for_course(c, courses[c], current_sem, with_petition))
        for c in remaining
    }

    def random_restart_state(rst_rng: random.Random):
        st: dict = {}
        for c in remaining:
            ch = valid_map[c] if valid_map[c] else (current_sem,)
            st[c] = rst_rng.choice(ch)
        return st

    # Seed with earliest feasible-by-window semester; perturb lightly for exploration.
    rng_init = random.Random(42)
    initial_state = {}
    for c in remaining:
        choices = valid_map[c] if valid_map[c] else (current_sem,)
        initial_state[c] = choices[0]
        if len(choices) > 1 and rng_init.random() < 0.25:
            initial_state[c] = rng_init.choice(choices[: min(3, len(choices))])

    initial_v = _state_violation_summary(
        initial_state, current_sem, passed_courses, with_petition
    )
    _debug_log(
        "H2",
        "CP-SAT.py:_run_alternative_core:initial_state",
        "initial_state_violations",
        {
            "algorithm": ALTERNATIVE_ALGORITHM,
            "violations": int(initial_v.get("violations", 0)),
            "max_sem": float(initial_v.get("max_sem", 0)),
            "sum_sem": float(initial_v.get("sum_sem", 0)),
        },
    )
    initial_fp = _assignment_fingerprint(initial_state, remaining)
    # #region agent log
    _ab886b_log(
        "H2",
        "CP-SAT.py:_run_alternative_core:initial_fp",
        "alternative_initial_fingerprint",
        {
            "algorithm": ALTERNATIVE_ALGORITHM,
            "initial_fp": initial_fp,
            "initial_violations": int(initial_v.get("violations", 0)),
        },
    )
    # #endregion

    algo = ALTERNATIVE_ALGORITHM
    neighbor_rng = random.Random(42 + _ALT_NEIGHBOR_RNG_SALT[algo])

    def neighbor_fn(state):
        neighbors = []
        for c in remaining:
            choices = valid_map.get(c) or (state[c],)
            if not choices:
                continue
            sc = state[c]
            idx = bisect.bisect_left(choices, sc)
            if idx >= len(choices) or choices[idx] != sc:
                idx = 0
            for nxt_idx in {max(0, idx - 1), min(len(choices) - 1, idx + 1)}:
                if nxt_idx == idx:
                    continue
                nxt = dict(state)
                nxt[c] = choices[nxt_idx]
                neighbors.append(nxt)
            if len(choices) > 2:
                nxt = dict(state)
                nxt[c] = neighbor_rng.choice(choices)
                neighbors.append(nxt)
        return neighbors

    def feasible_fn(state):
        return _state_violation_summary(state, current_sem, passed_courses, with_petition)["violations"] == 0

    def score_fn(state):
        v = _state_violation_summary(state, current_sem, passed_courses, with_petition)
        # Keep hard-constraint penalties dominant.
        return (10_000.0 * v["violations"]) + (100.0 * v["max_sem"]) + v["sum_sem"]

    def fuzzy_feature_fn(state):
        v = _state_violation_summary(state, current_sem, passed_courses, with_petition)
        denom = max(1, len(remaining))
        return {
            "load_balance": v["load_balance"],
            "prerequisite_pressure": v["prereq_violations"] / denom,
            "delay_risk": (v["max_sem"] - current_sem) / max(1, (u.MAX_SEMESTER - current_sem + 1)),
        }

    def move_id_fn(cur, nxt):
        for c in remaining:
            if cur.get(c) != nxt.get(c):
                return f"{c}:{cur.get(c)}->{nxt.get(c)}"
        return "noop"

    if algo == "local_search":
        best_state, _ = run_local_search(
            initial_state,
            score_fn,
            neighbor_fn,
            feasible_fn,
            random_restart_fn=random_restart_state,
        )
    elif algo == "fuzzy_logic":
        best_state, _ = run_fuzzy_logic_search(
            initial_state, neighbor_fn, fuzzy_feature_fn, feasible_fn
        )
    elif algo == "firefly":
        best_state, _ = run_firefly_search(
            initial_state,
            score_fn,
            feasible_fn,
            remaining,
            valid_map,
            random_restart_fn=random_restart_state,
            neighbor_fn=neighbor_fn,
        )
    else:  # tabu_search
        best_state, _ = run_tabu_search(
            initial_state, score_fn, neighbor_fn, feasible_fn, move_id_fn
        )

    violations = _state_violation_summary(
        best_state, current_sem, passed_courses, with_petition
    )
    changed_assignments = sum(
        1 for c in remaining if initial_state.get(c) != best_state.get(c)
    )
    final_fp = _assignment_fingerprint(best_state, remaining)
    # #region agent log
    _ab886b_log(
        "H3",
        "CP-SAT.py:_run_alternative_core:result_fp",
        "alternative_result_fingerprint",
        {
            "algorithm": algo,
            "neighbor_rng_salt": _ALT_NEIGHBOR_RNG_SALT.get(algo, 0),
            "initial_fp": initial_fp,
            "final_fp": final_fp,
            "initial_eq_final": initial_fp == final_fp,
            "changed_assignments": int(changed_assignments),
            "final_violations": int(violations.get("violations", 0)),
        },
    )
    # #endregion
    _debug_log(
        "H3",
        "CP-SAT.py:_run_alternative_core:result_state",
        "alternative_result_summary",
        {
            "algorithm": algo,
            "changed_assignments": int(changed_assignments),
            "final_violations": int(violations.get("violations", 0)),
            "final_max_sem": float(violations.get("max_sem", 0)),
            "final_sum_sem": float(violations.get("sum_sem", 0)),
        },
    )

    return {
        "status": "ok",
        "message": (
            f"Generated using alternative algorithm '{algo}'. "
            f"Estimated violations: {int(violations.get('violations', 0))}."
        ),
        "pos": _build_pos_from_state(best_state, current_sem, passed_courses),
    }


def _run_cp_sat_configuration_core(
    current_sem,
    passed_courses,
    failed_courses,
    with_petition: bool,
    forced_semester_map=None,
):
    """Run the existing CP-SAT pipeline for the selected CONFIGURATION."""
    # 2. Model Initialization
    model = cp_model.CpModel()
    year_units, year_courses_list, earned_units_by_year = build_year_aggregates(
        passed_courses
    )
    course_code_index, prereq_alias_map, year_standing_map = build_normalized_maps()

    # 3. Variable Initialization
    semester = create_semester_variables(model, current_sem, passed_courses)

    # Optional: force semester assignments from a user-edited POS.
    _apply_forced_semester_assignments(model, semester, passed_courses, forced_semester_map)

    # 4. Constraints
    add_offering_constraints(model, semester, passed_courses, current_sem, with_petition)
    add_unit_constraints(model, semester, passed_courses, current_sem)
    if ENFORCE_GRADUATION_TRIO_ONLY_SEMESTER:
        add_graduation_semester_constraint(model, semester, passed_courses)
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

    # 6. Solver
    return _solve_and_collect(model, semester, passed_courses, failed_courses, current_sem)


def _run_core(current_sem, passed_courses, failed_courses, with_petition: bool, forced_semester_map=None):
    """
    Internal pipeline that runs the full model and returns a structured result dict.
    Shared by CLI and external callers.
    """
    if CONFIGURATION not in CONFIGURATIONS:
        return {
            "status": "invalid_configuration",
            "message": f"Unknown configuration '{CONFIGURATION}'.",
            "pos": [],
        }

    # 1b. Probation / failed-units check (per semester)
    probation_msg = check_probation(current_sem, passed_courses, failed_courses)
    if probation_msg is not None:
        return {"status": "probation", "message": probation_msg, "pos": []}

    # #region agent log
    _ab886b_log(
        "H1",
        "CP-SAT.py:_run_core:branch",
        "solver_branch_selection",
        {
            "ALTERNATIVE_ALGORITHM": ALTERNATIVE_ALGORITHM,
            "branch": "alternative" if ALTERNATIVE_ALGORITHM is not None else "cp_sat",
            "CONFIGURATION": CONFIGURATION,
        },
    )
    # #endregion

    if ALTERNATIVE_ALGORITHM is not None:
        return _run_alternative_core(current_sem, passed_courses, failed_courses, with_petition)

    return _run_cp_sat_configuration_core(
        current_sem,
        passed_courses,
        failed_courses,
        with_petition=with_petition,
        forced_semester_map=forced_semester_map,
    )


def generate_pos(payload: dict) -> dict:
    """
    Pure API entry point for external callers (e.g. Django).

    Expected payload keys:
      - current_sem (int)
      - passed_courses (list of course codes)
      - failed_courses (list of course codes)
      - with_petition (bool, optional)
      - forced_semester_map (dict, optional) forcing course->semester
      - configuration (str, optional) CP-SAT configuration key
      - alternative_algorithm (str | null, optional): local_search | fuzzy_logic | tabu_search
      - enforce_graduation_trio_only_semester (bool, optional)

    Alternative algorithms use the same CONFIGURATION offering windows as CP-SAT
    (see _valid_semesters_for_course); returned POS is always the heuristic's own result.
    """
    if payload is None:
        raise ValueError("payload must be a dict")
    global CONFIGURATION, ALTERNATIVE_ALGORITHM, ENFORCE_GRADUATION_TRIO_ONLY_SEMESTER

    try:
        current_sem = int(payload.get("current_sem"))
    except (TypeError, ValueError):
        raise ValueError("payload['current_sem'] must be an integer")

    passed_courses = list(payload.get("passed_courses") or [])
    failed_courses = list(payload.get("failed_courses") or [])
    with_petition = bool(payload.get("with_petition", False))
    forced_semester_map = payload.get("forced_semester_map") or None
    requested_configuration = payload.get("configuration", CONFIGURATION)
    requested_alternative = payload.get("alternative_algorithm", ALTERNATIVE_ALGORITHM)
    requested_trio_enforcement = bool(
        payload.get(
            "enforce_graduation_trio_only_semester",
            ENFORCE_GRADUATION_TRIO_ONLY_SEMESTER,
        )
    )

    if requested_configuration not in CONFIGURATIONS:
        raise ValueError(
            f"payload['configuration'] must be one of: {', '.join(sorted(CONFIGURATIONS.keys()))}"
        )

    if requested_alternative is not None and requested_alternative not in ALTERNATIVE_ALGORITHMS:
        raise ValueError(
            "payload['alternative_algorithm'] must be one of: local_search, fuzzy_logic, tabu_search, firefly, or null"
        )

    # Apply payload-local solver selectors and restore afterwards so callers don't
    # permanently mutate process-global defaults across requests.
    old_configuration = CONFIGURATION
    old_alternative = ALTERNATIVE_ALGORITHM
    old_trio_enforcement = ENFORCE_GRADUATION_TRIO_ONLY_SEMESTER
    CONFIGURATION = requested_configuration
    ALTERNATIVE_ALGORITHM = requested_alternative
    ENFORCE_GRADUATION_TRIO_ONLY_SEMESTER = requested_trio_enforcement

    try:
        return _run_core(
            current_sem,
            passed_courses,
            failed_courses,
            with_petition=with_petition,
            forced_semester_map=forced_semester_map,
        )
    finally:
        CONFIGURATION = old_configuration
        ALTERNATIVE_ALGORITHM = old_alternative
        ENFORCE_GRADUATION_TRIO_ONLY_SEMESTER = old_trio_enforcement


def run_pos_with_state(current_sem, passed_courses, failed_courses, with_petition: bool = False):
    """
    Core solver pipeline shared by static and dynamic modes.
    Expects current_sem, passed_courses, failed_courses to be precomputed.
    """
    _algo_terminal = (
        f"{ALTERNATIVE_ALGORITHM} (heuristic)"
        if ALTERNATIVE_ALGORITHM is not None
        else "cp_sat (OR-Tools CP-SAT)"
    )
    print(
        f"\nActive solver — configuration: {CONFIGURATION!r}  |  algorithm: {_algo_terminal}"
    )
    _t_solve = time.perf_counter()
    result = _run_core(current_sem, passed_courses, failed_courses, with_petition=with_petition)
    _solve_secs = time.perf_counter() - _t_solve
    print(f"Solve time: {_solve_secs:.3f} s (wall clock, full pipeline)")

    status = result.get("status")
    if status == "ok":
        lines = ["--- GENERATED PROGRAM OF STUDY ---"]
        for sem in result["pos"]:
            label = sem["label"]
            total_u = sem["total_units"]
            courses_in_sem = sem["courses"]
            lines.append(f"{label} ({total_u} units)")
            for course in courses_in_sem:
                lines.append(
                    f"  • {course['code']} — {course['title']} ({course['units']} units)"
                )
        print("\n" + "\n".join(lines))
        try:
            pdf_path = export_generated_pos_pdf(result.get("pos") or [])
            print(f"\nPDF exported: {pdf_path}")
            _open_file_with_default_app(Path(pdf_path))
        except Exception as e:
            print(f"\nPDF export skipped: {e}")
    else:
        message = result.get("message")
        if message:
            print(f"\n{message}")


def run_pos_generator():
    # 1. Input & Data Handling (static mode)
    current_sem, passed_courses, failed_courses = gather_input_and_grades()

    # Ask if petition mode should be used for bottleneck subjects
    use_petition_input = input("Use petition mode for bottleneck subjects? (Y/N): ").strip().upper()
    with_petition = use_petition_input == "Y"

    # Delegate to shared pipeline
    run_pos_with_state(current_sem, passed_courses, failed_courses, with_petition=with_petition)


if __name__ == "__main__":
    run_pos_generator()