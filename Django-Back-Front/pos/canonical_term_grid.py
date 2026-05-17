"""
EE/CpE v1: map PDF ``term_label`` / ``term`` strings to canonical curriculum slots.

Slots match :data:`pos.curriculum_section_labels.INDEX_TO_YEAR_SEM` order; ``orig_sem``
is 1-based (section_index + 1). This fixes compressed ``term_index`` when the PDF omits
an entire band (e.g. empty 2nd-year midterm) but later rows still carry a correct label.
"""

from __future__ import annotations

import re
from typing import Any

from pos.curriculum_section_labels import YEAR_SEM_TO_INDEX

_YEAR_NUM_PATTERNS: list[tuple[int, re.Pattern[str]]] = [
    (1, re.compile(r"\b(?:1\s*st|1st|first)\s+year\b", re.I)),
    (2, re.compile(r"\b(?:2\s*nd|2nd|second)\s+year\b", re.I)),
    (3, re.compile(r"\b(?:3\s*rd|3rd|third)\s+year\b", re.I)),
    (4, re.compile(r"\b(?:4\s*th|4th|fourth)\s+year\b", re.I)),
    (5, re.compile(r"\b(?:5\s*th|5th|fifth)\s+year\b", re.I)),
    (6, re.compile(r"\b(?:6\s*th|6th|sixth)\s+year\b", re.I)),
    (7, re.compile(r"\b(?:7\s*th|7th|seventh)\s+year\b", re.I)),
    (8, re.compile(r"\b(?:8\s*th|8th|eighth)\s+year\b", re.I)),
    (9, re.compile(r"\b(?:9\s*th|9th|ninth)\s+year\b", re.I)),
    (10, re.compile(r"\b(?:10\s*th|10th|tenth)\s+year\b", re.I)),
]

_SEM_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("First Semester", re.compile(r"\b(?:first|1\s*st|1st)\s+sem(?:ester)?\b", re.I)),
    ("Second Semester", re.compile(r"\b(?:second|2\s*nd|2nd)\s+sem(?:ester)?\b", re.I)),
    ("Midterm", re.compile(r"\bmidterm\b", re.I)),
]


def _ordinal_year_label(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf} Year"


def _parse_year_sem_from_label(text: str) -> tuple[str, str] | None:
    """
    Return (year_key, semester_key) matching INDEX_TO_YEAR_SEM tuple strings, or None.
    """
    s = re.sub(r"\s+", " ", (text or "").strip())
    if len(s) < 8:
        return None
    year_n: int | None = None
    for n, pat in _YEAR_NUM_PATTERNS:
        if pat.search(s):
            year_n = n
            break
    if year_n is None:
        return None
    ykey = _ordinal_year_label(year_n)
    sem_key: str | None = None
    for sk, pat in _SEM_PATTERNS:
        if pat.search(s):
            sem_key = sk
            break
    if sem_key is None:
        return None
    return (ykey, sem_key)


def orig_sem_from_term_label(raw: str) -> int | None:
    """
    If ``raw`` matches a canonical (year, semester) band, return 1-based ``orig_sem``.
    Otherwise return None (caller uses ``term_index`` map).
    """
    parsed = _parse_year_sem_from_label(raw)
    if not parsed:
        return None
    idx = YEAR_SEM_TO_INDEX.get(parsed)
    if idx is None:
        return None
    return int(idx) + 1


def year_level_from_orig_sem(orig_sem: int) -> int:
    """Three bands per calendar year in INDEX_TO_YEAR_SEM (v1 EE/CpE)."""
    o = max(1, int(orig_sem))
    return (o - 1) // 3 + 1


def year_level_from_term_label(raw: str) -> int | None:
    """Parse academic year ordinal from label if possible (independent of orig_sem)."""
    s = re.sub(r"\s+", " ", (raw or "").strip())
    for n, pat in _YEAR_NUM_PATTERNS:
        if pat.search(s):
            return max(1, min(n, 10))
    return None


def term_label_for_row(row: dict[str, Any]) -> str:
    """Best-effort band label from an extracted draft row."""
    for k in ("term_label", "term"):
        v = row.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""
