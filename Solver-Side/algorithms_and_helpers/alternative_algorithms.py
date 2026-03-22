"""
Alternative algorithm module (separate from the current CP-SAT setup).

This file is intentionally isolated so your existing solver pipeline can remain
untouched while you experiment with additional algorithm families.
"""

from __future__ import annotations

from dataclasses import dataclass
from random import Random
from typing import Any, Callable, Optional, Sequence
from pathlib import Path
import json
import math
import time


State = dict[str, Any]
ScoreFn = Callable[[State], float]
NeighborFn = Callable[[State], list[State]]
FeasibleFn = Callable[[State], bool]


_DEBUG_LOG_PATH = Path(__file__).resolve().parents[2] / "debug-7593d3.log"
_AB886B_LOG_PATH = Path(__file__).resolve().parents[2] / "debug-ab886b.log"


def _ab886b_log(hypothesis_id: str, location: str, message: str, data: dict, run_id: str = "run1"):
    # #region agent log
    try:
        with _AB886B_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "sessionId": "ab886b",
                        "runId": run_id,
                        "hypothesisId": hypothesis_id,
                        "location": location,
                        "message": message,
                        "data": data,
                        "timestamp": int(time.time() * 1000),
                    },
                    ensure_ascii=True,
                )
                + "\n"
            )
    except Exception:
        pass
    # #endregion


def _debug_log(hypothesis_id: str, location: str, message: str, data: dict, run_id: str = "run1"):
    # #region agent log
    try:
        with _DEBUG_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "sessionId": "7593d3",
                        "runId": run_id,
                        "hypothesisId": hypothesis_id,
                        "location": location,
                        "message": message,
                        "data": data,
                        "timestamp": int(time.time() * 1000),
                    },
                    ensure_ascii=True,
                )
                + "\n"
            )
    except Exception:
        pass
    # #endregion


def _return_if_early_feasible(
    cfg: Any,
    best_feasible: State | None,
    best_feasible_score: float,
    algorithm: str,
) -> tuple[State, float] | None:
    """If early_stop_on_feasible is set and we already have a feasible state, return it."""
    if not getattr(cfg, "early_stop_on_feasible", False) or best_feasible is None:
        return None
    # #region agent log
    _ab886b_log(
        "H5",
        f"alternative_algorithms.py:{algorithm}:early_feasible",
        "alt_algorithm_early_exit",
        {
            "algorithm": algorithm,
            "used_best_feasible": True,
            "return_score": float(best_feasible_score),
        },
    )
    # #endregion
    return (best_feasible, best_feasible_score)


@dataclass(frozen=True)
class LocalSearchConfig:
    # Tuned for responsiveness; raise limits for higher solution quality.
    max_iterations: int = 1500
    max_no_improve: int = 250
    restarts: int = 3
    seed: int = 42
    # Stop as soon as any feasible state is found (much faster; may sacrifice best-feasible quality).
    early_stop_on_feasible: bool = True


@dataclass(frozen=True)
class FuzzyConfig:
    max_iterations: int = 1000
    seed: int = 42
    # You can tune these later per dataset.
    load_balance_weight: float = 0.35
    prerequisite_pressure_weight: float = 0.35
    delay_risk_weight: float = 0.30
    early_stop_on_feasible: bool = True


@dataclass(frozen=True)
class TabuConfig:
    max_iterations: int = 1500
    tabu_tenure: int = 12
    seed: int = 42
    early_stop_on_feasible: bool = True


@dataclass(frozen=True)
class FireflyConfig:
    """Discrete firefly search (DFA-style): continuous move + snap to valid semesters + optional LSM."""

    n_fireflies: int = 10
    max_generations: int = 12
    beta0: float = 1.0
    gamma: float = 1.0
    alpha: float = 0.5
    # Apply neighbor_fn local improvement from this generation index onward (like paper: after a few iters).
    lsm_start_generation: int = 2
    # Run LSM only every N-th generation (1 = every time).
    lsm_every_n_generations: int = 2
    # Each firefly is pulled only toward the K best brighter fireflies (cuts O(n²) work).
    max_attractors: int = 6
    seed: int = 42
    early_stop_on_feasible: bool = True


