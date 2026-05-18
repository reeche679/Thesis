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


def format_section_label(year_level: str, semester: str) -> str:
    """Join year and semester for UI/PDF. Do not use str.title() — it breaks ordinals (1st → 1St)."""
    y = (year_level or "").strip()
    s = (semester or "").strip()
    if y and s:
        return f"{y} - {s}"
    return y or s


def ordinal_year_label(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf} Year"


def section_meta_for_index(section_index: int) -> tuple[str, str, str]:
    """Return (year_level, semester, label) for a 0-based curriculum section index."""
    if 0 <= section_index < len(INDEX_TO_YEAR_SEM):
        yl, sem = INDEX_TO_YEAR_SEM[section_index]
        return yl, sem, format_section_label(yl, sem)
    rel = section_index - len(INDEX_TO_YEAR_SEM)
    year_num = rel // 3 + 11
    band = rel % 3
    sem_names = ("First Semester", "Second Semester", "Midterm")
    yl = ordinal_year_label(year_num)
    sem = sem_names[band]
    return yl, sem, format_section_label(yl, sem)


def format_legacy_pos_section_label(title1: str, title2: str) -> str:
    """ALL-CAPS legacy POS_CURRICULUM headings (safe for str.title — no numeric ordinals)."""
    t1 = (title1 or "").replace(" ", " ").strip().title()
    t2 = (title2 or "").replace(" ", " ").strip().title()
    return format_section_label(t1, t2)
