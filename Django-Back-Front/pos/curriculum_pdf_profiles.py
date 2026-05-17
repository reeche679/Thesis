"""
PDF extraction template profiles (catalog POS, evaluation worksheet, future formats).

Profiles are detected per table and merged at document level. Catalog behavior is the
default when detection is inconclusive so BSEE-style 8-column POS PDFs stay unchanged.
"""

from __future__ import annotations

import re
from typing import Any

# Stable ids stored in extraction_meta["template_profile"]
PROFILE_CATALOG_8COL = "catalog_pos_8col"
PROFILE_EVALUATION_5COL = "evaluation_pos_5col"
PROFILE_AUTO = "auto"

DEFAULT_PROFILE = PROFILE_CATALOG_8COL

KNOWN_PROFILES: tuple[str, ...] = (
    PROFILE_CATALOG_8COL,
    PROFILE_EVALUATION_5COL,
)

# Footer / signature rows (evaluation PDFs page 4; harmless on catalog)
_FOOTER_ROW = re.compile(
    r"(?is)\b(?:evaluated\s+by|program\s+chair|college\s+dean|noted\s+by|"
    r"university\s+registrar|signature\s+over\s+printed|adviser|advisor)\b",
)

# Filled evaluation grade scale (1.25, 5.00) — not whole-number credit units (3, 4)
_GRADE_VALUE = re.compile(r"^\d\.\d{1,2}$")


def _norm_header(cell: str) -> str:
    s = str(cell).lower().replace("\n", " ")
    s = re.sub(r"-\s+", "-", s)
    return re.sub(r"\s+", " ", s).strip()


def header_categories_from_row(row: list[Any | None]) -> set[str]:
    """Recognized logical columns in a header row (course, title, units, …)."""
    from pos.curriculum_pdf_extract import _header_cell_category, _norm_cell

    cats: set[str] = set()
    for c in row or []:
        cat = _header_cell_category(_norm_cell(c))
        if cat:
            cats.add(cat)
    return cats


def detect_table_profile(table: list[list[Any | None]]) -> str:
    """
    Pick the best profile for one pdfplumber table.

    Evaluation: Unit/s + combined prereq and/or Grade, no Lec/Lab/Category banner.
    Catalog: Lec/Lab/Category or classic 8-col credit-units grid (BSEE POS).
    """
    if not table:
        return DEFAULT_PROFILE
    from pos.curriculum_pdf_extract import _detect_header_row_index_and_score, _norm_cell

    hri, score = _detect_header_row_index_and_score(table)
    if score < 2:
        w = 0
        for r in table[:8]:
            if r:
                w = max(w, len(r))
        if 4 <= w <= 6:
            return PROFILE_EVALUATION_5COL
        if w >= 7:
            return PROFILE_CATALOG_8COL
        return DEFAULT_PROFILE

    row = table[hri] if hri < len(table) else []
    cats = header_categories_from_row(row)
    blob = " ".join(_norm_header(_norm_cell(c)) for c in row if _norm_cell(c))

    has_units = "units" in cats or "unit/s" in blob or re.search(r"\bunit\b", blob)
    has_grade = "grade" in cats or re.search(r"\bgrade\b", blob)
    has_lec_lab = "lec" in cats or "lab" in cats
    has_category = "category" in blob and "categor" in blob
    combined_prereq = (
        "prereq" in cats
        and ("co-requisite" in blob or "co requisite" in blob or "/" in blob)
        and "coreq" not in cats
    )

    if has_units and (has_grade or combined_prereq) and not has_lec_lab and not has_category:
        return PROFILE_EVALUATION_5COL
    if has_lec_lab or has_category:
        return PROFILE_CATALOG_8COL
    if has_units and ("prereq" in cats or "coreq" in cats) and not has_grade:
        return PROFILE_CATALOG_8COL
    if score >= 3 and has_units:
        return PROFILE_CATALOG_8COL
    return DEFAULT_PROFILE


def merge_document_profile(table_profiles: list[str]) -> str:
    """Majority vote; tie → catalog (preserve legacy default)."""
    if not table_profiles:
        return DEFAULT_PROFILE
    counts: dict[str, int] = {}
    for p in table_profiles:
        counts[p] = counts.get(p, 0) + 1
    best = max(counts.items(), key=lambda kv: (kv[1], kv[0] == PROFILE_CATALOG_8COL))
    return best[0]


def profile_from_draft_json(draft_json: dict[str, Any] | None) -> str:
    if not draft_json:
        return DEFAULT_PROFILE
    meta = draft_json.get("extraction_meta") or {}
    p = meta.get("template_profile")
    if isinstance(p, str) and p in KNOWN_PROFILES:
        return p
    return DEFAULT_PROFILE