def _snap_floats_to_valid_domains(
    floats: dict[str, float],
    remaining: Sequence[str],
    valid_map: dict[str, Sequence[int]],
) -> State:
    out: State = {}
    for c in remaining:
        ch = valid_map.get(c)
        if not ch:
            out[c] = int(round(floats.get(c, 0)))
            continue
        target = floats.get(c, float(ch[0]))
        out[c] = int(min(ch, key=lambda s: abs(float(s) - target)))
    return out


def run_firefly_search(
    initial_state: State,
    score_fn: ScoreFn,
    feasible_fn: FeasibleFn,
    remaining: Sequence[str],
    valid_map: dict[str, Sequence[int]],
    random_restart_fn: Callable[[Random], State],
    neighbor_fn: Optional[NeighborFn] = None,
    config: FireflyConfig | None = None,
) -> tuple[State, float]:
    """
    Swarm-style discrete firefly search adapted to course→semester states.

    Uses Yang-style attractiveness in continuous semester space, then snaps each
    course to the nearest allowed semester (valid_map). Prereq/coreq/load and all
    other rules enter only through score_fn / feasible_fn (same as other
    alternatives). Optional neighbor_fn provides a local-search polish (LSM).
    """
    cfg = config or FireflyConfig()
    rng = Random(cfg.seed)
    _debug_log(
        "H4",
        "alternative_algorithms.py:run_firefly_search:start",
        "firefly_enter",
        {
            "n_fireflies": cfg.n_fireflies,
            "max_generations": cfg.max_generations,
        },
    )

    population: list[State] = [dict(initial_state)]
    for k in range(1, cfg.n_fireflies):
        population.append(dict(random_restart_fn(Random(cfg.seed + 1009 * (k + 1)))))

    _init_best = min(population, key=score_fn)
    best_global = dict(_init_best)
    best_global_score = score_fn(best_global)
    best_feasible: State | None = None
    best_feasible_score = float("inf")

    def _maybe_record_feasible(state: State, sc: float) -> None:
        nonlocal best_feasible, best_feasible_score
        if feasible_fn(state) and (best_feasible is None or sc < best_feasible_score):
            best_feasible = dict(state)
            best_feasible_score = sc

    for s in population:
        _maybe_record_feasible(s, score_fn(s))

    er_init = _return_if_early_feasible(cfg, best_feasible, best_feasible_score, "firefly")
    if er_init is not None:
        return er_init

    rem = list(remaining)

    for gen in range(cfg.max_generations):
        scores = [score_fn(s) for s in population]
        new_pop: list[State] = []
        for i in range(cfg.n_fireflies):
            x = {c: float(population[i][c]) for c in rem}
            brighter = [j for j in range(cfg.n_fireflies) if j != i and scores[j] < scores[i]]
            brighter.sort(key=lambda j: scores[j])
            for j in brighter[: cfg.max_attractors]:
                xj = population[j]
                d_sq = sum((x[c] - float(xj[c])) ** 2 for c in rem)
                d = math.sqrt(d_sq) if d_sq > 0 else 1e-6
                beta = cfg.beta0 * math.exp(-cfg.gamma * d * d)
                for c in rem:
                    ch = valid_map.get(c) or (int(x[c]),)
                    span = float(max(ch) - min(ch)) if len(ch) > 1 else 1.0
                    x[c] = (
                        x[c]
                        + beta * (float(xj[c]) - x[c])
                        + cfg.alpha * (rng.random() - 0.5) * max(span, 1.0)
                    )
            snapped = _snap_floats_to_valid_domains(x, rem, valid_map)
            new_pop.append(snapped)
        population = new_pop

        for s in population:
            sc = score_fn(s)
            if sc < best_global_score:
                best_global = dict(s)
                best_global_score = sc
            _maybe_record_feasible(s, sc)

        er = _return_if_early_feasible(cfg, best_feasible, best_feasible_score, "firefly")
        if er is not None:
            return er

        _lsm_period = max(1, cfg.lsm_every_n_generations)
        run_lsm = (
            neighbor_fn is not None
            and gen >= cfg.lsm_start_generation
            and (gen - cfg.lsm_start_generation) % _lsm_period == 0
        )
        if run_lsm:
            for i in range(cfg.n_fireflies):
                nbrs = neighbor_fn(population[i])
                if not nbrs:
                    continue
                cand = min(nbrs, key=score_fn)
                sc_c = score_fn(cand)
                if sc_c < score_fn(population[i]):
                    population[i] = dict(cand)
                    if sc_c < best_global_score:
                        best_global = dict(cand)
                        best_global_score = sc_c
                    _maybe_record_feasible(cand, sc_c)
            er2 = _return_if_early_feasible(cfg, best_feasible, best_feasible_score, "firefly")
            if er2 is not None:
                return er2

    if best_feasible is not None:
        # #region agent log
        _ab886b_log(
            "H5",
            "alternative_algorithms.py:run_firefly_search:return",
            "alt_algorithm_return",
            {
                "algorithm": "firefly",
                "used_best_feasible": True,
                "return_score": float(best_feasible_score),
            },
        )
        # #endregion
        return best_feasible, best_feasible_score
    # #region agent log
    _ab886b_log(
        "H5",
        "alternative_algorithms.py:run_firefly_search:return",
        "alt_algorithm_return",
        {
            "algorithm": "firefly",
            "used_best_feasible": False,
            "return_score": float(best_global_score),
        },
    )
    # #endregion
    return best_global, best_global_score


