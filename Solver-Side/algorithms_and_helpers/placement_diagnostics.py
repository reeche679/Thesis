"""
Coarse placement / capacity diagnostics for CP-SAT curriculum models.

Does NOT call the solver. Offering domains use the caller-provided
``valid_semesters_fn`` (must match CP-SAT ``_valid_semesters_for_course``).

TODO: If bottleneck diagnostics are still inconclusive, add CP-SAT assumption literals
around major constraint groups: offering rules, unit caps, prerequisite rules,
corequisite rules, year-standing rules, specific high-risk course groups. Avoid one
assumption per course unless each guards meaningful constraints, or UNSAT cores are useless.
"""

from __future__ import annotations

import math
from typing import Any, Callable


def _int_units(info: dict[str, Any] | None) -> int:
    if not isinstance(info, dict):
        return 0
    try:
        return int(round(float(info.get("units", 0) or 0)))
    except (TypeError, ValueError):
        return 0


def _build_code_index(courses: dict[str, Any]) -> dict[str, str]:
    from .pos_helpers import _normalize_code

    return {_normalize_code(str(k)): str(k) for k in courses}


def _resolve_course_token(
    token: str,
    code_index: dict[str, str],
    prereq_aliases: dict[str, Any],
) -> str | None:
    from .pos_helpers import _normalize_code

    t = str(token).strip()
    if not t:
        return None
    for k, v in (prereq_aliases or {}).items():
        if _normalize_code(str(k)) != _normalize_code(t):
            continue
        if isinstance(v, str):
            return code_index.get(_normalize_code(v.strip()))
        if isinstance(v, (list, tuple)) and v:
            return code_index.get(_normalize_code(str(v[0]).strip()))
    return code_index.get(_normalize_code(t))


def _prefix_standing_numbers(
    course: str,
    courses: dict[str, Any],
    passed_set: set[str],
    first_semester_fn: Callable[[dict[str, Any] | None], int | None],
) -> tuple[float, float, float, int]:
    """
    Same prefix set and 90% rule as CP-SAT ``add_year_standing`` earned_fixed slice:
    total_units, earned_fixed (passed prefix only), required, remaining_needed.
    """
    info_c = courses.get(course)
    if not isinstance(info_c, dict):
        return 0.0, 0.0, 0.0, 0
    orig_sem_c = first_semester_fn(info_c)
    if orig_sem_c is None:
        return 0.0, 0.0, 0.0, 0
    try:
        os_c = int(orig_sem_c)
    except (TypeError, ValueError):
        return 0.0, 0.0, 0.0, 0

    total_units = 0.0
    earned_fixed = 0.0
    for code, info in courses.items():
        if not isinstance(info, dict):
            continue
        o = first_semester_fn(info)
        if o is None:
            continue
        try:
            oi = int(o)
        except (TypeError, ValueError):
            continue
        if oi >= os_c:
            continue
        u = float(_int_units(info))
        total_units += u
        if code in passed_set:
            earned_fixed += u

    if total_units <= 0:
        return 0.0, 0.0, 0.0, 0
    req = float(math.ceil(0.9 * total_units))
    return total_units, earned_fixed, req, os_c


def _course_has_year_standing_prereq(
    course: str,
    courses: dict[str, Any],
    year_standing_rules: dict[str, Any],
    get_req_list: Callable[..., list[Any]],
    code_index: dict[str, str],
    prereq_aliases: dict[str, Any],
) -> bool:
    from .pos_helpers import _normalize_code

    ysn = {_normalize_code(str(k)) for k in (year_standing_rules or {})}
    info = courses.get(course)
    if not isinstance(info, dict):
        return False
    for prereq in get_req_list(
        info,
        ("pre-requisites", "pre_requisites", "prerequisites", "pre-reqs"),
    ):
        if isinstance(prereq, (list, tuple)):
            for alt in prereq:
                if not isinstance(alt, str):
                    continue
                if _normalize_code(alt) in ysn:
                    return True
        elif isinstance(prereq, str):
            tok = prereq
            alias = prereq_aliases.get(_normalize_code(tok)) if prereq_aliases else None
            if isinstance(alias, str):
                tok = alias
            if _normalize_code(str(tok)) in ysn:
                return True
    return False


