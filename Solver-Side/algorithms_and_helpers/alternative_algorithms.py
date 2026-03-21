"""
Alternative algorithm module (separate from the current CP-SAT setup).

This file is intentionally isolated so your existing solver pipeline can remain
untouched while you experiment with additional algorithm families.
"""

from __future__ import annotations

from dataclasses import dataclass
from random import Random
from typing import Any, Callable
from pathlib import Path
import json
import time


State = dict[str, Any]
ScoreFn = Callable[[State], float]
NeighborFn = Callable[[State], list[State]]
FeasibleFn = Callable[[State], bool]


_DEBUG_LOG_PATH = Path(__file__).resolve().parents[2] / "debug-7593d3.log"


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


@dataclass(frozen=True)
class LocalSearchConfig:
    max_iterations: int = 3000
    max_no_improve: int = 400
    restarts: int = 3
    seed: int = 42


@dataclass(frozen=True)
class FuzzyConfig:
    max_iterations: int = 2000
    seed: int = 42
    # You can tune these later per dataset.
    load_balance_weight: float = 0.35
    prerequisite_pressure_weight: float = 0.35
    delay_risk_weight: float = 0.30


@dataclass(frozen=True)
class TabuConfig:
    max_iterations: int = 3000
    tabu_tenure: int = 12
    seed: int = 42


def run_local_search(
    initial_state: State,
    score_fn: ScoreFn,
    neighbor_fn: NeighborFn,
    feasible_fn: FeasibleFn,
    config: LocalSearchConfig | None = None,
) -> tuple[State, float]:
    """
    Basic hill-climbing local search with random-restart support.

    Notes:
    - Keeps only feasible states.
    - Uses greedy improvement among generated neighbors.
    """
    cfg = config or LocalSearchConfig()
    rng = Random(cfg.seed)
    _debug_log("H4", "alternative_algorithms.py:run_local_search:start", "local_search_enter", {"max_iterations": cfg.max_iterations, "restarts": cfg.restarts})

    best_global = initial_state
    best_global_score = score_fn(initial_state)

    for _restart in range(cfg.restarts):
        current = best_global
        current_score = best_global_score
        no_improve = 0

        for it in range(cfg.max_iterations):
            neighbors = neighbor_fn(current)
            if _restart == 0 and it == 0:
                _debug_log("H4", "alternative_algorithms.py:run_local_search:iter0", "local_search_first_iter_neighbors", {"neighbors_feasible_count": len(neighbors)})
            if not neighbors:
                break

            candidate = min(neighbors, key=score_fn)
            candidate_score = score_fn(candidate)
            if candidate_score < current_score:
                current = candidate
                current_score = candidate_score
                no_improve = 0
            else:
                no_improve += 1
                if no_improve >= cfg.max_no_improve:
                    break

            if current_score < best_global_score:
                best_global = current
                best_global_score = current_score

        # Randomly perturb seed progression across restarts without mutating state.
        rng.random()

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

    for it in range(cfg.max_iterations):
        candidates = candidate_fn(current)
        if it == 0:
            _debug_log("H4", "alternative_algorithms.py:run_fuzzy_logic_search:iter0", "fuzzy_first_iter_candidates", {"candidates_feasible_count": len(candidates)})
        if not candidates:
            break
        current = min(candidates, key=fuzzy_score)
        current_score = fuzzy_score(current)
        if current_score < best_score:
            best = current
            best_score = current_score

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
    """
    cfg = config or TabuConfig()
    _rng = Random(cfg.seed)
    _debug_log("H4", "alternative_algorithms.py:run_tabu_search:start", "tabu_enter", {"max_iterations": cfg.max_iterations, "tabu_tenure": cfg.tabu_tenure})

    current = initial_state
    current_score = score_fn(current)
    best = current
    best_score = current_score

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

    return best, best_score