def run_local_search(
    initial_state: State,
    score_fn: ScoreFn,
    neighbor_fn: NeighborFn,
    feasible_fn: FeasibleFn,
    config: LocalSearchConfig | None = None,
    random_restart_fn: Optional[Callable[[Random], State]] = None,
) -> tuple[State, float]:
    """
    Hill-climbing local search with random-restart support.

    Moves use all neighbors (penalty objective may visit infeasible states).
    If any feasible state is visited, returns the best feasible by score_fn;
    otherwise returns the best state found overall.

    If random_restart_fn is set, restart rounds r>0 begin from a fresh random
    valid assignment (from the caller's domain) instead of only continuing from
    the global best-so-far.
    """
    cfg = config or LocalSearchConfig()
    _debug_log("H4", "alternative_algorithms.py:run_local_search:start", "local_search_enter", {"max_iterations": cfg.max_iterations, "restarts": cfg.restarts})

    best_global = initial_state
    best_global_score = score_fn(initial_state)
    best_feasible: State | None = None
    best_feasible_score = float("inf")

    def _maybe_record_feasible(state: State, sc: float) -> None:
        nonlocal best_feasible, best_feasible_score
        if feasible_fn(state) and (best_feasible is None or sc < best_feasible_score):
            best_feasible = dict(state)
            best_feasible_score = sc

    _maybe_record_feasible(initial_state, best_global_score)
    er0 = _return_if_early_feasible(cfg, best_feasible, best_feasible_score, "local_search")
    if er0 is not None:
        return er0

    for restart_idx in range(cfg.restarts):
        if restart_idx > 0 and random_restart_fn is not None:
            rrng = Random(cfg.seed + 7919 * (restart_idx + 1))
            current = random_restart_fn(rrng)
            current_score = score_fn(current)
        else:
            current = best_global
            current_score = best_global_score
        no_improve = 0
        _maybe_record_feasible(current, current_score)
        er_r = _return_if_early_feasible(cfg, best_feasible, best_feasible_score, "local_search")
        if er_r is not None:
            return er_r

        for it in range(cfg.max_iterations):
            neighbors = neighbor_fn(current)
            if restart_idx == 0 and it == 0:
                _debug_log("H4", "alternative_algorithms.py:run_local_search:iter0", "local_search_first_iter_neighbors", {"neighbors_feasible_count": len(neighbors)})
            if not neighbors:
                break

            candidate = min(neighbors, key=score_fn)
            candidate_score = score_fn(candidate)
            if candidate_score < current_score:
                current = candidate
                current_score = candidate_score
                no_improve = 0
                _maybe_record_feasible(current, current_score)
                er = _return_if_early_feasible(cfg, best_feasible, best_feasible_score, "local_search")
                if er is not None:
                    return er
            else:
                no_improve += 1
                if no_improve >= cfg.max_no_improve:
                    break

            if current_score < best_global_score:
                best_global = current
                best_global_score = current_score

    if best_feasible is not None:
        # #region agent log
        _ab886b_log(
            "H5",
            "alternative_algorithms.py:run_local_search:return",
            "alt_algorithm_return",
            {
                "algorithm": "local_search",
                "used_best_feasible": True,
                "return_score": float(best_feasible_score),
            },
        )
        # #endregion
        return best_feasible, best_feasible_score
    # #region agent log
    _ab886b_log(
        "H5",
        "alternative_algorithms.py:run_local_search:return",
        "alt_algorithm_return",
        {
            "algorithm": "local_search",
            "used_best_feasible": False,
            "return_score": float(best_global_score),
        },
    )
    # #endregion
    return best_global, best_global_score


