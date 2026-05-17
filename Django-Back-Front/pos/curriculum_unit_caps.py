"""
Validate and build ``max_units_by_semester_index`` from a staff-edited
Year 1–6 × (1st / 2nd / Summer) unit-cap grid.

The grid lives in ``draft_json["max_units_by_year_term"]`` during draft
editing and is converted to the flat ``max_units_by_semester_index`` list
at approve time.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError

YEARS = range(1, 7)       # Year levels 1–6
TERMS = range(1, 4)       # 1st semester, 2nd semester, summer
TOTAL_CELLS = 18          # 6 years × 3 terms
OVERFLOW_SEMESTERS = 6


def sem_index(year_level: int, term_in_year: int) -> int:
    """Global semester index (1-based) from (year_level, term_in_year)."""
    return (year_level - 1) * 3 + term_in_year


def year_and_term(sem: int) -> tuple[int, int]:
    """Inverse of ``sem_index``: (year_level, term_in_year)."""
    year_level = (sem - 1) // 3 + 1
    term_in_year = ((sem - 1) % 3) + 1
    return year_level, term_in_year


def validate_max_units_by_year_term(data) -> dict[int, dict[int, int]]:
    """
    Validate and normalize a ``max_units_by_year_term`` dict (from draft JSON).

    Returns ``{year: {term: cap, ...}, ...}`` with integer keys.
    Raises ``ValidationError`` when incomplete or invalid.
    """
    if not isinstance(data, dict):
        raise ValidationError(
            "max_units_by_year_term must be an object with keys \"1\"–\"6\"."
        )
    out: dict[int, dict[int, int]] = {}
    for y in YEARS:
        key = str(y)
        year_obj = data.get(key) or data.get(y)
        if not isinstance(year_obj, dict):
            raise ValidationError(
                f"max_units_by_year_term[\"{y}\"] is required (Year {y} caps)."
            )
        inner: dict[int, int] = {}
        for t in TERMS:
            tkey = str(t)
            val = year_obj.get(tkey) if year_obj.get(tkey) is not None else year_obj.get(t)
            if val is None or val == "":
                term_label = {1: "1st semester", 2: "2nd semester", 3: "summer"}[t]
                raise ValidationError(
                    f"Max units for Year {y} {term_label} is required."
                )
            try:
                v = int(val)
            except (TypeError, ValueError):
                raise ValidationError(
                    f"Max units for Year {y} term {t} must be an integer."
                ) from None
            if v < 0:
                raise ValidationError(
                    f"Max units for Year {y} term {t} cannot be negative."
                )
            inner[t] = v
        out[y] = inner
    return out


def build_max_units_by_semester_index(
    year_term_map: dict[int, dict[int, int]],
    *,
    max_orig: int,
    overflow_semesters: int = OVERFLOW_SEMESTERS,
) -> list[int]:
    """
    Build the flat ``max_units_by_semester_index`` list for ``approved_json``.

    * Semesters 1–18 are filled from ``year_term_map`` (Y1–Y6 × 3 terms).
    * Additional overflow semesters (beyond max of 18 or ``max_orig``) reuse
      the **Year 6** caps by matching ``term_in_year``.
    """
    base: list[int] = []
    for y in YEARS:
        for t in TERMS:
            base.append(year_term_map[y][t])

    horizon = max(len(base), max_orig) + overflow_semesters
    while len(base) < horizon:
        s = len(base) + 1
        _, t = year_and_term(s)
        base.append(year_term_map[6][t])

    return base
