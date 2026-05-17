"""
Static curriculum checks (no CP-SAT) to narrow down infeasibility causes.

Used from validate_pos (DEBUG) when the solver returns INFEASIBLE: prereq typos,
missing tokens, obvious cycles among *remaining* courses, and a conservative
sequential-chain length vs planning horizon.
"""

from __future__ import annotations

from collections import deque
from typing import Any


def _norm_code(code: str) -> str:
    return " ".join(str(code).strip().split()).upper()


def _get_req_list(info: dict[str, Any], keys: tuple[str, ...]) -> list[Any]:
    for key in keys:
        if key not in info:
            continue
        val = info.get(key)
        if val is None:
            return []
        if isinstance(val, (list, tuple, set)):
            return list(val)
        if isinstance(val, str):
            return [val]
        return []
    return []


def _resolve_alias(prereq_aliases: dict[str, Any], token: str) -> str:
    """Follow one hop of alias map (CpEE 401 -> Elective Course 1 style)."""
    t = str(token).strip()
    if not t:
        return t
    key = _norm_code(t)
    for k, v in (prereq_aliases or {}).items():
        if _norm_code(str(k)) != key:
            continue
        if isinstance(v, str):
            return v.strip()
        if isinstance(v, (list, tuple)) and v:
            return str(v[0]).strip()
    return t


def _resolve_course_token(
    token: str,
    code_index: dict[str, str],
    prereq_aliases: dict[str, Any],
) -> str | None:
    t = _resolve_alias(prereq_aliases, token)
    if not t:
        return None
    return code_index.get(_norm_code(t))


