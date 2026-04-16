"""
POS scheduling configurations.

Each configuration is implemented as a function that adds constraints (and
optionally influences the objective) to the CP-SAT model. The modulo-3
(term-type-aligned) configuration is the baseline offering rule used by the
main solver.
"""

from dataclasses import dataclass
from typing import Callable

# ---------------------------------------------------------------------------
# Weights (Configuration 2 input)
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
    "ENGG 101": 10,
}

# ---------------------------------------------------------------------------
# Configuration 1: Term-type-aligned (Modulo-3) offering constraints
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
                # For this configuration, treat bottlenecks as always petition-enabled:
                # allow them in any regular term from current_sem to cap_sem.
                valid_sems.append(s)
        if valid_sems:
            model.AddAllowedAssignments([semester[c]], [[s] for s in valid_sems])


def add_category_without_modulo3_offering_constraints(
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
    Same problem class as the baseline, but **no modulo-3 term-type domain reduction**.

    Categories still matter, without using (semester % 3) vs (orig % 3):

    - **Rigid**: semesters s with max(current_sem, first_offering_sem) ≤ s ≤ max_semester
      (petition does not change Rigid — same as modulo-3 baseline).
    - **Bottleneck** (no petition): same continuous floor as Rigid (s ≥ lo).
    - **Bottleneck** (with petition): full [current_sem, max_semester] (same as baseline).
    - **Flexible** (no petition): same continuous floor as Rigid (s ≥ lo).
    - **Flexible** (with petition): full [current_sem, max_semester] (extra placement freedom
      when petition mode is on; baseline only used petition for Bottleneck, but config0
      needs a visible petition switch without modulo-3 slots).

    Use next to config1 to measure the effect of modulo-3 slot restriction alone.
    """
    for c, info in courses.items():
        if c in passed_courses:
            continue
        orig = first_semester_fn(info) or 1
        cat = info.get("category", "Rigid")
        lo = max(current_sem, orig)

        strict = [s for s in range(current_sem, max_semester + 1) if s >= lo]

        if cat == "Bottleneck" and with_petition:
            valid_sems = list(range(current_sem, max_semester + 1))
        elif cat == "Flexible" and with_petition:
            valid_sems = list(range(current_sem, max_semester + 1))
        else:
            # Rigid; Flexible without petition; Bottleneck without petition; unknown category
            valid_sems = strict

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
    Baseline objective:

    1) Minimize the latest semester used (finish as early as possible).
    2) Minimize total deviation from orig_sem (keep courses near their
       original curriculum position whenever possible).
    3) Tie-break by minimizing sum(semester[c]) to pack courses earlier.

    `weights` is accepted for API compatibility but ignored here.
    """
    remaining = [c for c in courses if c not in passed_courses]
    n = max(1, len(remaining))

    max_sem_used = model.NewIntVar(current_sem, max_semester, "max_sem_used")
    for c in remaining:
        model.Add(max_sem_used >= semester[c])

    deviation_vars = []
    for c in remaining:
        orig = courses[c].get("orig_sem", 1)
        dev = model.NewIntVar(0, max_semester, f"dev_{c}")
        model.Add(dev >= semester[c] - orig)
        model.Add(dev >= orig - semester[c])
        deviation_vars.append(dev)

    deviation_sum = sum(deviation_vars) if deviation_vars else 0
    plain_sum = sum(semester[c] for c in remaining)

    # Scaling: primary (max_sem) > deviation > plain_sum
    R = n * max_semester
    dev_scale = R + 1
    primary_scale = dev_scale * R + R + 1

    model.Minimize(
        max_sem_used * primary_scale
        + deviation_sum * dev_scale
        + plain_sum
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
    Configuration 2 (Weighted-priority objective):

    1) Minimize the latest semester used (finish as early as possible).
    2) Minimize total deviation from orig_sem (keep courses near their
       original curriculum position whenever possible).
    3) Tie-break by minimizing sum(weights[c] * semester[c]).

    Higher weights push those courses earlier whenever constraints allow,
    but only when doing so doesn't increase deviation from orig_sem.
    """
    if weights is None:
        weights = DEFAULT_COURSE_WEIGHTS

    remaining = [c for c in courses if c not in passed_courses]
    max_weight = max([1, *[int(weights.get(c, 1)) for c in remaining]])
    n = max(1, len(remaining))

    max_sem_used = model.NewIntVar(current_sem, max_semester, "max_sem_used")
    for c in remaining:
        model.Add(max_sem_used >= semester[c])

    deviation_vars = []
    for c in remaining:
        orig = courses[c].get("orig_sem", 1)
        dev = model.NewIntVar(0, max_semester, f"dev_{c}")
        model.Add(dev >= semester[c] - orig)
        model.Add(dev >= orig - semester[c])
        deviation_vars.append(dev)

    deviation_sum = sum(deviation_vars) if deviation_vars else 0
    weighted_sum = sum(int(weights.get(c, 1)) * semester[c] for c in remaining)

    # Scaling: primary (max_sem) > deviation > weighted_sum
    R_w = n * max_weight * max_semester
    R_d = n * max_semester
    dev_scale = R_w + 1
    primary_scale = dev_scale * R_d + R_w + 1

    model.Minimize(
        max_sem_used * primary_scale
        + deviation_sum * dev_scale
        + weighted_sum
    )


# ---------------------------------------------------------------------------
# Single-selection registry (one name = one full configuration)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfigurationSpec:
    """
    One configuration = one offering rule + one objective rule (+ optional weights).
    """

    offering_fn: Callable
    objective_fn: Callable
    weights: dict | None = None


CONFIGURATIONS = {
    # Configuration 0: same baseline objective as config1, categories + orig_sem floor,
    # but **no** modulo-3 term-type reduction — compare search domain / runtime vs config1.
    "config0_no_modulo3_baseline": ConfigurationSpec(
        offering_fn=add_category_without_modulo3_offering_constraints,
        objective_fn=add_baseline_objective,
        weights=None,
    ),
    # Configuration 1: original behavior (modulo-3 offering + baseline objective)
    "config1_baseline": ConfigurationSpec(
        offering_fn=add_term_type_aligned_offering_constraints,
        objective_fn=add_baseline_objective,
        weights=None,
    ),
    # Configuration 2: modulo-3 offering + weighted-priority objective
    "config2_weighted": ConfigurationSpec(
        offering_fn=add_term_type_aligned_offering_constraints,
        objective_fn=add_weighted_priority_objective,
        weights=DEFAULT_COURSE_WEIGHTS,
    ),
    # Configuration 3: capped horizon + trio fixed at 5th Year 2nd Sem + weighted objective
    "config3_weightedtriocap": ConfigurationSpec(
        offering_fn=add_capped_petition_trio_offering_constraints,
        objective_fn=add_weighted_priority_objective,
        weights=DEFAULT_COURSE_WEIGHTS,
    ),
}

DEFAULT_CONFIGURATION = "config1_baseline"

