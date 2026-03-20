"""
POS scheduling algorithms.

Each algorithm is implemented as a function that adds constraints (and optionally
influences the objective) to the CP-SAT model. The modulo-3 (term-type-aligned)
algorithm is the baseline offering rule used by the main solver.
"""

from dataclasses import dataclass
from typing import Callable

# ---------------------------------------------------------------------------
# Weights (Algorithm 2 input)
# ---------------------------------------------------------------------------
#
# Default weight is 1 for any course not listed here.
# You can freely edit these numbers for your experiments.
#
DEFAULT_COURSE_WEIGHTS = {
    "MATH 101": 10,
    "CHEM 101": 8,
    "MATH 102": 9,
    "PHYS 102": 8,
    "PHYS 103": 7,
    "CpE 202": 6,
    "MATH 103": 8,
    "CpE 304": 8,
    "CpE 306": 8,
    "CpE 401": 10,
    "CpE 402": 8,
    "CpE 404": 8,
    "Elective Course 3": 8,
    "CpE 310": 8,
    "CpE 312": 8,

}

# ---------------------------------------------------------------------------
# Algorithm 1: Term-type-aligned (Modulo-3) offering constraints
# ---------------------------------------------------------------------------

def add_term_type_aligned_offering_constraints(
    model,
    semester,
    courses,
    passed_courses,
    current_sem: int,
    with_petition: bool,
    first_semester_fn,
    max_semester: int,
):
    """Baseline modulo-3 offering rule (Rigid/Flexible/Bottleneck + petition)."""
    for c, info in courses.items():
        if c in passed_courses:
            continue
        orig = first_semester_fn(info) or 1
        cat = info.get("category", "Rigid")
        valid_sems = []
        for s in range(current_sem, max_semester + 1):
            sem_type, orig_type = s % 3, orig % 3  # 1: 1st, 2: 2nd, 0: Summer
            if cat == "Rigid" and sem_type == orig_type:
                valid_sems.append(s)
            elif cat == "Flexible" and (
                (orig_type in [1, 2] and sem_type in [1, 2])
                or (orig_type == 0 and sem_type == 0)
            ):
                valid_sems.append(s)
            elif cat == "Bottleneck":
                if not with_petition:
                    if sem_type == orig_type:
                        valid_sems.append(s)
                else:
                    valid_sems.append(s)
        if valid_sems:
            model.AddAllowedAssignments([semester[c]], [[s] for s in valid_sems])


def add_capped_petition_trio_offering_constraints(
    model,
    semester,
    courses,
    passed_courses,
    current_sem: int,
    with_petition: bool,
    first_semester_fn,
    max_semester: int,
):
    """
    Petition-friendly, capped offering rule that matches the template POS:

    - Hard cap at 5th Year 2nd Sem (sem 14): no courses scheduled beyond 14.
    - Graduation trio (ENGG 117, CpE 405, CpE 406) fixed at sem 14, alone.
    - Bottleneck subjects behave as if petition is always allowed (more flexible).
    - Still uses term-type logic (1st/2nd/Summer) for Rigid/Flexible.
    """
    # 1) Cap horizon at 5th Year 2nd Sem.
    cap_sem = min(max_semester, 14)

    # 2) Graduation trio fixed at cap_sem, no one else allowed there.
    graduation_trio = ("ENGG 117", "CpE 405", "CpE 406")
    trio_remaining = [c for c in graduation_trio if c in courses and c not in passed_courses]
    if trio_remaining:
        for c in trio_remaining:
            model.Add(semester[c] == cap_sem)
        for c in courses:
            if c in passed_courses or c in graduation_trio:
                continue
            model.Add(semester[c] != cap_sem)

    # 3) Offering windows for all other courses within [current_sem, cap_sem].
    for c, info in courses.items():
        if c in passed_courses or c in graduation_trio:
            continue
        orig = first_semester_fn(info) or 1
        cat = info.get("category", "Rigid")
        valid_sems = []
        for s in range(current_sem, cap_sem + 1):
            sem_type, orig_type = s % 3, orig % 3  # 1: 1st, 2: 2nd, 0: Summer
            if cat == "Rigid" and sem_type == orig_type:
                valid_sems.append(s)
            elif cat == "Flexible" and (
                (orig_type in [1, 2] and sem_type in [1, 2])
                or (orig_type == 0 and sem_type == 0)
            ):
                valid_sems.append(s)
            elif cat == "Bottleneck":
                # For this algorithm, treat bottlenecks as always petition-enabled:
                # allow them in any regular term from current_sem to cap_sem.
                valid_sems.append(s)
        if valid_sems:
            model.AddAllowedAssignments([semester[c]], [[s] for s in valid_sems])


