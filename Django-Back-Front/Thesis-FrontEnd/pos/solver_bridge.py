from pathlib import Path
import runpy

from django.conf import settings

_SOLVER_NAMESPACE = None


def _get_solver_namespace():
    """
    Lazily load the CP-SAT solver module (CP-SAT.py) and cache its namespace.

    The solver lives in the sibling 'Solver-Side/Main-Solver/CP-SAT.py' folder
    relative to the Django project root.
    """
    global _SOLVER_NAMESPACE
    if _SOLVER_NAMESPACE is not None:
        return _SOLVER_NAMESPACE

    # settings.BASE_DIR points to 'Django-Front-Back'.
    project_root = Path(settings.BASE_DIR).parent
    solver_script = project_root / "Solver-Side" / "Main-Solver" / "CP-SAT.py"
    if not solver_script.is_file():
        raise RuntimeError(f"Solver script not found at {solver_script}")

    # run_path executes CP-SAT.py once and returns its globals as a dict.
    _SOLVER_NAMESPACE = runpy.run_path(str(solver_script))
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
    return generate_pos(payload)

