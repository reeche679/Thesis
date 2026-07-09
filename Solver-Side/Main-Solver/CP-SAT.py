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

import math
import random
import json
import hashlib
import time
from ortools.sat.python import cp_model
from algorithms_and_helpers.curriculum_runtime import (
    GRADUATION_TRIO,
    PREREQ_ALIASES,
    YEAR_STANDING_RULES,
    courses,
    curriculum_context_scope,
    get_curriculum_context,
    normalize_offering_category_value,
    resolve_curriculum_context_from_payload,
    u,
)
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
    try:
        from solver_settings import CP_SAT_CONSTRAINT_TRACE_COURSES as _TRACE_DEFAULTS
    except ImportError:
        _TRACE_DEFAULTS = ()
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
    _TRACE_DEFAULTS = getattr(_settings_module, "CP_SAT_CONSTRAINT_TRACE_COURSES", ())
from pos_terminal_display import export_generated_pos_pdf
from algorithms_and_helpers.discrete_opt_adapter import (
    DiscreteOptUnavailableError,
    run_discrete_opt_search,
)
from algorithms_and_helpers.cpmpy_minizinc_adapter import (
    CpmPyMiniZincUnavailableError,
    run_cpmpy_minizinc_search,
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


# Align with Django `validate_pos`: 50%/75% rules apply per curriculum semester only when
# the student has grades totaling at least this many units in that semester (graded courses only).
MIN_GRADED_UNITS_PER_SEMESTER_FOR_FAIL_RATIO = 10


def _env_trace_course_token_strings() -> list[str]:
    raw = os.environ.get("CP_SAT_CONSTRAINT_TRACE_COURSES", "").strip()
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def _resolve_trace_course_codes(payload: dict | None, courses_dict: dict) -> frozenset[str]:
    """
    Map payload / solver_settings / env strings to canonical keys present in ``courses_dict``.
    """
    raw: list[str] = []
    if isinstance(payload, dict):
        v = payload.get("cp_sat_constraint_trace_courses")
        if isinstance(v, str) and v.strip():
            raw.extend(p.strip() for p in v.split(",") if p.strip())
        elif isinstance(v, (list, tuple)):
            raw.extend(str(x).strip() for x in v if str(x).strip())
    # Accept tuple/list, or a single str from settings (``("X")`` is a str, not a 1-tuple).
    _td = _TRACE_DEFAULTS or ()
    if isinstance(_td, str):
        _td = (_td,)
    for x in _td:
        s = str(x).strip()
        if s:
            raw.append(s)
    raw.extend(_env_trace_course_token_strings())

    norm_index = {_normalize_code(k): k for k in courses_dict}
    out: list[str] = []
    seen: set[str] = set()
    for x in raw:
        k = norm_index.get(_normalize_code(x))
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return frozenset(out)


def _trace_pipeline_order_doc() -> list[str]:
    return [
        "1) semester IntVar per course (passed → fixed 0)",
        "2) forced_semester_map equalities (if any)",
        "3) offering window constraints (see CONFIGURATION.offering_fn)",
        "4) per-semester unit-cap reified literals (all remaining courses)",
        "5) graduation trio same-semester / exclusion (if ENFORCE_GRADUATION_TRIO_ONLY_SEMESTER)",
        "6) prerequisites, year-standing prefix units, co-requisites",
        "7) objective (Minimize via configuration objective_fn)",
    ]


def _trace_append(
    events: list | None,
    trace_codes: frozenset[str],
    course: str | None,
    event: dict,
    *,
    allow_global: bool = False,
) -> None:
    """Record one model-building step for traced courses (or allow_global summary rows)."""
    if events is None:
        return
    if not allow_global:
        if not trace_codes or course is None or course not in trace_codes:
            return
    elif not trace_codes:
        return
    row = dict(event)
    row["seq"] = len(events) + 1
    if course is not None:
        row["course"] = course
    events.append(row)


def _int_units_for_cp_sat(raw) -> int:
    """
    CP-SAT linear constraints require integer coefficients. Approved JSON from Django
    often has ``units`` as JSON floats (e.g. 3.0); mixing ``b * 3.0`` with ``b * 3`` in
    the same ``sum()`` raises TypeError from OR-Tools.
    """
    try:
        return int(round(float(raw if raw is not None else 0)))
    except (TypeError, ValueError):
        return 0


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
    Check per-semester failure rate (by graded units, not course count). Returns None if OK;
    otherwise returns the message to display (no POS generated).
    - >=75% of graded units failed in any past semester (with >= min graded units): cannot generate.
    - >=50% of graded units failed (same threshold): manual evaluation.
    Semesters with fewer than MIN_GRADED_UNITS_PER_SEMESTER_FOR_FAIL_RATIO graded units are skipped
    (e.g. short midterm tables).
    """
    passed_set = set(passed_courses or [])
    failed_set = set(failed_courses or [])
    over_75 = []  # (label, failed_units, graded_units, pct, n_failed_courses, n_graded_courses)
    over_50 = []
    for s in range(1, current_sem):
        in_sem = [c for c in courses if _first_semester(courses.get(c, {})) == s]
        if not in_sem:
            continue
        graded = [c for c in in_sem if c in passed_set or c in failed_set]
        if not graded:
            continue
        graded_units = 0.0
        failed_units = 0.0
        for c in graded:
            try:
                uu = float(courses.get(c, {}).get("units", 0) or 0)
            except (TypeError, ValueError):
                uu = 0.0
            graded_units += uu
            if c in failed_set:
                failed_units += uu
        if graded_units < MIN_GRADED_UNITS_PER_SEMESTER_FOR_FAIL_RATIO:
            continue
        ratio = failed_units / graded_units if graded_units else 0.0
        pct = round(ratio * 100)
        label = _sem_label(s)
        failed_c = sum(1 for c in graded if c in failed_set)
        n_graded_c = len(graded)
        entry = (label, failed_units, graded_units, pct, failed_c, n_graded_c)
        if ratio >= 0.75:
            over_75.append(entry)
        elif ratio >= 0.50:
            over_50.append(entry)
    if over_75:
        lines = [
            "Probation: POS cannot be generated.",
            "Failure rate is 75% or higher in:",
        ]
        for label, fu, gu, pct, fc, tc in over_75:
            lines.append(
                f"  • {label}: {pct}% of graded units failed "
                f"({int(round(fu))} of {int(round(gu))} units; {fc} of {tc} courses graded)"
            )
        lines.append("Please proceed to the Department Head for manual evaluation.")
        return "\n".join(lines)
    if over_50:
        lines = [
            "Probation: Manual evaluation required.",
            "Failure rate is 50% or higher in:",
        ]
        for label, fu, gu, pct, fc, tc in over_50:
            lines.append(
                f"  • {label}: {pct}% of graded units failed "
                f"({int(round(fu))} of {int(round(gu))} units; {fc} of {tc} courses graded)"
            )
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


def create_semester_variables(
    model,
    current_sem,
    passed_courses,
    *,
    constraint_trace: list | None = None,
    trace_course_codes: frozenset[str] | None = None,
):
    """Create semester variables for all courses."""
    tc = trace_course_codes or frozenset()
    semester = {}
    for code in courses:
        if code in passed_courses:
            semester[code] = model.NewIntVar(0, 0, f"sem_{code}")
            _trace_append(
                constraint_trace,
                tc,
                code,
                {"phase": "semester_variable", "kind": "passed_fixed_zero"},
            )
        else:
            semester[code] = model.NewIntVar(current_sem, u.MAX_SEMESTER, f"sem_{code}")
            _trace_append(
                constraint_trace,
                tc,
                code,
                {
                    "phase": "semester_variable",
                    "kind": "remaining",
                    "domain": [int(current_sem), int(u.MAX_SEMESTER)],
                },
            )

    return semester


# Single configuration selection is centralized in solver_settings.py.
# CP-SAT configuration keys come from algorithms_and_helpers.Configurations.CONFIGURATIONS.

ALTERNATIVE_ALGORITHMS = {"discrete_opt", "cpmpy_minizinc"}



def add_offering_constraints(
    model,
    semester,
    passed_courses,
    current_sem,
    with_petition: bool,
    failed_courses=None,
    *,
    constraint_trace: list | None = None,
    trace_course_codes: frozenset[str] | None = None,
):
    """Add offering-window constraints using the selected configuration."""
    failed_set = frozenset(failed_courses or ())
    spec = CONFIGURATIONS[CONFIGURATION]
    trace_kw: dict = {}
    tc = trace_course_codes or frozenset()
    if constraint_trace is not None and tc:
        trace_kw["constraint_trace"] = constraint_trace
        trace_kw["trace_course_codes"] = tc
    spec.offering_fn(
        model,
        semester,
        courses,
        passed_courses,
        current_sem,
        with_petition,
        _first_semester,
        u.MAX_SEMESTER,
        failed_set,
        **trace_kw,
    )


# Graduation trio enforcement toggle is centralized in solver_settings.py.


def add_graduation_semester_constraint(
    model,
    semester,
    passed_courses,
    *,
    constraint_trace: list | None = None,
    trace_course_codes: frozenset[str] | None = None,
):
    """
    Enforce that the last semester contains ONLY CpE Practice 2, Compre, and OJT.
    - The three must be in the same semester.
    - No other course may be in that semester.
    """
    trio = [c for c in GRADUATION_TRIO if c in courses and c not in passed_courses]
    if len(trio) < 2:
        return  # Not enough to enforce (e.g. all passed or only one left)
    ref = trio[0]
    tc = trace_course_codes or frozenset()
    # All trio members in same semester
    for c in trio[1:]:
        model.Add(semester[c] == semester[ref])
        if c in tc or ref in tc:
            _trace_append(
                constraint_trace,
                tc,
                c if c in tc else ref,
                {"phase": "graduation_trio", "kind": "same_semester", "with_ref": ref},
            )
    # No other course may share that semester
    for c, info in courses.items():
        if c in passed_courses or c in GRADUATION_TRIO:
            continue
        model.Add(semester[c] != semester[ref])
        if c in tc:
            _trace_append(
                constraint_trace,
                tc,
                c,
                {"phase": "graduation_trio", "kind": "exclude_non_trio_from_trio_semester", "trio_ref": ref},
            )


def add_unit_constraints(
    model,
    semester,
    passed_courses,
    current_sem,
    *,
    constraint_trace: list | None = None,
    trace_course_codes: frozenset[str] | None = None,
):
    """Add per-semester unit load constraints."""
    tc = trace_course_codes or frozenset()
    unit_logged: set[str] = set()
    for s in range(current_sem, u.MAX_SEMESTER + 1):
        active_in_sem = []
        for c, info in courses.items():
            if c in passed_courses:
                continue
            b = model.NewBoolVar(f"{c}_in_sem_{s}")
            if constraint_trace is not None and c in tc and c not in unit_logged:
                unit_logged.add(c)
                _trace_append(
                    constraint_trace,
                    tc,
                    c,
                    {
                        "phase": "unit_cap",
                        "kind": "reified_in_semester",
                        "first_semester_index_logged": int(s),
                        "note": "BoolVar per (course, semester) then summed into cap; order follows courses dict iteration",
                    },
                )
            model.Add(semester[c] == s).OnlyEnforceIf(b)
            model.Add(semester[c] != s).OnlyEnforceIf(b.Not())
            active_in_sem.append(b * _int_units_for_cp_sat(info.get("units", 0)))
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
    *,
    constraint_trace: list | None = None,
    trace_course_codes: frozenset[str] | None = None,
):
    """Add prerequisite, year-standing, and co-requisite constraints."""
    tc = trace_course_codes or frozenset()

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
            units = _int_units_for_cp_sat(info.get("units", 0))
            prefix_courses.append((code, units))
            total_units += units
            if code in passed_courses:
                earned_fixed += units

        if total_units <= 0:
            return

        req_units = math.ceil(0.85 * total_units)
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
        _trace_append(
            constraint_trace,
            tc,
            course,
            {
                "phase": "year_standing_prefix_units",
                "remaining_needed_units": int(remaining_needed),
                "scoped": bool(enforce_if),
            },
        )

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
                    if c in tc:
                        _trace_append(
                            constraint_trace,
                            tc,
                            c,
                            {"phase": "prerequisite", "kind": "or_group_sum_ge_1", "head_course": c},
                        )
                    else:
                        for alt in prereq:
                            if not isinstance(alt, str):
                                continue
                            ac = resolve_course(alt)
                            if ac and ac in tc:
                                _trace_append(
                                    constraint_trace,
                                    tc,
                                    ac,
                                    {
                                        "phase": "prerequisite",
                                        "kind": "or_group_branch_as_prereq_of",
                                        "head_course": c,
                                    },
                                )
                                break
                else:
                    print(
                        f"Warning: {c} has prereq alternatives but none match known courses/standing."
                    )
            else:
                prereq_course = resolve_course(prereq)
                prereq_year = resolve_year_standing(prereq)
                if prereq_course:
                    model.Add(semester[c] > semester[prereq_course])
                    head = c if c in tc else (prereq_course if prereq_course in tc else None)
                    if head:
                        _trace_append(
                            constraint_trace,
                            tc,
                            head,
                            {
                                "phase": "prerequisite",
                                "kind": "simple_order_gt",
                                "head_course": c,
                                "prereq_course": prereq_course,
                            },
                        )
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
                head = c if c in tc else (coreq_course if coreq_course in tc else None)
                if head:
                    _trace_append(
                        constraint_trace,
                        tc,
                        head,
                        {
                            "phase": "corequisite",
                            "kind": "order_ge",
                            "head_course": c,
                            "coreq_course": coreq_course,
                        },
                    )
            else:
                print(f"Warning: {c} has unknown co-req '{coreq}'.")


def add_objective(
    model,
    semester,
    passed_courses,
    failed_courses,
    current_sem,
    *,
    constraint_trace: list | None = None,
    trace_course_codes: frozenset[str] | None = None,
):
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
    tc = trace_course_codes or frozenset()
    if constraint_trace is not None and tc:
        _trace_append(
            constraint_trace,
            tc,
            None,
            {
                "phase": "objective",
                "configuration": CONFIGURATION,
                "courses_traced": sorted(tc),
                "note": "objective_fn minimizes over all remaining courses; see Configurations objective",
            },
            allow_global=True,
        )


def _solve_and_collect(
    model,
    semester,
    passed_courses,
    failed_courses,
    current_sem,
    *,
    with_petition: bool = False,
    constraint_trace: list | None = None,
):
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
        return {"status": "ok", "message": None, "pos": pos, **({"constraint_build_trace": constraint_trace} if constraint_trace else {})}
    else:
        import io
        import contextlib

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _log_infeasible(
                solver, status, current_sem, failed_courses, passed_courses, courses, u
            )
        msg = buf.getvalue().strip()
        hint = _infeasibility_domain_hint(current_sem, passed_courses, failed_courses, with_petition)
        if hint:
            msg = msg + hint
        else:
            msg = (
                msg
                + "\n\nAutomated diagnosis: every remaining course has at least one allowed semester "
                "under the offering rules, so this infeasibility is likely from **graduation trio + no other "
                "courses in that semester**, **per-term unit caps**, **prerequisites**, or **year standing**. "
                f"ENFORCE_GRADUATION_TRIO_ONLY_SEMESTER={ENFORCE_GRADUATION_TRIO_ONLY_SEMESTER}."
            )
        out = {"status": "infeasible", "message": msg, "pos": []}
        if constraint_trace:
            out["constraint_build_trace"] = constraint_trace
        return out


def _apply_forced_semester_assignments(
    model,
    semester,
    passed_courses,
    forced_semester_map,
    *,
    constraint_trace: list | None = None,
    trace_course_codes: frozenset[str] | None = None,
):
    """
    If provided, force specific courses to be scheduled in specific semesters.

    forced_semester_map: { "<course_code>": <semester_index_int> }
    """
    if not forced_semester_map or not isinstance(forced_semester_map, dict):
        return

    tc = trace_course_codes or frozenset()
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
        _trace_append(
            constraint_trace,
            tc,
            code,
            {"phase": "forced_semester_map", "forced_semester": int(si)},
        )


def _valid_semesters_for_course(code, info, current_sem, with_petition: bool, failed_courses=None):
    """Compute allowed semesters for a course under the active configuration."""
    failed_set = frozenset(failed_courses or ())
    max_sem = u.MAX_SEMESTER
    if CONFIGURATION == "config3_weightedtriocap":
        max_sem = min(max_sem, 14)

    graduation_trio = set(get_curriculum_context().graduation_trio)
    if CONFIGURATION == "config3_weightedtriocap" and code in graduation_trio:
        return [max_sem]

    if CONFIGURATION == "config0_no_modulo3_baseline":
        orig = _first_semester(info) or 1
        cat = normalize_offering_category_value(info)
        lo = max(current_sem, orig)
        strict = [s for s in range(current_sem, max_sem + 1) if s >= lo]
        if cat == "Bottleneck" and with_petition:
            return list(range(current_sem, max_sem + 1))
        if code in failed_set and cat in ("Rigid", "Flexible"):
            return list(range(current_sem, max_sem + 1))
        if cat == "Flexible" and with_petition:
            return list(range(current_sem, max_sem + 1))
        return strict

    orig = _first_semester(info) or 1
    cat = normalize_offering_category_value(info)

    valid = []
    for s in range(current_sem, max_sem + 1):
        sem_type, orig_type = s % 3, orig % 3
        if cat == "Rigid":
            # Rigid is always locked to its orig term-type band (failed retakes included).
            if sem_type == orig_type:
                valid.append(s)
        elif cat == "Bottleneck":
            if CONFIGURATION == "config3_weightedtriocap":
                valid.append(s)
            elif with_petition or sem_type == orig_type:
                valid.append(s)
        elif cat == "Flexible" and (
            (orig_type in [1, 2] and sem_type in [1, 2]) or (orig_type == 0 and sem_type == 0)
        ):
            valid.append(s)
        else:
            valid.append(s)
    return valid


def _infeasibility_domain_hint(
    current_sem: int,
    passed_courses,
    failed_courses,
    with_petition: bool,
) -> str:
    """
    When CP-SAT is infeasible, list remaining courses whose allowed-semester set is empty
    under the same rules as `_valid_semesters_for_course` (offering windows).
    """
    failed_set = frozenset(failed_courses or ())
    empty: list[tuple[str, object, object]] = []
    for c, info in courses.items():
        if c in passed_courses:
            continue
        try:
            vs = _valid_semesters_for_course(c, info, current_sem, with_petition, failed_set)
        except Exception:
            continue
        if len(vs) == 0:
            raw_cat = info.get("category") if isinstance(info, dict) else None
            empty.append((c, raw_cat, _first_semester(info)))
    if not empty:
        return ""
    n_rem = len([c for c in courses if c not in passed_courses])
    lines = [
        "",
        "Automated diagnosis (courses with 0 allowed semesters under offering rules):",
        f"  CONFIGURATION={CONFIGURATION!r}  ENFORCE_GRADUATION_TRIO_ONLY_SEMESTER={ENFORCE_GRADUATION_TRIO_ONLY_SEMESTER}",
        f"  current_sem={current_sem}  MAX_SEMESTER={u.MAX_SEMESTER}  remaining_courses={n_rem}",
        "  Common fix: ensure each course `category` is the string Rigid, Flexible, or Bottleneck (not a JSON number).",
    ]
    for row in sorted(empty, key=lambda x: x[0])[:60]:
        lines.append(f"    - {row[0]!r}  category_raw={row[1]!r}  orig_sem={row[2]!r}")
    return "\n".join(lines)


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


def _state_violation_summary(
    state,
    current_sem,
    passed_courses,
    with_petition: bool,
    valid_map_override: dict[str, set[int]] | None = None,
    course_code_index: dict[str, str] | None = None,
    prereq_alias_map: dict[str, str] | None = None,
    year_standing_map: dict[str, str] | None = None,
    failed_courses_for_domains=None,
):
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
        if valid_map_override is not None:
            valid = valid_map_override.get(c)
            if valid is None:
                # Fallback: should only happen if caller didn't include c.
                valid = set(_valid_semesters_for_course(c, info, current_sem, with_petition, failed_courses_for_domains))
            if s not in valid:
                violation_count += 1
        else:
            valid = _valid_semesters_for_course(c, info, current_sem, with_petition, failed_courses_for_domains)
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

    # 3) Prerequisite/co-requisite checks (with CP-SAT-like year-standing check)
    if course_code_index is None:
        course_code_index = {_normalize_code(code): code for code in courses}
    if prereq_alias_map is None:
        prereq_alias_map = {_normalize_code(k): v for k, v in PREREQ_ALIASES.items()}
    if year_standing_map is None:
        year_standing_map = {_normalize_code(k): v for k, v in YEAR_STANDING_RULES.items()}

    def resolve_course(code: str):
        if not isinstance(code, str):
            return None
        return course_code_index.get(_normalize_code(code))

    def resolve_year_standing(code: str):
        if not isinstance(code, str):
            return None
        return year_standing_map.get(_normalize_code(code))

    def _is_critical_foundation(token) -> bool:
        """
        Strongly penalize violations involving key foundation prereqs.
        """
        critical = {_normalize_code("MATH 102"), _normalize_code("EE 212")}
        if isinstance(token, str):
            return _normalize_code(token) in critical
        if isinstance(token, (list, tuple)):
            return any(isinstance(t, str) and _normalize_code(t) in critical for t in token)
        return False

    def _critical_prereq_pair_penalty(course_code: str, prereq_code: str) -> int:
        """
        Extra-heavy penalties for known critical chains that must preserve order.
        """
        cc = _normalize_code(course_code)
        pc = _normalize_code(prereq_code)
        critical_pairs = {
            (_normalize_code("MATH 102"), _normalize_code("MATH 101")),
            (_normalize_code("EE 212"), _normalize_code("PHYS 103")),
            (_normalize_code("CPE 302"), _normalize_code("ECE 405")),
        }
        return 5000 if (cc, pc) in critical_pairs else 0

    def year_standing_satisfied(course: str) -> bool:
        """
        Mirror CP-SAT's prefix-units standing rule used in add_year_standing():
        when taking `course`, student must have >=90% of units from all courses
        scheduled strictly before `course`'s original semester.
        """
        info_c = courses.get(course)
        orig_sem_c = _first_semester(info_c)
        if orig_sem_c is None:
            return True

        sem_c = state.get(course, current_sem)
        total_units = 0
        earned_before = 0
        for code, info in courses.items():
            orig_sem = _first_semester(info)
            if orig_sem is None or orig_sem >= orig_sem_c:
                continue
            units = info.get("units", 0)
            total_units += units
            if code in passed_courses:
                earned_before += units
            else:
                sem_code = state.get(code, 10**9)
                if sem_code < sem_c:
                    earned_before += units

        if total_units <= 0:
            return True
        req_units = math.ceil(0.9 * total_units)
        return earned_before >= req_units

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
                    alt_year = resolve_year_standing(alt)
                    if alt_course:
                        if alt_course in passed_courses or state.get(alt_course, 10**9) < sem_c:
                            satisfied = True
                            break
                    elif alt_year is not None:
                        if year_standing_satisfied(c):
                            satisfied = True
                            break
            else:
                pre_course = resolve_course(prereq)
                pre_year = resolve_year_standing(prereq)
                if pre_course:
                    satisfied = pre_course in passed_courses or state.get(pre_course, 10**9) < sem_c
                elif pre_year is not None:
                    satisfied = year_standing_satisfied(c)

            if not satisfied:
                # Strict prerequisite enforcement:
                # - very heavy default prereq penalty
                # - extra-heavy penalty for key foundation dependency breaks
                penalty = 5_000
                if _is_critical_foundation(prereq):
                    penalty = 20_000
                if isinstance(prereq, str):
                    pre_course_code = resolve_course(prereq)
                    if pre_course_code:
                        penalty += _critical_prereq_pair_penalty(c, pre_course_code)
                violation_count += penalty
                prereq_violations += 1

        # co-requisites
        for coreq in _get_req_list(
            info, ("co-requisites", "co_requisites", "corequisites", "co-reqs")
        ):
            co_course = resolve_course(coreq)
            if co_course and co_course not in passed_courses:
                if state.get(co_course, -1) > sem_c:
                    # Co-requisite scheduled after dependent course: strongly penalize.
                    violation_count += 3_000

    # 4) Explicit near-hard prerequisite order guards for critical chains.
    # These are intentionally very large to behave like hard constraints
    # inside heuristic search/repair.
    def _strict_after(course_code: str, prereq_code: str, penalty: int = 1_000_000):
        nonlocal violation_count
        if course_code in passed_courses:
            return
        sem_course = state.get(course_code)
        if sem_course is None:
            return
        if prereq_code in passed_courses:
            return
        sem_prereq = state.get(prereq_code)
        if sem_prereq is None:
            return
        if sem_course <= sem_prereq:
            violation_count += penalty

    _strict_after("ECE 405", "EE 212", penalty=10_000_000)

    _strict_after("MATH 102", "MATH 101", penalty=10_000_000)
    _strict_after("EE 212", "PHYS 103", penalty=10_000_000)
    _strict_after("CpE 302", "ECE 405", penalty=10_000_000)
    # Additional critical CpE prerequisite ordering to prevent early placement.
    _strict_after("CpE 308", "CpE 302", penalty=10_000_0000)   # Microprocessors after Logic Circuits/Design
    _strict_after("CpE 311", "CpE 308", penalty=10_000_000)   # Computer Architecture after Microprocessors
    # CpE 310 depends on BOTH CpE 305 and CpE 306.
    _strict_after("CpE 310", "CpE 305", penalty=10_000_000)
    _strict_after("CpE 310", "CpE 306", penalty=10_000_000)

    remaining = [c for c in courses if c not in passed_courses]
    if remaining:
        max_sem = max(state.get(c, current_sem) for c in remaining)
        sum_sem = sum(state.get(c, current_sem) for c in remaining)
    else:
        max_sem = current_sem
        sum_sem = 0

    # Count empty internal semesters between first scheduled and max scheduled.
    # This helps produce CP-SAT-like compact plans (fewer "gap" semesters).
    if remaining:
        min_sem = min(state.get(c, current_sem) for c in remaining)
        occupied = {state.get(c, current_sem) for c in remaining}
        empty_gap_semesters = sum(1 for s in range(min_sem, max_sem + 1) if s not in occupied)
        # Tail-empty semesters after last scheduled course up to MAX_SEMESTER.
        tail_empty_semesters = max(0, u.MAX_SEMESTER - max_sem)
    else:
        empty_gap_semesters = 0
        tail_empty_semesters = 0

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
        "empty_gap_semesters": float(empty_gap_semesters),
        "tail_empty_semesters": float(tail_empty_semesters),
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

    failed_set = frozenset(failed_courses or ())
    # Per-course allowed semesters from CONFIGURATION (modulo-3 / config0 / config3, categories,
    # petition). Heuristic search never leaves these domains — same offering reduction as CP-SAT.
    valid_map = {
        c: tuple(_valid_semesters_for_course(c, courses[c], current_sem, with_petition, failed_set))
        for c in remaining
    }
    # Precompute maps once so the discrete_opt hot loop doesn't rebuild them.
    valid_map_override = {c: set(sems) for c, sems in valid_map.items()}
    course_code_index = {_normalize_code(code): code for code in courses}
    prereq_alias_map = {_normalize_code(k): v for k, v in PREREQ_ALIASES.items()}
    year_standing_map = {_normalize_code(k): v for k, v in YEAR_STANDING_RULES.items()}

    # Seed with earliest feasible-by-window semester; perturb lightly for exploration.
    rng_init = random.Random(42)
    initial_state = {}
    for c in remaining:
        choices = valid_map[c] if valid_map[c] else (current_sem,)
        initial_state[c] = choices[0]
        if len(choices) > 1 and rng_init.random() < 0.25:
            initial_state[c] = rng_init.choice(choices[: min(3, len(choices))])

    initial_v = _state_violation_summary(
        initial_state,
        current_sem,
        passed_courses,
        with_petition,
        valid_map_override=valid_map_override,
        course_code_index=course_code_index,
        prereq_alias_map=prereq_alias_map,
        year_standing_map=year_standing_map,
        failed_courses_for_domains=failed_set,
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

    # Discrete-opt speedup: the adapter evaluates both `violation_summary_fn(state)`
    # and `score_fn(state)` for the *same* candidate `state`. Cache the last
    # `_state_violation_summary(state, ...)` result so we don't recompute the full
    # violation model twice per candidate.
    _last_eval_key: tuple[int, ...] | None = None
    _last_eval_v: dict[str, float] | None = None

    def _state_key(st: dict[str, int]) -> tuple[int, ...]:
        # Remaining courses are always assigned by `discrete_opt_adapter`.
        return tuple(int(st.get(c, -1)) for c in remaining)

    def _eval_v(st: dict[str, int]) -> dict[str, float]:
        nonlocal _last_eval_key, _last_eval_v
        k = _state_key(st)
        if _last_eval_key == k and _last_eval_v is not None:
            return _last_eval_v
        _last_eval_key = k
        _last_eval_v = _state_violation_summary(
            st,
            current_sem,
            passed_courses,
            with_petition,
            valid_map_override=valid_map_override,
            course_code_index=course_code_index,
            prereq_alias_map=prereq_alias_map,
            year_standing_map=year_standing_map,
            failed_courses_for_domains=failed_set,
        )
        return _last_eval_v

    violation_summary_fn = _eval_v

    def feasible_fn(state):
        return _eval_v(state)["violations"] == 0

    def score_fn(state):
        v = _eval_v(state)
        # Keep hard-constraint penalties dominant.
        return (
            (1_000_000.0 * v["violations"])
            + (20_000.0 * v["max_sem"])
            + (10_000.0 * v.get("empty_gap_semesters", 0.0))
            + (2_000.0 * v.get("tail_empty_semesters", 0.0))
            + (10.0 * v["sum_sem"])
        )

    algo = ALTERNATIVE_ALGORITHM
    if algo == "discrete_opt":
        try:
            best_state, _, is_feasible = run_discrete_opt_search(
                initial_state=initial_state,
                score_fn=score_fn,
                feasible_fn=feasible_fn,
                violation_summary_fn=violation_summary_fn,
                remaining=remaining,
                valid_map=valid_map,
            )
        except DiscreteOptUnavailableError as exc:
            return {
                "status": "discrete_opt_unavailable",
                "message": str(exc),
                "pos": [],
            }
    else:
        try:
            best_state, is_feasible, meta = run_cpmpy_minizinc_search(
                current_sem=current_sem,
                passed_courses=passed_courses,
                with_petition=with_petition,
                valid_map=valid_map,
                courses=courses,
                max_semester=u.MAX_SEMESTER,
                max_units_for_sem=u.max_units_for_sem,
                get_req_list=_get_req_list,
                normalize_code=_normalize_code,
                prereq_aliases=dict(PREREQ_ALIASES),
                year_standing_rules=dict(YEAR_STANDING_RULES),
                enforce_graduation_trio_only_semester=ENFORCE_GRADUATION_TRIO_ONLY_SEMESTER,
                graduation_trio=tuple(get_curriculum_context().graduation_trio),
                solver_name="chuffed",
            )
            if not is_feasible:
                return {
                    "status": "cpmpy_minizinc_infeasible",
                    "message": f"CPMpy MiniZinc did not find a feasible solution ({meta.get('status')}).",
                    "pos": [],
                }
        except CpmPyMiniZincUnavailableError as exc:
            return {
                "status": "cpmpy_minizinc_unavailable",
                "message": str(exc),
                "pos": [],
            }

    violations = _state_violation_summary(
        best_state,
        current_sem,
        passed_courses,
        with_petition,
        valid_map_override=valid_map_override,
        course_code_index=course_code_index,
        prereq_alias_map=prereq_alias_map,
        year_standing_map=year_standing_map,
        failed_courses_for_domains=failed_set,
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
            f"Generated using {algo} best-{'feasible' if is_feasible else 'so-far'} solution. "
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
    trace_course_codes: frozenset[str] | None = None,
):
    """Run the existing CP-SAT pipeline for the selected CONFIGURATION."""
    tc = trace_course_codes or frozenset()
    constraint_trace: list | None = [] if tc else None
    if constraint_trace is not None:
        _trace_append(
            constraint_trace,
            tc,
            None,
            {
                "phase": "cp_sat_model_build",
                "courses_traced": sorted(tc),
                "constraint_class_order": _trace_pipeline_order_doc(),
            },
            allow_global=True,
        )

    # 2. Model Initialization
    model = cp_model.CpModel()
    year_units, year_courses_list, earned_units_by_year = build_year_aggregates(
        passed_courses
    )
    course_code_index, prereq_alias_map, year_standing_map = build_normalized_maps()

    # 3. Variable Initialization
    semester = create_semester_variables(
        model,
        current_sem,
        passed_courses,
        constraint_trace=constraint_trace,
        trace_course_codes=tc,
    )

    # Optional: force semester assignments from a user-edited POS.
    _apply_forced_semester_assignments(
        model,
        semester,
        passed_courses,
        forced_semester_map,
        constraint_trace=constraint_trace,
        trace_course_codes=tc,
    )

    # 4. Constraints
    add_offering_constraints(
        model,
        semester,
        passed_courses,
        current_sem,
        with_petition,
        failed_courses,
        constraint_trace=constraint_trace,
        trace_course_codes=tc,
    )
    add_unit_constraints(
        model,
        semester,
        passed_courses,
        current_sem,
        constraint_trace=constraint_trace,
        trace_course_codes=tc,
    )
    if ENFORCE_GRADUATION_TRIO_ONLY_SEMESTER:
        add_graduation_semester_constraint(
            model,
            semester,
            passed_courses,
            constraint_trace=constraint_trace,
            trace_course_codes=tc,
        )
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
        constraint_trace=constraint_trace,
        trace_course_codes=tc,
    )

    # 5. Objective
    add_objective(
        model,
        semester,
        passed_courses,
        failed_courses,
        current_sem,
        constraint_trace=constraint_trace,
        trace_course_codes=tc,
    )

    # 6. Solver
    return _solve_and_collect(
        model,
        semester,
        passed_courses,
        failed_courses,
        current_sem,
        with_petition=with_petition,
        constraint_trace=constraint_trace,
    )


def _build_original_curriculum_pos(current_sem, passed_courses):
    """
    Build POS directly from the original curriculum (orig_sem) without solving.
    Used when there are no failed courses — a regular student should see the
    standard curriculum layout rather than an optimizer-rearranged one.
    """
    passed_set = set(passed_courses or [])
    pos = []
    for s in range(current_sem, u.MAX_SEMESTER + 1):
        sem_list = [
            c for c, info in courses.items()
            if c not in passed_set and _first_semester(info) == s
        ]
        label = _sem_label(s)
        total_u = sum(courses[c].get("units", 0) for c in sem_list) if sem_list else 0
        pos.append({
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
        })
    return {"status": "ok", "message": None, "pos": pos}


def _run_core(
    current_sem,
    passed_courses,
    failed_courses,
    with_petition: bool,
    forced_semester_map=None,
    trace_course_codes: frozenset[str] | None = None,
):
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

    # No failed courses and no forced overrides → regular student path.
    # Return the original curriculum layout without running the optimizer.
    if not failed_courses and not forced_semester_map:
        return _build_original_curriculum_pos(current_sem, passed_courses)

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
        trace_course_codes=trace_course_codes,
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
      - alternative_algorithm (str | null, optional): discrete_opt | cpmpy_minizinc
      - enforce_graduation_trio_only_semester (bool, optional)
      - curriculum_bundle (dict, optional): approved curriculum JSON (Phase 0 schema).
        When omitted, static ``courses.py`` + ``units_and_year_standings`` are used.
      - curriculum_version_id (optional): resolved by Django ``solver_bridge`` into
        ``curriculum_bundle`` before the solver is invoked; not read inside CP-SAT.
      - cp_sat_constraint_trace_courses (optional): list[str] or comma-separated str;
        when set, CP-SAT responses include ``constraint_build_trace`` (ordered model-build
        steps touching those course codes). Also see env ``CP_SAT_CONSTRAINT_TRACE_COURSES``
        and ``solver_settings.CP_SAT_CONSTRAINT_TRACE_COURSES``.

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
            "payload['alternative_algorithm'] must be one of: discrete_opt, cpmpy_minizinc, or null"
        )

    # Apply payload-local solver selectors and restore afterwards so callers don't
    # permanently mutate process-global defaults across requests.
    old_configuration = CONFIGURATION
    old_alternative = ALTERNATIVE_ALGORITHM
    old_trio_enforcement = ENFORCE_GRADUATION_TRIO_ONLY_SEMESTER
    CONFIGURATION = requested_configuration
    ALTERNATIVE_ALGORITHM = requested_alternative
    ENFORCE_GRADUATION_TRIO_ONLY_SEMESTER = requested_trio_enforcement

    ctx = resolve_curriculum_context_from_payload(payload)
    with curriculum_context_scope(ctx):
        try:
            trace_codes = _resolve_trace_course_codes(payload, courses)
            return _run_core(
                current_sem,
                passed_courses,
                failed_courses,
                with_petition=with_petition,
                forced_semester_map=forced_semester_map,
                trace_course_codes=trace_codes,
            )
        finally:
            CONFIGURATION = old_configuration
            ALTERNATIVE_ALGORITHM = old_alternative
            ENFORCE_GRADUATION_TRIO_ONLY_SEMESTER = old_trio_enforcement


def compute_placement_diagnostics(payload: dict) -> dict:
    """
    JSON-serializable coarse placement diagnostics (capacity, terms, constrained courses).
    Uses the same curriculum context and ``_valid_semesters_for_course`` offering domains
    as CP-SAT; does not run the solver.
    """
    if payload is None or not isinstance(payload, dict):
        raise ValueError("payload must be a dict")
    global CONFIGURATION, ALTERNATIVE_ALGORITHM, ENFORCE_GRADUATION_TRIO_ONLY_SEMESTER

    try:
        current_sem = int(payload.get("current_sem"))
    except (TypeError, ValueError):
        raise ValueError("payload['current_sem'] must be an integer")

    passed_courses = list(payload.get("passed_courses") or [])
    failed_courses = list(payload.get("failed_courses") or [])
    with_petition = bool(payload.get("with_petition", False))

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
            "payload['alternative_algorithm'] must be one of: discrete_opt, cpmpy_minizinc, or null"
        )

    old_configuration = CONFIGURATION
    old_alternative = ALTERNATIVE_ALGORITHM
    old_trio_enforcement = ENFORCE_GRADUATION_TRIO_ONLY_SEMESTER
    CONFIGURATION = requested_configuration
    ALTERNATIVE_ALGORITHM = requested_alternative
    ENFORCE_GRADUATION_TRIO_ONLY_SEMESTER = requested_trio_enforcement

    ctx = resolve_curriculum_context_from_payload(payload)
    with curriculum_context_scope(ctx):
        try:
            from algorithms_and_helpers.placement_diagnostics import run_placement_diagnostics

            def _vs(code, info):
                return _valid_semesters_for_course(code, info, current_sem, with_petition, failed_courses)

            return run_placement_diagnostics(
                courses=courses,
                passed_courses=passed_courses,
                failed_courses=failed_courses,
                current_sem=current_sem,
                with_petition=with_petition,
                max_semester=int(u.MAX_SEMESTER),
                max_units_for_sem=u.max_units_for_sem,
                valid_semesters_fn=_vs,
                first_semester_fn=_first_semester,
                normalize_code=_normalize_code,
                get_req_list=_get_req_list,
                prereq_aliases=dict(PREREQ_ALIASES),
                year_standing_rules=dict(YEAR_STANDING_RULES),
                configuration=str(CONFIGURATION),
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
            algo_component = ALTERNATIVE_ALGORITHM if ALTERNATIVE_ALGORITHM is not None else "cp_sat"
            pdf_path = export_generated_pos_pdf(
                result.get("pos") or [],
                solver_used=f"{_algo_terminal} | config: {CONFIGURATION}",
                solve_time_seconds=_solve_secs,
                algorithm_used_for_filename=algo_component,
                failed_courses=failed_courses,
            )
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