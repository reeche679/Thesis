"""
Discrete-optimization adapter for course-to-semester scheduling.

Uses a simulated annealing-style search over the existing state model
(course -> semester) while preserving the existing scoring semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence
import math
import random
import time


State = dict[str, int]
ScoreFn = Callable[[State], float]
FeasibleFn = Callable[[State], bool]
ViolationSummaryFn = Callable[[State], dict[str, float]]


@dataclass(frozen=True)
class DiscreteOptConfig:
    seed: int = 42
    # "Balanced fast" defaults tuned to reach feasibility more often
    # while keeping per-request runtime under ~10s.
    max_iterations: int = 800
    restarts: int = 3
    initial_temperature: float = 160.0
    cooling_rate: float = 0.995
    min_temperature: float = 0.05
    early_stop_on_feasible: bool = False
    repair_max_iterations: int = 600
    repair_sample_neighbors: int = 12
    violation_worsen_accept_prob: float = 0.0
    repair_scan_passes: int = 5
    compact_passes: int = 3
    # Hard stop to prevent long runs when feasibility is hard.
    # Note: CP-SAT.py's total "Solve time" includes some overhead outside the
    # heuristic; keep this a bit below 10 seconds.
    time_budget_seconds: float = 8.5


class DiscreteOptUnavailableError(RuntimeError):
    pass


def _ensure_library_present() -> None:
    """
    Guard that discrete-optimization dependency exists in runtime env.

    The search implementation here is custom and does not depend on API
    stability of the external package, but we still require installation
    because this backend was selected explicitly by configuration.
    """
    try:
        __import__("discrete_optimization")
    except Exception as exc:
        raise DiscreteOptUnavailableError(
            "discrete-optimization is not available. Install project requirements first."
        ) from exc


def run_discrete_opt_search(
    initial_state: State,
    score_fn: ScoreFn,
    feasible_fn: FeasibleFn,
    violation_summary_fn: ViolationSummaryFn,
    remaining: Sequence[str],
    valid_map: dict[str, Sequence[int]],
    config: DiscreteOptConfig | None = None,
) -> tuple[State, float, bool]:
    """
    Run a simulated-annealing-style search and return:
    (best_state, best_score, is_feasible_best_state).
    """
    _ensure_library_present()
    cfg = config or DiscreteOptConfig()
    rem = list(remaining)
    t_end = time.perf_counter() + float(cfg.time_budget_seconds) if cfg.time_budget_seconds else None

    def _time_exceeded() -> bool:
        return t_end is not None and time.perf_counter() >= t_end

    if not rem:
        s0 = dict(initial_state)
        sc0 = float(score_fn(s0))
        return s0, sc0, bool(feasible_fn(s0))

    def _random_state(local_rng: random.Random) -> State:
        st: State = {}
        for c in rem:
            choices = tuple(valid_map.get(c) or ())
            if choices:
                st[c] = int(local_rng.choice(choices))
            else:
                st[c] = int(initial_state.get(c, 1))
        return st

    def _neighbor(state: State, local_rng: random.Random) -> State:
        nxt = dict(state)
        c = local_rng.choice(rem)
        choices = tuple(valid_map.get(c) or ())
        if not choices:
            return nxt
        cur = nxt.get(c, choices[0])
        if len(choices) == 1:
            nxt[c] = int(choices[0])
            return nxt

        try:
            idx = choices.index(cur)
        except ValueError:
            idx = 0

        move_p = local_rng.random()
        # Mostly local moves; occasional random jump for diversification.
        if move_p < 0.65:
            cand_idxs = [max(0, idx - 1), min(len(choices) - 1, idx + 1)]
            cand_idxs = [i for i in cand_idxs if i != idx]
            if cand_idxs:
                nxt[c] = int(choices[local_rng.choice(cand_idxs)])
        elif move_p < 0.90:
            nxt[c] = int(local_rng.choice(choices))
        else:
            # Pair-swap style move to improve load balancing while preserving domains.
            d = local_rng.choice(rem)
            if d != c:
                d_choices = tuple(valid_map.get(d) or ())
                c_sem = nxt.get(c)
                d_sem = nxt.get(d)
                if d_choices and choices and d_sem in choices and c_sem in d_choices:
                    nxt[c], nxt[d] = int(d_sem), int(c_sem)
        return nxt

    def _guided_neighbors(state: State, local_rng: random.Random) -> list[State]:
        """
        Build a focused set of neighbors around a sampled subset of courses.
        """
        neighbors: list[State] = []
        course_pool = rem if len(rem) <= 14 else local_rng.sample(rem, 14)
        for c in course_pool:
            choices = tuple(valid_map.get(c) or ())
            if not choices:
                continue
            cur = state.get(c, choices[0])
            try:
                idx = choices.index(cur)
            except ValueError:
                idx = 0
            for ni in {max(0, idx - 1), min(len(choices) - 1, idx + 1)}:
                if ni == idx:
                    continue
                nxt = dict(state)
                nxt[c] = int(choices[ni])
                neighbors.append(nxt)
            if len(choices) > 2:
                nxt = dict(state)
                nxt[c] = int(local_rng.choice(choices))
                neighbors.append(nxt)
        return neighbors

    def _summary_tuple(state: State) -> tuple[float, float, float, float]:
        """
        Feasible-first lexicographic comparison key:
        (violations, max_sem, sum_sem, scalar_score).
        """
        v = violation_summary_fn(state)
        return (
            float(v.get("violations", 0.0)),
            float(v.get("max_sem", 0.0)),
            float(v.get("sum_sem", 0.0)),
            float(score_fn(state)),
        )

    def _is_better(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
        return a < b

    best_global = dict(initial_state)
    best_global_key = _summary_tuple(best_global)
    # In this codebase, `feasible_fn` is equivalent to `violations == 0`.
    # Use the already-computed key to avoid an extra evaluator call.
    best_feasible: State | None = dict(best_global) if best_global_key[0] <= 0.0 else None
    best_feasible_key = best_global_key if best_feasible is not None else (float("inf"),) * 4

    for r in range(cfg.restarts):
        rrng = random.Random(cfg.seed + 9973 * (r + 1))
        current = dict(initial_state) if r == 0 else _random_state(rrng)
        current_key = _summary_tuple(current)
        temperature = float(cfg.initial_temperature)

        if _is_better(current_key, best_global_key):
            best_global, best_global_key = dict(current), current_key
        if current_key[0] <= 0.0 and _is_better(current_key, best_feasible_key):
            best_feasible, best_feasible_key = dict(current), current_key
            if cfg.early_stop_on_feasible:
                return dict(best_feasible), float(best_feasible_key[3]), True

        for _ in range(cfg.max_iterations):
            if _time_exceeded():
                break
            candidate = _neighbor(current, rrng)
            cand_key = _summary_tuple(candidate)
            delta = (
                (cand_key[0] - current_key[0]) * 1_000_000.0
                + (cand_key[1] - current_key[1]) * 1000.0
                + (cand_key[2] - current_key[2]) * 10.0
                + (cand_key[3] - current_key[3])
            )
            accept = delta <= 0.0
            # While infeasible, avoid moves that worsen violations.
            if current_key[0] > 0.0 and cand_key[0] > current_key[0]:
                if rrng.random() >= cfg.violation_worsen_accept_prob:
                    accept = False
            if not accept and temperature > 0:
                accept = rrng.random() < math.exp(-delta / max(temperature, 1e-9))
            if accept:
                current, current_key = candidate, cand_key

            if _is_better(current_key, best_global_key):
                best_global, best_global_key = dict(current), current_key

            if current_key[0] <= 0.0 and _is_better(current_key, best_feasible_key):
                best_feasible, best_feasible_key = dict(current), current_key
                if cfg.early_stop_on_feasible:
                    return dict(best_feasible), float(best_feasible_key[3]), True

            temperature = max(cfg.min_temperature, temperature * cfg.cooling_rate)
        if _time_exceeded():
            break

    # Repair phase: deterministic local improvement focused on reducing violations.
    if _time_exceeded():
        best_state = best_feasible if best_feasible is not None else best_global
        best_key = best_feasible_key if best_feasible is not None else best_global_key
        return dict(best_state), float(best_key[3]), bool(best_key[0] <= 0.0)

    repair_state = dict(best_feasible if best_feasible is not None else best_global)
    repair_key = _summary_tuple(repair_state)
    repair_rng = random.Random(cfg.seed + 88_777)
    for _ in range(cfg.repair_max_iterations):
        if _time_exceeded():
            best_state = best_feasible if best_feasible is not None else best_global
            best_key = best_feasible_key if best_feasible is not None else best_global_key
            return dict(best_state), float(best_key[3]), bool(best_key[0] <= 0.0)
        if repair_key[0] <= 0.0:
            break
        candidates: list[tuple[tuple[float, float, float, float], State]] = []
        for cand in _guided_neighbors(repair_state, repair_rng):
            cand_key = _summary_tuple(cand)
            # Repair phase focuses on reducing hard-constraint violations first.
            if cand_key[0] <= repair_key[0]:
                candidates.append((cand_key, cand))
        if not candidates:
            for _n in range(cfg.repair_sample_neighbors):
                cand = _neighbor(repair_state, repair_rng)
                cand_key = _summary_tuple(cand)
                if cand_key[0] <= repair_key[0]:
                    candidates.append((cand_key, cand))

        if candidates:
            best_local_key, best_local_state = min(candidates, key=lambda x: x[0])
            if _is_better(best_local_key, repair_key):
                repair_state, repair_key = best_local_state, best_local_key
            else:
                # Small kick move when equal-quality plateau is reached.
                repair_state = _neighbor(repair_state, repair_rng)
                repair_key = _summary_tuple(repair_state)
        else:
            # Random perturbation to escape dead-end.
            repair_state = _random_state(repair_rng)
            repair_key = _summary_tuple(repair_state)

    # Deterministic scan repair: for each course, try all valid semesters and
    # keep moves that improve the feasible-first tuple (especially violations).
    for _ in range(cfg.repair_scan_passes):
        if _time_exceeded():
            best_state = best_feasible if best_feasible is not None else best_global
            best_key = best_feasible_key if best_feasible is not None else best_global_key
            return dict(best_state), float(best_key[3]), bool(best_key[0] <= 0.0)
        improved_any = False
        for c in repair_rng.sample(rem, len(rem)):
            current_sem = repair_state.get(c)
            choices = tuple(valid_map.get(c) or ())
            if not choices:
                continue
            best_local_key = repair_key
            best_local_sem = current_sem
            for sem in choices:
                sem = int(sem)
                if sem == current_sem:
                    continue
                cand = dict(repair_state)
                cand[c] = sem
                cand_key = _summary_tuple(cand)
                if _is_better(cand_key, best_local_key):
                    best_local_key = cand_key
                    best_local_sem = sem
            if best_local_sem != current_sem:
                repair_state[c] = int(best_local_sem)
                repair_key = best_local_key
                improved_any = True
                if repair_key[0] <= 0.0:
                    break
        if not improved_any or repair_key[0] <= 0.0:
            break

    final_state = repair_state if _is_better(repair_key, best_global_key) else best_global
    final_key = _summary_tuple(final_state)
    if best_feasible is not None and _is_better(best_feasible_key, final_key):
        final_state = dict(best_feasible)
        final_key = best_feasible_key

    # Feasible compaction pass: once feasible, pull courses earlier where allowed
    # to prioritize earlier graduation without breaking constraints.
    if final_key[0] <= 0.0:
        if _time_exceeded():
            return dict(final_state), float(final_key[3]), True
        compact_state = dict(final_state)
        compact_key = _summary_tuple(compact_state)
        for _ in range(cfg.compact_passes):
            if _time_exceeded():
                return dict(compact_state), float(compact_key[3]), bool(compact_key[0] <= 0.0)
            moved = False
            by_latest = sorted(rem, key=lambda c: compact_state.get(c, 10**9), reverse=True)
            for c in by_latest:
                cur = compact_state.get(c)
                choices = tuple(valid_map.get(c) or ())
                if not choices:
                    continue
                earlier = [int(s) for s in choices if int(s) < int(cur)]
                earlier.sort()
                for sem in earlier:
                    cand = dict(compact_state)
                    cand[c] = sem
                    cand_key = _summary_tuple(cand)
                    if cand_key[0] > 0.0:
                        continue
                    if _is_better(cand_key, compact_key):
                        compact_state, compact_key = cand, cand_key
                        moved = True
                        break
            if not moved:
                break
        final_state, final_key = compact_state, compact_key

    return dict(final_state), float(final_key[3]), bool(final_key[0] <= 0.0)