def static_prereq_horizon_diagnosis(
    *,
    courses: dict[str, Any],
    passed_courses: list[str],
    failed_courses: list[str],
    current_sem: int,
    max_semester: int,
    prereq_aliases: dict[str, Any] | None,
    year_standing_rules: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Returns a JSON-serializable dict. Does not prove CP-SAT feasibility;
    it flags common curriculum data issues and a coarse horizon lower bound.
    """
    pa = prereq_aliases if isinstance(prereq_aliases, dict) else {}
    ysr = year_standing_rules if isinstance(year_standing_rules, dict) else {}
    ysr_norm = {_norm_code(str(k)): str(k) for k in ysr}

    passed_set = {str(c) for c in (passed_courses or []) if c}
    failed_set = {str(c) for c in (failed_courses or []) if c}
    # Remaining = anything not passed (includes failed retakes).
    remaining = [str(c) for c in courses if str(c) not in passed_set]
    rem_set = set(remaining)

    code_index: dict[str, str] = {}
    for k in courses:
        code_index[_norm_code(str(k))] = str(k)

    unknown_prereq_tokens: list[dict[str, Any]] = []
    or_groups_unresolved: list[dict[str, Any]] = []
    edges: list[tuple[str, str]] = []  # u -> v means u must be scheduled before v (both remaining)

    def is_year_standing_token(tok: str) -> bool:
        s = str(tok).strip()
        if not s:
            return False
        return _norm_code(s) in ysr_norm

    for c in remaining:
        info = courses.get(c)
        if not isinstance(info, dict):
            continue
        for prereq in _get_req_list(
            info,
            ("pre-requisites", "pre_requisites", "prerequisites", "pre-reqs"),
        ):
            if isinstance(prereq, (list, tuple)):
                # OR group: do not add edges (would over-constrain). Only detect
                # groups that resolve to neither a known course branch nor year standing.
                has_course = False
                has_standing = False
                for alt in prereq:
                    if not isinstance(alt, str) or not alt.strip():
                        continue
                    if is_year_standing_token(alt):
                        has_standing = True
                        continue
                    pc = _resolve_course_token(alt, code_index, pa)
                    if pc and (pc in rem_set or pc in passed_set):
                        has_course = True
                if not has_course and not has_standing:
                    or_groups_unresolved.append({"course": c, "alternatives": list(prereq)})
            else:
                if not isinstance(prereq, str) or not prereq.strip():
                    continue
                if is_year_standing_token(prereq):
                    continue
                pc = _resolve_course_token(prereq, code_index, pa)
                if pc is None:
                    unknown_prereq_tokens.append({"course": c, "token": prereq, "branch": "AND"})
                elif pc in rem_set:
                    edges.append((pc, c))
                elif pc in passed_set:
                    pass
                else:
                    unknown_prereq_tokens.append(
                        {
                            "course": c,
                            "token": prereq,
                            "branch": "AND",
                            "resolved": pc,
                            "note": "resolved token not in remaining or passed",
                        }
                    )

    # Co-requisites: coreq must be same or earlier semester -> edge coreq -> c
    for c in remaining:
        info = courses.get(c)
        if not isinstance(info, dict):
            continue
        for coreq in _get_req_list(
            info,
            ("co-requisites", "co_requisites", "corequisites", "co-reqs"),
        ):
            if not isinstance(coreq, str) or not coreq.strip():
                continue
            cc = _resolve_course_token(coreq, code_index, pa)
            if cc is None:
                unknown_prereq_tokens.append({"course": c, "token": coreq, "branch": "COREQ"})
            elif cc in rem_set:
                edges.append((cc, c))

    # Cycle detection (Kahn) on remaining nodes only
    nodes = sorted(rem_set)
    indeg = {n: 0 for n in nodes}
    adj: dict[str, list[str]] = {n: [] for n in nodes}
    for u, v in edges:
        if u in rem_set and v in rem_set and u != v:
            adj[u].append(v)
            indeg[v] = indeg.get(v, 0) + 1

    q = deque([n for n in nodes if indeg[n] == 0])
    visited = 0
    topo: list[str] = []
    while q:
        u = q.popleft()
        visited += 1
        topo.append(u)
        for v in adj.get(u, []):
            indeg[v] -= 1
            if indeg[v] == 0:
                q.append(v)
    cyclic = visited != len(nodes)

    # Longest path on DAG (if acyclic); NP-hard on cyclic graph — skip length if cyclic
    longest_chain = 0
    if not cyclic:
        dist = {n: 1 for n in nodes}
        rank = {n: i for i, n in enumerate(topo)}
        for u in topo:
            for v in adj.get(u, []):
                if rank.get(u, -1) < rank.get(v, -2):
                    dist[v] = max(dist.get(v, 1), dist[u] + 1)
        longest_chain = max(dist.values()) if dist else 0

    try:
        cs = int(current_sem)
        mx = int(max_semester)
    except (TypeError, ValueError):
        cs, mx = 1, 0
    available_slots = max(0, mx - cs + 1) if mx >= cs else 0
    chain_exceeds_horizon = (not cyclic) and longest_chain > available_slots and available_slots > 0

    return {
        "remaining_course_count": len(remaining),
        "current_sem": cs,
        "max_semester": mx,
        "available_semester_slots": available_slots,
        "prereq_coreq_edges_among_remaining": len([(u, v) for u, v in edges if u in rem_set and v in rem_set]),
        "graph_has_cycle_among_remaining": cyclic,
        "longest_remaining_chain_length_nodes": longest_chain,
        "sequential_chain_longer_than_available_slots": chain_exceeds_horizon,
        "unknown_or_unresolved_prereq_tokens_sample": unknown_prereq_tokens[:40],
        "or_prereq_groups_with_no_course_and_no_year_standing": or_groups_unresolved[:20],
        "failed_courses_in_remaining": sorted(failed_set & rem_set),
        "note": "Year-standing tokens are not expanded into unit math here; CP-SAT still enforces them. "
        "Simple AND-prereqs and co-reqs build the directed graph; OR-prereq groups are only checked for "
        "unresolvable alternatives (no course or year-standing branch recognized).",
    }
