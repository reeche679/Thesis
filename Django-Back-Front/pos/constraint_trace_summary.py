"""Roll up flat ``constraint_build_trace`` rows into per-course summaries for DEBUG APIs."""

from __future__ import annotations

from typing import Any


def build_constraint_trace_summary_by_course(
    trace: list[dict[str, Any]] | None,
    final_terms_by_course: dict[str, list[int]] | None,
) -> dict[str, Any]:
    """
    Group trace events by ``course`` key. Attach ``final_candidate_terms_from_diagnostic``
    when provided (from placement diagnostics ``most_constrained_courses`` keyed lookup).
    """
    if not trace:
        return {}
    terms_map = final_terms_by_course or {}
    by_course: dict[str, dict[str, Any]] = {}
    for row in trace:
        if not isinstance(row, dict):
            continue
        c = row.get("course")
        if not isinstance(c, str) or not c:
            continue
        bucket = by_course.setdefault(
            c,
            {
                "course": c,
                "semester_variable": None,
                "offering_constraints": [],
                "unit_cap_constraints": [],
                "prerequisite_constraints": [],
                "corequisite_constraints": [],
                "standing_constraints": [],
            },
        )
        ph = row.get("phase")
        if ph == "semester_variable":
            bucket["semester_variable"] = {k: v for k, v in row.items() if k not in ("seq", "course")}
        elif ph == "offering":
            bucket["offering_constraints"].append({k: v for k, v in row.items() if k not in ("seq", "course", "phase")})
        elif ph == "unit_cap":
            bucket["unit_cap_constraints"].append({k: v for k, v in row.items() if k not in ("seq", "course", "phase")})
        elif ph == "prerequisite":
            bucket["prerequisite_constraints"].append({k: v for k, v in row.items() if k not in ("seq", "course", "phase")})
        elif ph == "corequisite":
            bucket["corequisite_constraints"].append({k: v for k, v in row.items() if k not in ("seq", "course", "phase")})
        elif ph == "year_standing_prefix_units":
            bucket["standing_constraints"].append({k: v for k, v in row.items() if k not in ("seq", "course", "phase")})
    for c, b in by_course.items():
        ft = terms_map.get(c)
        if isinstance(ft, list):
            b["final_candidate_terms_from_diagnostic"] = ft[:25]
    return by_course
