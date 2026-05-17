"""
Centralized solver settings for CP-SAT runtime.

Edit these values when you want backend-only changes to solver behavior.
"""

"POSSIBLE_CONFIGURATIONS:"

"config0_no_modulo3_baseline"
"config1_baseline"
"config2_weighted"
"config3_weightedtriocap"

# CP-SAT configuration key (must exist in Configurations.CONFIGURATIONS)
CONFIGURATION = "config2_weighted"

# Alternative algorithm selector:
# - None -> use pure CP-SAT configuration flow
# - "discrete_opt" -> use discrete-optimization simulated annealing branch
# - "cpmpy_minizinc" -> use CPMpy exact model with MiniZinc backend (Chuffed)
ALTERNATIVE_ALGORITHM = None

# Graduation trio enforcement switch
ENFORCE_GRADUATION_TRIO_ONLY_SEMESTER = False

# CP-SAT: optional course codes to include in ``constraint_build_trace`` on solver responses
# (also overridable per request via payload ``cp_sat_constraint_trace_courses`` or env
# ``CP_SAT_CONSTRAINT_TRACE_COURSES`` comma-separated).
# Note: one course must be a 1-tuple — use a trailing comma: ``("MATH 101",)``.
# ``("MATH 101")`` is just the string ``"MATH 101"`` and breaks trace resolution.
CP_SAT_CONSTRAINT_TRACE_COURSES: tuple[str, ...] = ("MATH 101", "MATH 102", "PHYS 102")