def run_fuzzy_logic_search(
    initial_state: State,
    candidate_fn: NeighborFn,
    fuzzy_feature_fn: Callable[[State], dict[str, float]],
    feasible_fn: FeasibleFn,
    config: FuzzyConfig | None = None,
) -> tuple[State, float]:
    """
    Fuzzy-logic guided local improvement.

    Expected feature keys:
    - load_balance
    - prerequisite_pressure
    - delay_risk

    Lower final score is better.

    If any feasible state is visited, returns the best feasible by fuzzy_score;
    otherwise returns the best state by fuzzy_score overall.
    """
    cfg = config or FuzzyConfig()
    _rng = Random(cfg.seed)
    _debug_log("H4", "alternative_algorithms.py:run_fuzzy_logic_search:start", "fuzzy_enter", {"max_iterations": cfg.max_iterations})

    def fuzzy_score(state: State) -> float:
        f = fuzzy_feature_fn(state)
        return (
            cfg.load_balance_weight * float(f.get("load_balance", 1.0))
            + cfg.prerequisite_pressure_weight * float(f.get("prerequisite_pressure", 1.0))
            + cfg.delay_risk_weight * float(f.get("delay_risk", 1.0))
        )

    current = initial_state
    best = initial_state
    best_score = fuzzy_score(initial_state)
    best_feasible: State | None = None
    best_feasible_score = float("inf")

    def _maybe_record_feasible(state: State, sc: float) -> None:
        nonlocal best_feasible, best_feasible_score
        if feasible_fn(state) and (best_feasible is None or sc < best_feasible_score):
            best_feasible = dict(state)
            best_feasible_score = sc

    _maybe_record_feasible(initial_state, best_score)
    er0 = _return_if_early_feasible(cfg, best_feasible, best_feasible_score, "fuzzy_logic")
    if er0 is not None:
        return er0

    for it in range(cfg.max_iterations):
        candidates = candidate_fn(current)
        if it == 0:
            _debug_log("H4", "alternative_algorithms.py:run_fuzzy_logic_search:iter0", "fuzzy_first_iter_candidates", {"candidates_feasible_count": len(candidates)})
        if not candidates:
            break
        current = min(candidates, key=fuzzy_score)
        current_score = fuzzy_score(current)
        _maybe_record_feasible(current, current_score)
        er = _return_if_early_feasible(cfg, best_feasible, best_feasible_score, "fuzzy_logic")
        if er is not None:
            return er
        if current_score < best_score:
            best = current
            best_score = current_score

    if best_feasible is not None:
        # #region agent log
        _ab886b_log(
            "H5",
            "alternative_algorithms.py:run_fuzzy_logic_search:return",
            "alt_algorithm_return",
            {
                "algorithm": "fuzzy_logic",
                "used_best_feasible": True,
                "return_score": float(best_feasible_score),
            },
        )
        # #endregion
        return best_feasible, best_feasible_score
    # #region agent log
    _ab886b_log(
        "H5",
        "alternative_algorithms.py:run_fuzzy_logic_search:return",
        "alt_algorithm_return",
        {
            "algorithm": "fuzzy_logic",
            "used_best_feasible": False,
            "return_score": float(best_score),
        },
    )
    # #endregion
    return best, best_score


