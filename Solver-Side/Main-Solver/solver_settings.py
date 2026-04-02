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
CONFIGURATION = "config1_baseline"

# Alternative algorithm selector:
# - None -> use pure CP-SAT configuration flow
# - "discrete_opt" -> use discrete-optimization simulated annealing branch
# - "cpmpy_minizinc" -> use CPMpy exact model with MiniZinc backend (Chuffed)
ALTERNATIVE_ALGORITHM = None

# Graduation trio enforcement switch
ENFORCE_GRADUATION_TRIO_ONLY_SEMESTER = True