def infer_evaluation_col_map(n_cols: int) -> dict[str, int]:
    """5-column CpE evaluation worksheet (code, title, units, prereq, grade)."""
    if n_cols < 4:
        return {}
    m: dict[str, int] = {"course": 0, "title": 1, "units": 2, "prereq": 3}
    if n_cols >= 5:
        m["grade"] = 4
    return m


def expand_runon_heading(label: str) -> str:
    """FIRSTYEAR / FirstSemester → spaced tokens for canonical parsers."""
    x = (label or "").strip()
    x = re.sub(
        r"(?i)((?:first|second|third|fourth|fifth|sixth|1st|2nd|3rd|4th|5th|6th))(?=year(?:\b|$))",
        r"\1 ",
        x,
    )
    x = re.sub(
        r"(?i)((?:first|second|1st|2nd))(?=sem(ester)?(?:\b|$))",
        r"\1 ",
        x,
    )
    x = re.sub(r"(?i)\bmidterm\s+semester\b", "Midterm", x)
    return re.sub(r"\s+", " ", x).strip()


def normalize_term_label(raw: str, profile: str | None = None) -> str:
    """
    Map band labels to canonical ``1st Year - First Semester`` form when possible.

    Idempotent for labels already on the EE/CpE grid. Safe for catalog POS headings.
    """
    from pos.canonical_term_grid import orig_sem_from_term_label

    s = re.sub(r"\s+", " ", (raw or "").strip())
    if not s:
        return s
    if orig_sem_from_term_label(s) is not None:
        return _format_canonical_label(s)
    expanded = expand_runon_heading(s)
    if expanded != s and orig_sem_from_term_label(expanded) is not None:
        return _format_canonical_label(expanded)
    if profile == PROFILE_EVALUATION_5COL or _looks_like_runon_band(s):
        if orig_sem_from_term_label(expanded) is not None:
            return _format_canonical_label(expanded)
    return s


def _looks_like_runon_band(s: str) -> bool:
    c = re.sub(r"\s+", "", s.lower())
    return bool(
        re.search(r"^(?:first|second|third|fourth|fifth|sixth|1st|2nd|3rd|4th|5th|6th)year", c)
        or re.search(r"semester", s, re.I)
    )


def _format_canonical_label(text: str) -> str:
    from pos.canonical_term_grid import _parse_year_sem_from_label

    parsed = _parse_year_sem_from_label(expand_runon_heading(text))
    if not parsed:
        return text
    ykey, sem_key = parsed
    return f"{ykey} - {sem_key}"


def is_footer_or_signature_row(joined: str) -> bool:
    return bool(_FOOTER_ROW.search(joined or ""))


def is_semester_total_only_row(cells: list[str], *, has_course_col: bool) -> bool:
    """Lone semester unit total (25, 23, 9) — not a course."""
    if not cells:
        return False
    nonempty = [c.strip() for c in cells if c and c.strip()]
    if not nonempty:
        return False
    if has_course_col and any(_looks_like_course_code(c) for c in nonempty):
        return False
    if len(nonempty) == 1 and re.fullmatch(r"\d{1,2}", nonempty[0]):
        v = int(nonempty[0])
        return 8 <= v <= 30
    if all(re.fullmatch(r"\d{1,2}", c) for c in nonempty) and len(nonempty) <= 2:
        return True
    return False


def _looks_like_course_code(cell: str) -> bool:
    from pos.curriculum_pdf_extract import _find_code_in_text

    return _find_code_in_text(cell) is not None


def looks_like_grade_not_units(value: str, *, profile: str) -> bool:
    """Filled evaluation PDF: decimal grades (1.25, 5.00) must not become units."""
    if profile != PROFILE_EVALUATION_5COL:
        return False
    s = (value or "").strip()
    if not _GRADE_VALUE.match(s):
        return False
    try:
        v = float(s)
    except ValueError:
        return False
    return 0.0 < v <= 5.0


def split_combined_requisite_cell(raw: str) -> tuple[str, str]:
    """
    Split combined Pre-requisite / Co-requisite column (evaluation template).

    v1: slash/semicolon/newline → prereq list; explicit co-req section → coreq.
    """
    s = (raw or "").strip()
    if not s:
        return "", ""
    low = s.lower()
    co_marker = re.search(r"(?is)\bco-?\s*requisite\s*[:\-]?\s*", s)
    if co_marker:
        pre_part = s[: co_marker.start()].strip(" /;,-")
        co_part = s[co_marker.end() :].strip()
        return pre_part, co_part
    if "/" in s or ";" in s or "\n" in s:
        return s, ""
    return s, ""


def should_skip_ocr_for_profile(
    profile: str,
    extracted_count: int,
    total_text_chars: int,
) -> bool:
    """Native-text evaluation worksheets rarely need Tesseract."""
    if profile != PROFILE_EVALUATION_5COL:
        return False
    return extracted_count >= 8 and total_text_chars >= 1500