def add_baseline_objective(
    model,
    semester,
    courses,
    passed_courses,
    current_sem: int,
    max_semester: int,
    weights: dict | None = None,
):
    """
    Baseline objective (your current behavior):

    1) Minimize the latest semester used (finish as early as possible).
    2) Tie-break by minimizing sum(semester[c]) to pack courses earlier.

    `weights` is accepted for API compatibility but ignored here.
    """
    max_sem_used = model.NewIntVar(current_sem, max_semester, "max_sem_used")
    for c in courses:
        if c not in passed_courses:
            model.Add(max_sem_used >= semester[c])

    scale = max_semester * max_semester + 1
    model.Minimize(
        max_sem_used * scale
        + sum(semester[c] for c in courses if c not in passed_courses)
    )


def add_weighted_priority_objective(
    model,
    semester,
    courses,
    passed_courses,
    current_sem: int,
    max_semester: int,
    weights: dict | None = None,
):
    """
    Algorithm 2 (Weighted-priority objective):

    1) Minimize the latest semester used (finish as early as possible).
    2) Tie-break by minimizing sum(weights[c] * semester[c]).

    Higher weights push those courses earlier whenever constraints allow.
    """
    if weights is None:
        weights = DEFAULT_COURSE_WEIGHTS

    remaining = [c for c in courses if c not in passed_courses]
    max_weight = max([1, *[int(weights.get(c, 1)) for c in remaining]])

    max_sem_used = model.NewIntVar(current_sem, max_semester, "max_sem_used")
    for c in remaining:
        model.Add(max_sem_used >= semester[c])

    # Ensure primary objective dominates any possible change in the weighted term.
    scale = (max_weight * max_semester * max(1, len(remaining))) + 1
    weighted_sum = sum(int(weights.get(c, 1)) * semester[c] for c in remaining)
    model.Minimize(max_sem_used * scale + weighted_sum)


# ---------------------------------------------------------------------------
# Single-selection registry (one name = one full algorithm)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AlgorithmSpec:
    """
    One thesis algorithm = one offering rule + one objective rule (+ optional weights).
    """

    offering_fn: Callable
    objective_fn: Callable
    weights: dict | None = None


ALGORITHMS = {
    # Algorithm 1: your original behavior (modulo-3 offering + baseline objective)
    "algo1_modulo3_baseline": AlgorithmSpec(
        offering_fn=add_term_type_aligned_offering_constraints,
        objective_fn=add_baseline_objective,
        weights=None,
    ),
    # Algorithm 2: modulo-3 offering + weighted-priority objective
    "algo2_modulo3_weighted_priority": AlgorithmSpec(
        offering_fn=add_term_type_aligned_offering_constraints,
        objective_fn=add_weighted_priority_objective,
        weights=DEFAULT_COURSE_WEIGHTS,
    ),
    # Algorithm 3: capped horizon + trio fixed at 5th Year 2nd Sem + weighted objective
    "algo3_capped_trio_weighted": AlgorithmSpec(
        offering_fn=add_capped_petition_trio_offering_constraints,
        objective_fn=add_weighted_priority_objective,
        weights=DEFAULT_COURSE_WEIGHTS,
    ),
}

DEFAULT_ALGORITHM = "algo1_modulo3_baseline"