def run_tabu_search(
    initial_state: State,
    score_fn: ScoreFn,
    neighbor_fn: NeighborFn,
    feasible_fn: FeasibleFn,
    move_id_fn: Callable[[State, State], str],
    config: TabuConfig | None = None,
) -> tuple[State, float]:
    """
    Tabu Search with short-term memory and aspiration criterion.

    A tabu move is allowed if it improves the global best score.

    If any feasible state is visited, returns the best feasible by score_fn;
    otherwise returns the best state found overall.
    """
    cfg = config or TabuConfig()
    _rng = Random(cfg.seed)
    _debug_log("H4", "alternative_algorithms.py:run_tabu_search:start", "tabu_enter", {"max_iterations": cfg.max_iterations, "tabu_tenure": cfg.tabu_tenure})

    current = initial_state
    current_score = score_fn(current)
    best = current
    best_score = current_score
    best_feasible: State | None = None
    best_feasible_score = float("inf")

    def _maybe_record_feasible(state: State, sc: float) -> None:
        nonlocal best_feasible, best_feasible_score
        if feasible_fn(state) and (best_feasible is None or sc < best_feasible_score):
            best_feasible = dict(state)
            best_feasible_score = sc

    _maybe_record_feasible(current, current_score)
    er0 = _return_if_early_feasible(cfg, best_feasible, best_feasible_score, "tabu_search")
    if er0 is not None:
        return er0

    tabu: dict[str, int] = {}

    for it in range(cfg.max_iterations):
        neighbors = neighbor_fn(current)
        if it == 0:
            _debug_log("H4", "alternative_algorithms.py:run_tabu_search:iter0", "tabu_first_iter_neighbors", {"neighbors_feasible_count": len(neighbors)})
        if not neighbors:
            break

        allowed: list[tuple[State, float, str]] = []
        for nxt in neighbors:
            mv = move_id_fn(current, nxt)
            sc = score_fn(nxt)
            is_tabu = tabu.get(mv, -1) > it
            if (not is_tabu) or (sc < best_score):
                allowed.append((nxt, sc, mv))

        if not allowed:
            break

        nxt, nxt_score, mv = min(allowed, key=lambda x: x[1])
        current = nxt
        current_score = nxt_score
        tabu[mv] = it + cfg.tabu_tenure

        if current_score < best_score:
            best = current
            best_score = current_score
        _maybe_record_feasible(current, current_score)
        er = _return_if_early_feasible(cfg, best_feasible, best_feasible_score, "tabu_search")
        if er is not None:
            return er

    if best_feasible is not None:
        # #region agent log
        _ab886b_log(
            "H5",
            "alternative_algorithms.py:run_tabu_search:return",
            "alt_algorithm_return",
            {
                "algorithm": "tabu_search",
                "used_best_feasible": True,
                "return_score": float(best_feasible_score),
            },
        )
        # #endregion
        return best_feasible, best_feasible_score
    # #region agent log
    _ab886b_log(
        "H5",
        "alternative_algorithms.py:run_tabu_search:return",
        "alt_algorithm_return",
        {
            "algorithm": "tabu_search",
            "used_best_feasible": False,
            "return_score": float(best_score),
        },
    )
    # #endregion
    return best, best_score

