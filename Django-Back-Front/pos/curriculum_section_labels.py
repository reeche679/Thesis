"""
Section index ↔ year/semester labels for Generate POS and curriculum UI mapping.

Must stay aligned with legacy `pos_pdf.POS_CURRICULUM` section order (0 = 1st Year First Sem, …).
"""

from __future__ import annotations

# Order matches POS_CURRICULUM: 0=1st Y 1st Sem, 1=1st Y 2nd Sem, 2=1st Y Midterm, …
INDEX_TO_YEAR_SEM: list[tuple[str, str]] = [
    ("1st Year", "First Semester"),
    ("1st Year", "Second Semester"),
    ("1st Year", "Midterm"),
    ("2nd Year", "First Semester"),
    ("2nd Year", "Second Semester"),
    ("2nd Year", "Midterm"),
    ("3rd Year", "First Semester"),
    ("3rd Year", "Second Semester"),
    ("3rd Year", "Midterm"),
    ("4th Year", "First Semester"),
    ("4th Year", "Second Semester"),
    ("4th Year", "Midterm"),
    ("5th Year", "First Semester"),
    ("5th Year", "Second Semester"),
    ("5th Year", "Midterm"),
    ("6th Year", "First Semester"),
    ("6th Year", "Second Semester"),
    ("6th Year", "Midterm"),
    ("7th Year", "First Semester"),
    ("7th Year", "Second Semester"),
    ("7th Year", "Midterm"),
    ("8th Year", "First Semester"),
    ("8th Year", "Second Semester"),
    ("8th Year", "Midterm"),
    ("9th Year", "First Semester"),
    ("9th Year", "Second Semester"),
    ("9th Year", "Midterm"),
    ("10th Year", "First Semester"),
    ("10th Year", "Second Semester"),
    ("10th Year", "Midterm"),
]

YEAR_SEM_TO_INDEX: dict[tuple[str, str], int] = {pair: i for i, pair in enumerate(INDEX_TO_YEAR_SEM)}
