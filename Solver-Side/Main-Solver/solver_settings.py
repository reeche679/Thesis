"""
Centralized solver settings for CP-SAT runtime.

Edit these values when you want backend-only changes to solver behavior.
"""

# CP-SAT configuration key (must exist in Configurations.CONFIGURATIONS)
CONFIGURATION = "config1_baseline"

# Alternative algorithm selector:
# - None -> use pure CP-SAT configuration flow
# - "local_search" | "fuzzy_logic" | "tabu_search" -> use heuristic flow
ALTERNATIVE_ALGORITHM = "fuzzy_logic"

# Graduation trio enforcement switch
ENFORCE_GRADUATION_TRIO_ONLY_SEMESTER = True