def _list_simple_prereqs_and_coreqs(
    course: str,
    courses: dict[str, Any],
    get_req_list: Callable[..., list[Any]],
    code_index: dict[str, str],
    prereq_aliases: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """AND-only simple string prereqs and coreqs resolved to curriculum keys."""
    from .pos_helpers import _normalize_code

    info = courses.get(course)
    prereqs: list[str] = []
    coreqs: list[str] = []
    if not isinstance(info, dict):
        return prereqs, coreqs
    for prereq in get_req_list(
        info,
        ("pre-requisites", "pre_requisites", "prerequisites", "pre-reqs"),
    ):
        if isinstance(prereq, (list, tuple)):
            continue
        if not isinstance(prereq, str):
            continue
        tok = prereq.strip()
        if not tok:
            continue
        alias = prereq_aliases.get(_normalize_code(tok)) if prereq_aliases else None
        if isinstance(alias, str):
            tok = alias
        pc = _resolve_course_token(tok, code_index, prereq_aliases)
        if pc:
            prereqs.append(pc)
    for coreq in get_req_list(
        info,
        ("co-requisites", "co_requisites", "corequisites", "co-reqs"),
    ):
        if not isinstance(coreq, str):
            continue
        cc = _resolve_course_token(coreq.strip(), code_index, prereq_aliases)
        if cc:
            coreqs.append(cc)
    return prereqs, coreqs


def _compute_lb_earliest_semesters(
    remaining: list[str],
    courses: dict[str, Any],
    passed_set: set[str],
    current_sem: int,
    max_semester: int,
    valid_semesters_fn: Callable[[str, dict[str, Any]], list[int]],
    get_req_list: Callable[..., list[Any]],
    code_index: dict[str, str],
    prereq_aliases: dict[str, Any],
    first_semester_fn: Callable[[dict[str, Any] | None], int | None],
) -> dict[str, int]:
    """ASAP lower bound on semester index (AND prereqs + coreqs + offering min). Coarse; skips OR-prereqs."""
    LB: dict[str, int] = {}
    for c in remaining:
        info = courses.get(c)
        vs = valid_semesters_fn(c, info) if isinstance(info, dict) else []
        if not vs:
            LB[c] = max_semester + 1
        else:
            LB[c] = min(int(x) for x in vs)

    for _ in range(len(remaining) + 3):
        changed = False
        for c in remaining:
            info = courses.get(c)
            if not isinstance(info, dict):
                continue
            pre, core = _list_simple_prereqs_and_coreqs(c, courses, get_req_list, code_index, prereq_aliases)
            for p in pre:
                if p in passed_set:
                    continue
                if p in LB:
                    nv = LB[p] + 1
                    if nv > LB.get(c, current_sem):
                        LB[c] = nv
                        changed = True
            for k in core:
                if k in passed_set:
                    continue
                if k in LB:
                    nv = LB[k]
                    if nv > LB.get(c, current_sem):
                        LB[c] = nv
                        changed = True
        if not changed:
            break
    return LB


def _capacity_sum(
    lo: int,
    hi: int,
    max_units_for_sem: Callable[[int], int],
) -> int:
    s = 0
    for t in range(lo, hi + 1):
        try:
            s += int(max_units_for_sem(t))
        except Exception:
            s += 0
    return s


def run_placement_diagnostics(
    *,
    courses: dict[str, Any],
    passed_courses: list[str],
    failed_courses: list[str],
    current_sem: int,
    with_petition: bool,
    max_semester: int,
    max_units_for_sem: Callable[[int], int],
    valid_semesters_fn: Callable[[str, dict[str, Any]], list[int]],
    first_semester_fn: Callable[[dict[str, Any] | None], int | None],
    normalize_code: Callable[[str], str],
    get_req_list: Callable[..., list[Any]],
    prereq_aliases: dict[str, Any],
    year_standing_rules: dict[str, Any],
    configuration: str,
) -> dict[str, Any]:
    passed_set = {str(c) for c in (passed_courses or []) if c}
    failed_list = [str(c) for c in (failed_courses or []) if c]
    failed_set = frozenset(failed_list)
    remaining = [str(c) for c in courses if str(c) not in passed_set]
    code_index = _build_code_index(courses)

    notes: list[str] = [
        "OR-prerequisite groups ignored in LB and prereq_possible_terms (coarse).",
        "Year-standing allowed terms use capacity-before-t surrogate when remaining_need>0 (approximate).",
        "Term bottleneck blocked_* buckets are mutually exclusive with priority: offering, standing, prereq, coreq.",
    ]

    total_remaining_units = sum(_int_units(courses.get(c)) for c in remaining)
    total_available_capacity = _capacity_sum(current_sem, max_semester, max_units_for_sem)
    global_capacity_ok = total_remaining_units <= total_available_capacity

    LB = _compute_lb_earliest_semesters(
        remaining,
        courses,
        passed_set,
        current_sem,
        max_semester,
        valid_semesters_fn,
        get_req_list,
        code_index,
        prereq_aliases,
        first_semester_fn,
    )

    suffix_checks: list[dict[str, Any]] = []
    for t in range(current_sem, max_semester + 1):
        cannot_before = sum(
            _int_units(courses.get(c)) for c in remaining if LB.get(c, max_semester + 1) >= t
        )
        cap_from_t = _capacity_sum(t, max_semester, max_units_for_sem)
        suffix_checks.append(
            {
                "from_term": int(t),
                "remaining_units_that_cannot_be_before_t": int(cannot_before),
                "capacity_from_t_to_end": int(cap_from_t),
                "ok": bool(cannot_before <= cap_from_t),
            }
        )

    # Per-course explain
    per: dict[str, dict[str, Any]] = {}
    for c in remaining:
        info = courses.get(c)
        if not isinstance(info, dict):
            continue
        off = sorted(int(x) for x in valid_semesters_fn(c, info))
        off_set = set(off)

        tot_p, ear_p, req_p, _os_c = _prefix_standing_numbers(c, courses, passed_set, first_semester_fn)
        rem_need = max(0.0, float(req_p) - float(ear_p))

        standing_terms: list[int] = []
        if rem_need <= 0:
            standing_terms = list(off)
        else:
            for t in off:
                cap_b = _capacity_sum(current_sem, t - 1, max_units_for_sem) if t > current_sem else 0
                if cap_b >= rem_need:
                    standing_terms.append(int(t))

        pre_list, core_list = _list_simple_prereqs_and_coreqs(c, courses, get_req_list, code_index, prereq_aliases)
        lb = LB.get(c, min(off) if off else max_semester + 1)
        pre_ok = [t for t in off if t >= lb]
        core_lb = lb
        for k in core_list:
            if k in passed_set:
                continue
            core_lb = max(core_lb, LB.get(k, current_sem))
        core_ok = [t for t in off if t >= core_lb]

        final_set = off_set.intersection(set(standing_terms)).intersection(set(pre_ok)).intersection(set(core_ok))
        final_sorted = sorted(final_set)

        raw_pre = get_req_list(
            info,
            ("pre-requisites", "pre_requisites", "prerequisites", "pre-reqs"),
        )
        raw_co = get_req_list(
            info,
            ("co-requisites", "co_requisites", "corequisites", "co-reqs"),
        )

        per[c] = {
            "course": c,
            "units": _int_units(info),
            "offering_allowed_terms": off,
            "standing_allowed_terms": standing_terms,
            "prereq_possible_terms": pre_ok,
            "coreq_possible_terms": core_ok,
            "final_candidate_terms": final_sorted,
            "blocked_terms": {
                "offering": [],
                "standing": sorted(off_set - set(standing_terms)),
                "prereq": sorted(off_set - set(pre_ok)),
                "coreq": sorted(off_set - set(core_ok)),
            },
            "raw_prerequisites": raw_pre,
            "raw_corequisites": raw_co,
            "standing_requirement": {
                "prefix_total_units": tot_p,
                "earned_fixed_passed_prefix_units": ear_p,
                "required_prefix_units": req_p,
                "remaining_need_units": rem_need,
                "approximation": "standing_allowed_terms uses sum(max_units) before t vs remaining_need when remaining_need>0",
            },
        }

    # Fix blocked_terms.offering: terms where course cannot be placed for offering alone
    for c in remaining:
        d = per.get(c)
        if not d:
            continue
        off = set(d["offering_allowed_terms"])
        all_terms = set(range(current_sem, max_semester + 1))
        d["blocked_terms"]["offering"] = sorted(all_terms - off)

    ranked = sorted(
        (per[c] for c in remaining if c in per),
        key=lambda row: (len(row["final_candidate_terms"]), row["course"]),
    )
    most_constrained = []
    for row in ranked[:15]:
        most_constrained.append(
            {
                "course": row["course"],
                "units": row["units"],
                "n_final_candidate_terms": len(row["final_candidate_terms"]),
                "final_candidate_terms": row["final_candidate_terms"][:20],
                "raw_prerequisites": row["raw_prerequisites"],
                "raw_corequisites": row["raw_corequisites"],
            }
        )

    term_rows: list[dict[str, Any]] = []
    for t in range(current_sem, max_semester + 1):
        try:
            cap = int(max_units_for_sem(t))
        except Exception:
            cap = 0
        off_c = [c for c in remaining if t in set(per[c]["offering_allowed_terms"])]
        off_u = sum(_int_units(courses.get(c)) for c in off_c)

        st_c = [c for c in remaining if t in set(per[c]["standing_allowed_terms"])]
        st_u = sum(_int_units(courses.get(c)) for c in st_c)

        pr_c = [c for c in remaining if t in set(per[c]["prereq_possible_terms"])]
        pr_u = sum(_int_units(courses.get(c)) for c in pr_c)

        cq_c = [c for c in remaining if t in set(per[c]["coreq_possible_terms"])]
        cq_u = sum(_int_units(courses.get(c)) for c in cq_c)

        full_c = [c for c in remaining if t in set(per[c]["final_candidate_terms"])]
        full_u = sum(_int_units(courses.get(c)) for c in full_c)

        bo = [c for c in remaining if c not in off_c]
        bs = [c for c in off_c if c not in st_c]
        bp = [c for c in st_c if c not in pr_c]
        bc = [c for c in pr_c if c not in cq_c]

        term_rows.append(
            {
                "term": int(t),
                "unit_cap": cap,
                "offering_eligible_course_count": len(off_c),
                "offering_eligible_units": int(off_u),
                "standing_eligible_course_count": len(st_c),
                "standing_eligible_units": int(st_u),
                "prereq_possible_course_count": len(pr_c),
                "prereq_possible_units": int(pr_u),
                "coreq_possible_course_count": len(cq_c),
                "coreq_possible_units": int(cq_u),
                "fully_placeable_course_count": len(full_c),
                "fully_placeable_units": int(full_u),
                "blocked_by_offering_count": len(bo),
                "blocked_by_standing_count": len(bs),
                "blocked_by_prereq_count": len(bp),
                "blocked_by_coreq_count": len(bc),
            }
        )

    return {
        "diagnostics_version": 1,
        "configuration": configuration,
        "current_sem": int(current_sem),
        "max_semester": int(max_semester),
        "notes": notes,
        "capacity_feasibility_snapshot": {
            "total_remaining_units": int(total_remaining_units),
            "total_available_capacity": int(total_available_capacity),
            "global_capacity_ok": bool(global_capacity_ok),
            "suffix_checks": suffix_checks,
        },
        "term_bottleneck_report": term_rows,
        "most_constrained_courses": most_constrained,
    }
