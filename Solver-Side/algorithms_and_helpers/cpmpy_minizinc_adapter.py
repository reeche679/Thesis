"""
CPMpy + MiniZinc adapter for POS generation.
"""

from __future__ import annotations

from typing import Any, Callable
import math


class CpmPyMiniZincUnavailableError(RuntimeError):
    pass


def run_cpmpy_minizinc_search(
    *,
    current_sem: int,
    passed_courses: list[str],
    with_petition: bool,
    valid_map: dict[str, tuple[int, ...]],
    courses: dict[str, dict[str, Any]],
    max_semester: int,
    max_units_for_sem: Callable[[int], int],
    get_req_list: Callable[[dict[str, Any], tuple[str, ...]], list[Any]],
    normalize_code: Callable[[str], str],
    prereq_aliases: dict[str, Any],
    year_standing_rules: dict[str, int],
    enforce_graduation_trio_only_semester: bool,
    graduation_trio: tuple[str, ...],
    solver_name: str = "chuffed",
) -> tuple[dict[str, int], bool, dict[str, Any]]:
    try:
        import cpmpy as cp
    except Exception as exc:
        raise CpmPyMiniZincUnavailableError(
            "CPMpy is not available. Install `cpmpy` and `minizinc`."
        ) from exc

    remaining = [c for c in courses if c not in passed_courses]
    if not remaining:
        return {}, True, {"solver": f"minizinc:{solver_name}", "status": "trivial"}

    # semester variables for remaining courses
    sem = {
        c: cp.intvar(current_sem, max_semester, name=f"sem_{c.replace(' ', '_')}")
        for c in remaining
    }
    model = cp.Model()

    # domain constraints from valid_map (already includes modulo/category logic)
    for c in remaining:
        allowed = tuple(valid_map.get(c) or ())
        if not allowed:
            return {}, False, {"solver": f"minizinc:{solver_name}", "status": "no_domain", "course": c}
        model += cp.sum([sem[c] == s for s in allowed]) >= 1

    # per-semester unit limits
    for s in range(current_sem, max_semester + 1):
        model += cp.sum(
            [int(courses[c].get("units", 0)) * (sem[c] == s) for c in remaining]
        ) <= int(max_units_for_sem(s))

    # graduation trio constraint
    if enforce_graduation_trio_only_semester:
        trio = [c for c in graduation_trio if c in sem]
        if len(trio) >= 2:
            ref = trio[0]
            for c in trio[1:]:
                model += sem[c] == sem[ref]
            for c in remaining:
                if c in graduation_trio:
                    continue
                model += sem[c] != sem[ref]

    code_index = {normalize_code(c): c for c in courses}
    prereq_alias_map = {normalize_code(k): v for k, v in prereq_aliases.items()}
    standing_map = {normalize_code(k): v for k, v in year_standing_rules.items()}

    def resolve_course(token: Any) -> str | None:
        return code_index.get(normalize_code(token)) if isinstance(token, str) else None

    def resolve_standing(token: Any) -> int | None:
        return standing_map.get(normalize_code(token)) if isinstance(token, str) else None

    def sem_expr(course_code: str):
        if course_code in sem:
            return sem[course_code]
        return 0

    def add_year_standing(course_code: str):
        info_c = courses.get(course_code, {})
        orig_sem_c = info_c.get("orig_sem")
        if orig_sem_c is None:
            return None

        prefix = []
        total_units = 0
        earned_fixed = 0
        for cc, info in courses.items():
            orig = info.get("orig_sem")
            if orig is None or orig >= orig_sem_c:
                continue
            units = int(info.get("units", 0))
            total_units += units
            if cc in passed_courses:
                earned_fixed += units
            else:
                prefix.append((cc, units))
        if total_units <= 0:
            return None
        need = math.ceil(0.9 * total_units) - earned_fixed
        if need <= 0:
            return None
        lhs_terms = []
        for cc, units in prefix:
            lhs_terms.append(units * (sem_expr(cc) < sem_expr(course_code)))
        if not lhs_terms:
            return None
        return cp.sum(lhs_terms) >= need

    # prereq + coreq
    for c in remaining:
        info = courses.get(c, {})
        prereqs = get_req_list(info, ("pre-requisites", "pre_requisites", "prerequisites", "pre-reqs"))
        for prereq in prereqs:
            if isinstance(prereq, str):
                alias = prereq_alias_map.get(normalize_code(prereq))
                if alias is not None:
                    prereq = alias

            if isinstance(prereq, (list, tuple)):
                options = []
                for alt in prereq:
                    alt_course = resolve_course(alt)
                    alt_stand = resolve_standing(alt)
                    if alt_course is not None:
                        options.append(sem_expr(c) > sem_expr(alt_course))
                    elif alt_stand is not None:
                        ys = add_year_standing(c)
                        if ys is not None:
                            options.append(ys)
                if options:
                    model += cp.any(options)
            else:
                pre_course = resolve_course(prereq)
                pre_stand = resolve_standing(prereq)
                if pre_course is not None:
                    model += sem_expr(c) > sem_expr(pre_course)
                elif pre_stand is not None:
                    ys = add_year_standing(c)
                    if ys is not None:
                        model += ys

        coreqs = get_req_list(info, ("co-requisites", "co_requisites", "corequisites", "co-reqs"))
        for coreq in coreqs:
            co_course = resolve_course(coreq)
            if co_course is not None:
                model += sem_expr(c) >= sem_expr(co_course)

    max_var = cp.intvar(current_sem, max_semester, name="max_sem")
    for c in remaining:
        model += max_var >= sem[c]
    sum_var = cp.sum([sem[c] for c in remaining])
    model.minimize((1000 * max_var) + sum_var)

    solver_id = f"minizinc:{solver_name}"
    try:
        ok = model.solve(solver=solver_id)
    except Exception as exc:
        raise CpmPyMiniZincUnavailableError(
            f"MiniZinc backend '{solver_name}' unavailable. Ensure MiniZinc and solver are installed."
        ) from exc

    if not ok:
        return {}, False, {"solver": solver_id, "status": "infeasible"}

    out_state = {c: int(sem[c].value()) for c in remaining}
    return out_state, True, {"solver": solver_id, "status": "ok"}
