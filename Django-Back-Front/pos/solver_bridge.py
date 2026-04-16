from pathlib import Path
import runpy

from django.conf import settings

_SOLVER_NAMESPACE = None
# When CP-SAT.py changes on disk, reload so probation/unit-threshold fixes apply without a full server restart.
_SOLVER_SCRIPT_MTIME_AT_LOAD = None


def _get_solver_namespace():
    """
    Lazily load the CP-SAT solver module (CP-SAT.py) and cache its namespace.

    The solver lives in the sibling 'Solver-Side/Main-Solver/CP-SAT.py' folder
    relative to the Django project root.

    The namespace is reloaded when CP-SAT.py's modification time changes, so edits
    to solver logic (e.g. check_probation) are not stuck behind a one-time import cache.
    """
    global _SOLVER_NAMESPACE, _SOLVER_SCRIPT_MTIME_AT_LOAD

    # settings.BASE_DIR points to 'Django-Front-Back'.
    project_root = Path(settings.BASE_DIR).parent
    solver_script = project_root / "Solver-Side" / "Main-Solver" / "CP-SAT.py"
    if not solver_script.is_file():
        raise RuntimeError(f"Solver script not found at {solver_script}")

    try:
        current_mtime = solver_script.stat().st_mtime
    except OSError:
        current_mtime = None

    if _SOLVER_NAMESPACE is not None:
        if current_mtime is None:
            return _SOLVER_NAMESPACE
        if current_mtime == _SOLVER_SCRIPT_MTIME_AT_LOAD:
            return _SOLVER_NAMESPACE

    # run_path executes CP-SAT.py once and returns its globals as a dict.
    _SOLVER_NAMESPACE = runpy.run_path(str(solver_script))
    _SOLVER_SCRIPT_MTIME_AT_LOAD = current_mtime
    return _SOLVER_NAMESPACE


def generate_pos_via_solver(payload: dict) -> dict:
    """
    Thin wrapper around the solver's generate_pos(payload) function.

    payload keys:
      - current_sem (int)
      - passed_courses (list[str])
      - failed_courses (list[str])
      - with_petition (bool, optional)
    """
    ns = _get_solver_namespace()
    generate_pos = ns.get("generate_pos")
    if generate_pos is None:
        raise RuntimeError("generate_pos function not found in solver namespace")

    # Generate two alternative schedules:
    # - no petition
    # - with petition
    #
    # The UI will let the user choose which one to use for Preview/PDF.
    def _run(with_petition: bool) -> dict:
        p = dict(payload or {})
        p["with_petition"] = bool(with_petition)
        return generate_pos(p)

    result_no = _run(False)
    result_yes = _run(True)

    def _latest_semester_with_courses(result: dict) -> int | None:
        if not isinstance(result, dict) or result.get("status") != "ok":
            return None
        pos = result.get("pos") or []
        latest = None
        for sem in pos:
            try:
                total_units = int(sem.get("total_units", 0) or 0)
            except (TypeError, ValueError):
                total_units = 0
            if total_units <= 0:
                continue
            sem_idx = sem.get("semester_index")
            if isinstance(sem_idx, int):
                latest = sem_idx if latest is None else max(latest, sem_idx)
        return latest

    latest_no = _latest_semester_with_courses(result_no)
    latest_yes = _latest_semester_with_courses(result_yes)

    # Choose default: smaller latest semester (faster graduation). If tie, no petition.
    if latest_no is not None and latest_yes is not None:
        if latest_no < latest_yes:
            default_mode = "no_petition"
        elif latest_yes < latest_no:
            default_mode = "with_petition"
        else:
            default_mode = "no_petition"
    elif latest_no is not None:
        default_mode = "no_petition"
    elif latest_yes is not None:
        default_mode = "with_petition"
    else:
        # Neither schedule is "ok" (could be probation/infeasible).
        # Default to no petition but preserve both outputs.
        default_mode = "no_petition"

    default_res = result_no if default_mode == "no_petition" else result_yes

    # Provide a user-facing explanation for the default choice.
    def _make_reason() -> str:
        if latest_no is None and latest_yes is None:
            return "No schedule was feasible."
        if latest_no is None:
            return "Selected with petition because the no-petition schedule was not feasible."
        if latest_yes is None:
            return "Selected no petition because the with-petition schedule was not feasible."
        if latest_no < latest_yes:
            return f"Selected no petition because it finishes earlier (latest course semester: {latest_no} vs {latest_yes})."
        if latest_yes < latest_no:
            return f"Selected with petition because it finishes earlier (latest course semester: {latest_yes} vs {latest_no})."
        return f"Both options finish at the same time (latest course semester: {latest_no}); defaulted to no petition."

    return {
        # Backward-compatible top-level fields (Preview legacy uses these)
        "status": default_res.get("status"),
        "message": default_res.get("message"),
        "pos": default_res.get("pos") if isinstance(default_res, dict) else [],
        # New dual-output fields
        "pos_options": {
            "no_petition": {
                "status": result_no.get("status"),
                "message": result_no.get("message"),
                "pos": result_no.get("pos") if isinstance(result_no, dict) else [],
            },
            "with_petition": {
                "status": result_yes.get("status"),
                "message": result_yes.get("message"),
                "pos": result_yes.get("pos") if isinstance(result_yes, dict) else [],
            },
        },
        "default_petition_mode": default_mode,
        "default_reason": _make_reason(),
    }


def validate_pos_via_solver(payload: dict, with_petition: bool, forced_semester_map: dict | None) -> dict:
    """
    Validate feasibility of a user-edited POS by forcing course->semester assignments.

    Returns the solver's raw result dict, e.g.:
      - { "status": "ok", "pos": [...], ... }
      - { "status": "infeasible", "message": "...", "pos": [] }
    """
    ns = _get_solver_namespace()
    generate_pos = ns.get("generate_pos")
    if generate_pos is None:
        raise RuntimeError("generate_pos function not found in solver namespace")

    p = dict(payload or {})
    p["with_petition"] = bool(with_petition)
    if forced_semester_map is not None:
        p["forced_semester_map"] = forced_semester_map
    return generate_pos(p)


def get_solver_metadata() -> dict:
    """
    Return solver-side static metadata needed for typed violation heuristics.

    This avoids fragile sys.path imports in Django by reusing the same CP-SAT
    namespace loaded by `_get_solver_namespace()`.

    Returns:
      - courses: solver-side `courses` dict (code -> info)
      - year_standing_rules: solver-side `YEAR_STANDING_RULES`
      - max_units_for_sem: callable max_units_for_sem(sem_index) -> int
    """
    ns = _get_solver_namespace()
    courses = ns.get("courses") or {}
    year_standing_rules = ns.get("YEAR_STANDING_RULES") or {}
    u = ns.get("u")
    max_units_for_sem = None
    try:
        if u is not None and hasattr(u, "max_units_for_sem"):
            max_units_for_sem = u.max_units_for_sem
    except Exception:
        max_units_for_sem = None

    return {
        "courses": courses,
        "year_standing_rules": year_standing_rules,
        "max_units_for_sem": max_units_for_sem,
    }

