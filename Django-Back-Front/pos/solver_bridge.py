from pathlib import Path
import runpy
import sys

from django.conf import settings


def _attach_curriculum_bundle_from_version(payload: dict) -> dict:
    """
    If payload contains curriculum_version_id, load approved_json from the DB
    and set curriculum_bundle for the solver (solver stays ORM-free).
    """
    p = dict(payload or {})
    vid = p.get("curriculum_version_id")
    if vid is None:
        return p
    try:
        from pos.models import CurriculumVersion
    except Exception:
        p.pop("curriculum_version_id", None)
        return p
    try:
        pk = int(vid)
    except (TypeError, ValueError):
        p.pop("curriculum_version_id", None)
        return p
    cv = (
        CurriculumVersion.objects.filter(pk=pk, status=CurriculumVersion.STATUS_APPROVED)
        .only("approved_json")
        .first()
    )
    if cv and isinstance(cv.approved_json, dict) and cv.approved_json.get("courses"):
        p["curriculum_bundle"] = cv.approved_json
    # Solver (CP-SAT) is ORM-free: never pass DB ids into generate_pos.
    p.pop("curriculum_version_id", None)
    return p


def _curriculum_versions_mtime() -> float | None:
    """Latest CurriculumVersion.updated_at as unix time for solver namespace cache busting."""
    try:
        from django.db.models import Max
        from pos.models import CurriculumVersion

        ts = CurriculumVersion.objects.aggregate(m=Max("updated_at"))["m"]
        if ts is None:
            return None
        return ts.timestamp()
    except Exception:
        return None


_SOLVER_NAMESPACE = None
_SOLVER_MTIMES_AT_LOAD: dict[str, float | None] = {}

_SOLVER_WATCHED_FILES = [
    Path("Main-Solver") / "CP-SAT.py",
    Path("algorithms_and_helpers") / "Configurations.py",
    Path("courses_and_year_standings") / "units_and_year_standings.py",
    Path("courses_and_year_standings") / "courses.py",
    Path("Main-Solver") / "solver_settings.py",
]

_SOLVER_MODULE_PREFIXES = (
    "algorithms_and_helpers",
    "courses_and_year_standings",
    "solver_settings",
    "pos_terminal_display",
)


def _get_solver_namespace():
    """
    Lazily load the CP-SAT solver module (CP-SAT.py) and cache its namespace.

    The solver lives in the sibling 'Solver-Side/Main-Solver/CP-SAT.py' folder
    relative to the Django project root.

    The namespace is reloaded when any watched solver-side file's modification
    time changes, so edits to Configurations.py, courses.py, etc. are picked up
    without a full server restart.
    """
    global _SOLVER_NAMESPACE, _SOLVER_MTIMES_AT_LOAD

    project_root = Path(settings.BASE_DIR).parent
    solver_root = project_root / "Solver-Side"
    solver_script = solver_root / "Main-Solver" / "CP-SAT.py"
    if not solver_script.is_file():
        raise RuntimeError(f"Solver script not found at {solver_script}")

    def _current_mtimes() -> dict[str, float | None]:
        mtimes: dict[str, float | None] = {}
        for rel in _SOLVER_WATCHED_FILES:
            p = solver_root / rel
            try:
                mtimes[str(rel)] = p.stat().st_mtime
            except OSError:
                mtimes[str(rel)] = None
        return mtimes

    current = _current_mtimes()
    current["__curriculum_versions__"] = _curriculum_versions_mtime()

    if _SOLVER_NAMESPACE is not None and current == _SOLVER_MTIMES_AT_LOAD:
        return _SOLVER_NAMESPACE

    for key in list(sys.modules):
        if any(key == pfx or key.startswith(pfx + ".") for pfx in _SOLVER_MODULE_PREFIXES):
            del sys.modules[key]

    _SOLVER_NAMESPACE = runpy.run_path(str(solver_script))
    _SOLVER_MTIMES_AT_LOAD = current
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
        p = _attach_curriculum_bundle_from_version(p)
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
    p = _attach_curriculum_bundle_from_version(p)
    p["with_petition"] = bool(with_petition)
    if forced_semester_map is not None:
        p["forced_semester_map"] = forced_semester_map
    return generate_pos(p)


def get_solver_metadata(curriculum_version_id: int | None = None) -> dict:
    """
    Return solver-side metadata for typed violation heuristics.

    curriculum_version_id:
      When set, loads courses / year_standing_rules / max_units_for_sem from that
      approved CurriculumVersion. Otherwise uses the static solver namespace (legacy).

    Returned dict includes ``max_semester`` (planning horizon length) so callers can
    clamp ``current_sem`` before invoking CP-SAT (short DB curricula vs long static grid).
    """
    if curriculum_version_id is not None:
        try:
            from pos.models import CurriculumVersion

            cv = (
                CurriculumVersion.objects.filter(
                    pk=int(curriculum_version_id),
                    status=CurriculumVersion.STATUS_APPROVED,
                )
                .only("approved_json")
                .first()
            )
            if cv and isinstance(cv.approved_json, dict):
                b = cv.approved_json
                raw_caps = b.get("max_units_by_semester_index") or []
                max_semester = len(raw_caps) if isinstance(raw_caps, list) and raw_caps else 0

                def max_units_for_sem(sem_index: int) -> int:
                    if isinstance(sem_index, int) and 1 <= sem_index <= len(raw_caps):
                        return int(raw_caps[sem_index - 1])
                    return 0

                return {
                    "courses": b.get("courses") or {},
                    "year_standing_rules": b.get("year_standing_rules") or {},
                    "prereq_aliases": b.get("prereq_aliases") or {},
                    "max_units_for_sem": max_units_for_sem,
                    "max_semester": int(max_semester),
                    "curriculum_version_id": int(curriculum_version_id),
                }
        except (TypeError, ValueError, Exception):
            pass

    ns = _get_solver_namespace()
    courses = ns.get("courses") or {}
    year_standing_rules = ns.get("YEAR_STANDING_RULES") or {}
    u = ns.get("u")
    max_units_for_sem = None
    max_semester = 0
    try:
        if u is not None and hasattr(u, "max_units_for_sem"):
            max_units_for_sem = u.max_units_for_sem
        if u is not None and hasattr(u, "MAX_SEMESTER"):
            max_semester = int(u.MAX_SEMESTER)
    except Exception:
        max_units_for_sem = None

    return {
        "courses": courses,
        "year_standing_rules": year_standing_rules,
        "max_units_for_sem": max_units_for_sem,
        "max_semester": max_semester,
        "prereq_aliases": ns.get("PREREQ_ALIASES") or {},
        "curriculum_version_id": None,
    }

