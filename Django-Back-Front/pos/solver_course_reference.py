"""
Solver course ``category`` (Rigid / Bottleneck / Flexible) for staff-approved bundles.

Staff set categories on the curriculum review grid. Each course defaults to **Rigid**
unless the row carries an explicit valid category. The solver runtime
(``curriculum_runtime``) can still backfill missing/invalid categories from static
``courses.py`` when a bundle is loaded without going through staff review.
"""

from __future__ import annotations

import sys
from pathlib import Path

_VALID_CATEGORIES = frozenset({"Rigid", "Bottleneck", "Flexible"})

_CATEGORY_BY_CODE: dict[str, str] | None = None


def _thesis_root() -> Path:
    # pos/solver_course_reference.py -> parents[2] == Thesis repo root
    return Path(__file__).resolve().parents[2]


def _ensure_solver_on_path() -> Path | None:
    root = _thesis_root() / "Solver-Side"
    if not root.is_dir():
        return None
    s = str(root.resolve())
    if s not in sys.path:
        sys.path.insert(0, s)
    return root


def get_static_category_by_code() -> dict[str, str]:
    """Return a copy of ``{ course_code: category }`` from static ``courses.py``."""
    global _CATEGORY_BY_CODE
    if _CATEGORY_BY_CODE is not None:
        return dict(_CATEGORY_BY_CODE)
    out: dict[str, str] = {}
    try:
        if _ensure_solver_on_path() is None:
            _CATEGORY_BY_CODE = {}
            return {}
        from courses_and_year_standings.courses import courses as static_courses

        for k, v in static_courses.items():
            if not isinstance(v, dict):
                continue
            c = v.get("category")
            if isinstance(c, str) and c.strip() in _VALID_CATEGORIES:
                out[str(k).strip()] = c.strip()
    except Exception:
        out = {}
    _CATEGORY_BY_CODE = out
    return dict(out)


def resolve_course_category(code: str, explicit: str | None = None) -> str:
    """
    Return the staff-chosen category, falling back to the static ``courses.py``
    value when no valid explicit category is provided.

    ``explicit`` comes from the review row ``category`` field (staff-editable).
    """
    if explicit is not None:
        e = str(explicit).strip()
        if e in _VALID_CATEGORIES:
            return e
    return get_static_category_by_code().get(str(code).strip(), "Rigid")
