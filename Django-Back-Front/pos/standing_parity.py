"""
Compare Django validate_pos grade-table standing check vs CP-SAT prefix standing math.

Django (views.validate_pos) only applies the 90% prefix rule to historical *passed* rows
(passed_occurrences). This module adds hypothetical checks at term T using the same
counting rule Django uses: earned prefix units from passes with pass_sem < T.
"""

from __future__ import annotations

import math
from typing import Any, Callable

from .curriculum_static_feasibility import _get_req_list


def _norm(s: str) -> str:
    return " ".join(str(s).strip().split()).upper()


def _first_semester(info: dict[str, Any] | None) -> int | None:
    if not isinstance(info, dict):
        return None
    for key in ("orig_sem", "orig_semester", "original_semester"):
        v = info.get(key)
        if v is None or (isinstance(v, str) and not str(v).strip()):
            continue
        try:
            return int(v)
        except (TypeError, ValueError):
            continue
    return None


def django_hypothetical_prefix_standing_ok(
    *,
    course: str,
    hypothetical_course_sem_t: int,
    courses: dict[str, Any],
    year_standing_rules: dict[str, Any],
    passed_sem_by_solver: dict[str, set[int]],
    prereq_aliases: dict[str, Any] | None,
) -> tuple[bool, str, float, float, float]:
    """
    True if every year-standing token on ``course`` would pass Django's formula
    when this course is assumed taken at solver semester index ``hypothetical_course_sem_t``.
    Returns (ok, reason, total_prefix, earned_prefix, required_prefix).
    """
    info = courses.get(course)
    if not isinstance(info, dict):
        return True, "no_course_meta", 0.0, 0.0, 0.0
    ysn = {_norm(str(k)) for k in (year_standing_rules or {})}
    pre_list = _get_req_list(
        info,
        ("pre-requisites", "pre_requisites", "prerequisites", "pre-reqs"),
    )
    has_standing = False
    for pre in pre_list:
        if isinstance(pre, (list, tuple)):
            for alt in pre:
                if isinstance(alt, str) and _norm(alt) in ysn:
                    has_standing = True
                    break
        elif isinstance(pre, str):
            tok = pre.strip()
            if prereq_aliases:
                alias = prereq_aliases.get(_norm(tok))
                if isinstance(alias, str):
                    tok = alias
            if _norm(tok) in ysn:
                has_standing = True
                break
    if not has_standing:
        return True, "no_year_standing_token_on_course", 0.0, 0.0, 0.0

    try:
        orig_sem_c_i = int(info.get("orig_sem"))
    except (TypeError, ValueError):
        return True, "no_orig_sem_for_standing_row", 0.0, 0.0, 0.0

    total_units_prefix = 0.0
    earned_units_prefix = 0.0
    for pc, pinfo in courses.items():
        if not isinstance(pinfo, dict):
            continue
        p_orig = pinfo.get("orig_sem")
        if p_orig is None:
            continue
        try:
            if int(p_orig) >= orig_sem_c_i:
                continue
        except (TypeError, ValueError):
            continue
        try:
            units = float(pinfo.get("units", 0) or 0)
        except (TypeError, ValueError):
            units = 0.0
        total_units_prefix += units
        pre_passes = passed_sem_by_solver.get(str(pc)) or set()
        if any(pre_sem < hypothetical_course_sem_t for pre_sem in pre_passes):
            earned_units_prefix += units

    required = float(math.ceil(0.9 * total_units_prefix)) if total_units_prefix > 0 else 0.0
    ok = earned_units_prefix >= required if total_units_prefix > 0 else True
    reason = "django_hypothetical_pass_prefix_rule"
    if not ok:
        reason = "django_hypothetical_prefix_insufficient"
    return ok, reason, total_units_prefix, earned_units_prefix, required


def cp_sat_prefix_standing_numbers(
    course: str,
    courses: dict[str, Any],
    passed_courses: list[str],
    first_semester_fn: Callable[[dict[str, Any] | None], int | None] | None = None,
) -> tuple[float, float, float, float]:
    """Same earned_fixed slice as CP-SAT add_year_standing (passed prefix only)."""
    fs = first_semester_fn or _first_semester
    passed_set = {str(c) for c in (passed_courses or []) if c}
    info_c = courses.get(course)
    if not isinstance(info_c, dict):
        return 0.0, 0.0, 0.0, 0.0
    orig_sem_c = fs(info_c)
    if orig_sem_c is None:
        return 0.0, 0.0, 0.0, 0.0
    try:
        os_c = int(orig_sem_c)
    except (TypeError, ValueError):
        return 0.0, 0.0, 0.0, 0.0

    total_units = 0.0
    earned_fixed = 0.0
    for code, info in courses.items():
        if not isinstance(info, dict):
            continue
        orig_sem = fs(info)
        if orig_sem is None:
            continue
        try:
            oi = int(orig_sem)
        except (TypeError, ValueError):
            continue
        if oi >= os_c:
            continue
        try:
            u = float(info.get("units", 0) or 0)
        except (TypeError, ValueError):
            u = 0.0
        total_units += u
        if str(code) in passed_set:
            earned_fixed += u

    if total_units <= 0:
        return 0.0, 0.0, 0.0, 0.0
    req = float(math.ceil(0.9 * total_units))
    remaining_need = max(0.0, req - earned_fixed)
    return total_units, earned_fixed, req, remaining_need


