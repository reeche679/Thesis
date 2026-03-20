"""
Maximum allowed units per term.

Whatever units you put here is the *cap* for that (year, term_in_year):
the student can go below it, but never above it.
"""

# Term numbering within an academic year (optional helpers)
TERM_1 = 1  # 1st semester
TERM_2 = 2  # 2nd semester
TERM_SUMMER = 3  # summer / midyear


def sem_index(year_level: int, term_in_year: int) -> int:
    """Convert (year_level, term_in_year) -> global semester index (1..MAX_SEMESTER)."""
    return (year_level - 1) * 3 + term_in_year


def year_and_term_from_sem(sem: int) -> tuple[int, int]:
    """Convert global semester index (1..MAX_SEMESTER) -> (year_level, term_in_year)."""
    year_level = (sem - 1) // 3 + 1
    term_in_year = ((sem - 1) % 3) + 1
    return year_level, term_in_year


# Max units per (year_level, term_in_year).
# These are the *only* numbers that matter for your cap logic.
MAX_UNITS_BY_YEAR_TERM = {
    (1, TERM_1): 24,
    (1, TERM_2): 26,
    (1, TERM_SUMMER): 9,

    (2, TERM_1): 27,
    (2, TERM_2): 23,
    (2, TERM_SUMMER): 9,

    (3, TERM_1): 21,
    (3, TERM_2): 23,
    (3, TERM_SUMMER): 5,

    (4, TERM_1): 19,
    (4, TERM_2): 21,
    (4, TERM_SUMMER): 9,

    (5, TERM_1): 21,
    (5, TERM_2): 23,
    (5, TERM_SUMMER): 9,

    (6, TERM_1): 21,
    (6, TERM_2): 23,
    (6, TERM_SUMMER): 9,

}

# Planning horizon: highest semester index (CP-SAT will use semesters 1..MAX_SEMESTER).
MAX_SEMESTER = max(sem_index(yl, t) for (yl, t) in MAX_UNITS_BY_YEAR_TERM) if MAX_UNITS_BY_YEAR_TERM else 14


def max_units_for(year_level: int, term_in_year: int) -> int:
    """Maximum allowed units for a given (year, term)."""
    return MAX_UNITS_BY_YEAR_TERM.get((year_level, term_in_year), 0)


def max_units_for_sem(sem: int) -> int:
    """Maximum allowed units for a global semester index (1..MAX_SEMESTER)."""
    year_level, term_in_year = year_and_term_from_sem(sem)
    return max_units_for(year_level, term_in_year)


# Backwards-compatible alias name if you prefer to call it units_for(...)
units_for = max_units_for