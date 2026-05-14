"""
Phase 6 — map positional term bands (term_index + stable source order) to orig_sem (1-based).
"""

from __future__ import annotations

from typing import Any


def _row_sort_key(row: dict[str, Any]) -> tuple[int, int, int, int]:
    ti = int(row.get("term_index") or 0)
    src = row.get("source") or {}
    page = int(src.get("page") or 0)
    table = int(src.get("table") or 0)
    ri = int(src.get("row_index") or 0)
    # Rows with no term_index sort after real bands so their band follows PDF order among unknowns.
    ti_sort = ti if ti > 0 else 10**9
    return (ti_sort, page, table, ri)


def term_index_to_orig_sem_map(rows: list[dict[str, Any]]) -> dict[int, int]:
    """
    First distinct term_index values in (term_index, page, table, row_index) order
    map to orig_sem 1, 2, 3, ...
    """
    ordered: list[dict[str, Any]] = sorted(rows, key=_row_sort_key)
    seen: list[int] = []
    for r in ordered:
        ti = int(r.get("term_index") or 0)
        if ti not in seen:
            seen.append(ti)
    return {t: i + 1 for i, t in enumerate(seen)}


def orig_sem_suggested_for_row(row: dict[str, Any], ti_map: dict[int, int]) -> int:
    ti = int(row.get("term_index") or 0)
    return int(ti_map.get(ti, 1))


def orig_sem_for_course_row(
    row: dict[str, Any],
    ti_map: dict[int, int],
    *,
    draft_analysis_row: dict[str, Any] | None = None,
) -> int:
    """Hybrid: per-row orig_sem / orig_sem_override wins; else analyzer suggestion; else band default."""
    for key in ("orig_sem_override", "orig_sem"):
        if key in row and row[key] is not None and str(row[key]).strip() != "":
            try:
                v = int(row[key])
                return max(1, v)
            except (TypeError, ValueError):
                pass
    if draft_analysis_row:
        try:
            v = int(draft_analysis_row.get("orig_sem_suggested") or 0)
            if v >= 1:
                return v
        except (TypeError, ValueError):
            pass
    return orig_sem_suggested_for_row(row, ti_map)