def build_standing_logic_mismatch_rows(
    *,
    courses: dict[str, Any],
    passed_courses: list[str],
    failed_courses: list[str],
    current_sem: int,
    max_semester: int,
    year_standing_rules: dict[str, Any],
    prereq_aliases: dict[str, Any],
    passed_sem_by_solver: dict[str, set[int]],
    most_constrained_courses: list[dict[str, Any]],
    max_rows: int = 15,
) -> list[dict[str, Any]]:
    """
    Rows highlight: hypothetical Django OK on passes-only at T but CP-SAT still needs
    remaining prefix units scheduled before taking the course (remaining_need > 0).
    """
    passed_set = {str(c) for c in (passed_courses or []) if c}
    remaining = [str(c) for c in courses if str(c) not in passed_set]

    # Candidate (course, term) pairs: up to 3 terms per most-constrained head + scan remaining with standing
    pairs: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for row in most_constrained_courses or []:
        c = str(row.get("course") or "")
        for t in (row.get("final_candidate_terms") or [])[:5]:
            if isinstance(t, int) and (c, t) not in seen:
                seen.add((c, t))
                pairs.append((c, t))
    for c in remaining[:40]:
        if not _course_has_year_standing_token(c, courses, year_standing_rules, prereq_aliases):
            continue
        for t in range(current_sem, min(max_semester, current_sem + 4) + 1):
            if (c, t) not in seen:
                seen.add((c, t))
                pairs.append((c, t))

    out_rows: list[dict[str, Any]] = []
    for course, term in pairs:
        dj_ok, dj_reason, tot, ear, req = django_hypothetical_prefix_standing_ok(
            course=course,
            hypothetical_course_sem_t=term,
            courses=courses,
            year_standing_rules=year_standing_rules,
            passed_sem_by_solver=passed_sem_by_solver,
            prereq_aliases=prereq_aliases,
        )
        tot2, ear2, req2, rem_need = cp_sat_prefix_standing_numbers(course, courses, passed_courses)
        cp_fixed_ok = rem_need <= 0
        if rem_need <= 0 or not dj_ok:
            continue
        out_rows.append(
            {
                "course": course,
                "term": int(term),
                "django_actual_validate_pos_scope": "grade_table_pass_history_only_no_future_placement_check",
                "django_hypothetical_prefix_ok_at_term": bool(dj_ok),
                "django_hypothetical_reason": dj_reason,
                "cp_sat_prefix_total_units": float(tot2),
                "cp_sat_earned_fixed_passed_prefix_units": float(ear2),
                "cp_sat_required_prefix_units": float(req2),
                "cp_sat_remaining_prefix_units_to_schedule": float(rem_need),
                "cp_sat_standing_fixed_part_satisfied_by_passes_only": bool(cp_fixed_ok),
                "difference_flag_passes_ok_but_solver_needs_scheduled_prefix_units": bool(dj_ok and rem_need > 0),
            }
        )
        if len(out_rows) >= max_rows:
            break

    out_rows.sort(key=lambda r: (-float(r.get("cp_sat_remaining_prefix_units_to_schedule") or 0), str(r.get("course"))))
    return out_rows[:max_rows]


def _course_has_year_standing_token(
    course: str,
    courses: dict[str, Any],
    year_standing_rules: dict[str, Any],
    prereq_aliases: dict[str, Any] | None,
) -> bool:
    ysn = {_norm(str(k)) for k in (year_standing_rules or {})}
    info = courses.get(course)
    if not isinstance(info, dict):
        return False
    for prereq in _get_req_list(
        info,
        ("pre-requisites", "pre_requisites", "prerequisites", "pre-reqs"),
    ):
        if isinstance(prereq, (list, tuple)):
            for alt in prereq:
                if isinstance(alt, str) and _norm(alt) in ysn:
                    return True
        elif isinstance(prereq, str):
            tok = prereq.strip()
            if prereq_aliases:
                al = prereq_aliases.get(_norm(tok))
                if isinstance(al, str):
                    tok = al
            if _norm(str(tok)) in ysn:
                return True
    return False
