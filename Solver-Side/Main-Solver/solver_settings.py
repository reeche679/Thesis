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
# - "local_search" | "fuzzy_logic" | "tabu_search" | "firefly" -> use heuristic flow
# Speed vs quality: edit *Config dataclass defaults in
# algorithms_and_helpers/alternative_algorithms.py (iterations, early_stop_on_feasible, etc.).
ALTERNATIVE_ALGORITHM = "local_search"

# Graduation trio enforcement switch
ENFORCE_GRADUATION_TRIO_ONLY_SEMESTER = True

