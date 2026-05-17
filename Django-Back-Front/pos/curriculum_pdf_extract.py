"""
Phase 5 — machine-assisted draft extraction from curriculum PDFs.

Uses pdfplumber for text/tables; when little text or few rows are found, falls back to
PyMuPDF rasterization + Tesseract OCR (requires Tesseract on the server PATH).

term_index: positional band index (1-based) in top-to-bottom PDF order. Each **distinct**
structural semester / year-band heading increments the counter once (repeated identical
headings are deduped). It is not a calendar-normalized “semester number” from a lookup table.

See README Phase 5 for column assumptions and backfill behavior.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Course codes: MATH 101, CpE 203, GEd 108, PATHFit 3 (1–3 digit numbers), NSTP 111
_CODE_TOKEN = re.compile(
    r"\b([A-Za-z]{2,10})\s*(\d{1,3}[A-Za-z]?)\b",
)

# Subject part → preferred display (keys are lowercase). Covers OCR/PDF casing drift.
_SUBJECT_CANONICAL_BY_LOWER: dict[str, str] = {
    "pathfit": "PATHFit",
    "nstp": "NSTP",
    "math": "MATH",
    "phys": "PHYS",
    "chem": "CHEM",
    "stat": "STAT",
    "bio": "BIO",
    "engg": "ENGG",
    "ged": "GEd",
    "gede": "GEdE",
    "cpe": "CpE",
    "ece": "ECE",
    "eee": "EEE",
    "ee": "EE",
    "me": "ME",
    "ice": "ICE",
    "ai": "AI",
    "ce": "CE",
    "ie": "IE",
    "se": "SE",
    "cs": "CS",
    "it": "IT",
    "pe": "PE",
    "fil": "FIL",
    "litr": "Litr",
    "acct": "ACCT",
    "econ": "ECON",
    "hum": "HUM",
    "hist": "HIST",
    "pols": "POLS",
    "psyc": "PSYC",
    "soc": "SOC",
    "theo": "THEO",
    "rel": "REL",
    "engl": "ENGL",
    "drmath": "DRMATH",
    "sci": "SCI",
    "scl": "SCI",
}


def _canonical_course_code(code: str) -> str:
    """
    Normalize parsed codes for stable keys and UI.

    Known subjects (NSTP, MATH, GEd, CpE, PATHFit, …) map from any letter casing.
    Unlisted subjects keep the substring matched from the PDF (e.g. unusual dept codes).
    """
    s = re.sub(r"\s+", " ", (code or "").strip())
    m = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,3}[A-Za-z]?)", s)
    if not m:
        return s
    subj, num = m.group(1), m.group(2)
    canon = _SUBJECT_CANONICAL_BY_LOWER.get(subj.lower())
    if canon is not None:
        return f"{canon} {num}"
    return f"{subj} {num}"


# Section band (no course code row): year + semester or midterm
_SECTION_HEADER = re.compile(
    r"(?is)(?:"
    r"(?:first|second|third|fourth|fifth|sixth|1st|2nd|3rd|4th|5th|6th)\s+year.{0,80}"
    r"(?:first|second|1st|2nd)\s+sem(?:ester)?"
    r"|"
    r"(?:first|second|1st|2nd)\s+sem(?:ester)?\b.{0,80}"
    r"(?:first|second|third|fourth|fifth|sixth|1st|2nd|3rd|4th|5th|6th)\s+year"
    r"|"
    r"(?:first|second|third|fourth|fifth|sixth|1st|2nd|3rd|4th|5th|6th)\s+year.{0,20}midterm"
    r"|"
    r"\bmidterm\b.{0,30}(?:first|second|third|fourth|fifth|sixth|1st|2nd)\s+year"
    r"|"
    r"(?:first|second|third|fourth|fifth|sixth|1st|2nd|3rd|4th|5th|6th)\s+year.{0,20}summer"
    r")",
)

# Legacy / loose term hint (text fallback)
_TERM_LINE = re.compile(
    r"(?i)(?:"
    r"(?:first|second|third|fourth|fifth|sixth|1st|2nd|3rd|4th|5th|6th)\b.{0,40}\b(?:year|semester|sem\.?|midterm)\b"
    r"|"
    r"\b(?:year|semester)\b.{0,8}[ivx\d]"
    r"|"
    r"table\s+of\s+courses|curriculum|course\s+code"
    r")",
)

_UNITS_HINT = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:units?|u\.?|cred)\b", re.I)
_MAX_UNITS_HINT = re.compile(r"(?i)(max|maximum|not\s+more\s+than|limit).{0,40}(\d+)")

# Prerequisite / co-requisite cells often list class-year rules (no DEPT 123 token).
_STANDING_OR_PROGRESSION = re.compile(
    r"(?is)(?:"
    r"\b(?:[1-6]|1st|2nd|3rd|4th|5th|6th|first|second|third|fourth|fifth|sixth)\s+year\s+standing\b"
    r"|"
    r"\b\d(?:st|nd|rd|th)\s+year\s+standing\b"
    r"|"
    r"\bgraduating\b"
    r"|"
    r"\bregular\s+standing\b"
    r"|"
    r"\badvanced\s+standing\b"
    r"|"
    r"\b(?:junior|senior)\s+standing\b"
    r")",
)

# Structural band headings (positional term_index); see README Phase 5
_STRUCTURAL_HEADING_LINE_MAX_LEN = 160
_STRUCTURAL_TABLE_JOINED_MAX_LEN = 280
_BAND_TERM_LINE_SIGNAL_MAX_LEN = 140

# Truncate text timelines at BatState-style “comparison vs CMO” annexes (not semester POS grids).
_POS_SECTION_END_HEADING = re.compile(
    r"(?i)^(?:[IVXLCDM]{1,8}\.?\s*)?(?:comparison\s+of\s+.*\bcmo\b|comparison\s+of\s+proposed\s+curriculum\b)",
)

# Header cell → logical column (first match wins per column index).
# "title" must come before "course": otherwise "Course Title" matches substring "course" first.
_HEADER_SYNONYMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("title", ("title", "description", "course title", "name of course", "course name")),
    ("course", ("course", "code", "subject", "crs", "course no", "course no.", "course\nno")),
    ("lec", ("lec", "lecture", "lec h", "lec\n")),
    ("lab", ("lab", "laboratory", "lab\n")),
    ("units", ("units", "unit", "unit/s", "unit(s)", "credit", "credits", "credit units", "total units", "cr", "cu", "hrs/wk", "no. of hour")),
    ("prereq", ("prereq", "pre-req", "prerequisite", "pre req", "pre-requisite", "pre-requisite / co-requisite", "pre-requisite/co-requisite")),
    ("coreq", ("coreq", "co-req", "corequisite", "co req", "co-requisite")),
    ("grade", ("grade", "final grade")),
    ("max", ("max", "maximum", "load", "max load")),
)


def _norm_cell(x: Any) -> str:
    if x is None:
        return ""
    return re.sub(r"\s+", " ", str(x)).strip()


def _truncate_combined_text_at_pos_section_end(full_text: str) -> tuple[str, bool]:
    """Drop comparison/CMO annex text so course timelines only see Program-of-Study content."""
    if not (full_text or "").strip():
        return full_text, False
    lines = full_text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or len(stripped) > 220:
            continue
        if _POS_SECTION_END_HEADING.search(stripped):
            return "\n".join(lines[:i]), True
    return full_text, False


def _table_looks_like_cmo_comparison_matrix(table: list[list[Any | None]]) -> bool:
    """
    Heuristic for pdfplumber tables that are “Existing vs Proposed vs CMO” matrices,
    not the semester course grid (BSEE POS PDFs append these on the same page as POS).
    """
    if not table or len(table) < 2:
        return False
    parts: list[str] = []
    for row in table[:12]:
        if not row:
            continue
        for c in row:
            s = _norm_cell(c).lower()
            if s and len(s) < 120:
                parts.append(s)
    blob = " ".join(parts)
    if "existing" not in blob or "proposed" not in blob:
        return False
    return "remarks" in blob or bool(re.search(r"\bcmo\b", blob))


def _norm_header_cell(cell: str) -> str:
    s = str(cell).lower().replace("\n", " ")
    s = re.sub(r"-\s+", "-", s)
    return re.sub(r"\s+", " ", s).strip()


def _header_cell_category(cell: str) -> str | None:
    cl = _norm_header_cell(cell)
    if not cl or len(cl) > 80:
        return None
    for cat, keys in _HEADER_SYNONYMS:
        for k in keys:
            if k in cl:
                return cat
    return None


def _table_width(table: list[list[Any | None]]) -> int:
    w = 0
    for r in table[:12]:
        if r:
            w = max(w, len(r))
    return w


def _detect_header_row_index_and_score(table: list[list[Any | None]]) -> tuple[int, int]:
    """Best banner/header row in the first few rows; score = count of recognized header cells."""
    best_ri = 0
    best_score = -1
    for ri in range(min(4, len(table))):
        row = table[ri]
        if not row:
            continue
        score = 0
        for c in row:
            if _header_cell_category(_norm_cell(c)):
                score += 1
        if score > best_score:
            best_score = score
            best_ri = ri
    return best_ri, best_score


def _first_row_looks_like_pos_course_grid_data(row: list[Any | None] | None) -> bool:
    """True when row 0 is likely a data row (code + title + digits), not a year/semester banner."""
    if not row:
        return False
    c0 = _norm_cell(row[0])
    if not c0 or _find_code_in_text(c0) is None:
        return False
    if re.match(r"(?i)^(total|subtotal)$", c0):
        return False
    if len(row) > 1:
        t = _norm_cell(row[1])
        if len(t) >= 8 and not re.fullmatch(r"\d+(?:\.\d+)?", t):
            return True
    if len(row) > 2 and re.fullmatch(r"\d+(?:\.\d+)?", _norm_cell(row[2]) or ""):
        return True
    return False


def _infer_pos_grid_col_map(n_cols: int) -> dict[str, int]:
    """8-column BatState-style continuation tables without a header row (code, title, units, lec, lab, …)."""
    if n_cols < 6:
        return {}
    m: dict[str, int] = {"course": 0, "title": 1, "units": 2, "lec": 3, "lab": 4, "prereq": 5}
    if n_cols >= 7:
        m["coreq"] = 6
    return m


def _build_col_map(
    table: list[list[Any | None]],
    warnings: list[str],
    *,
    profile: str | None = None,
) -> tuple[dict[str, int], str]:
    """Map logical field → column index from the best header row; return (col_map, profile)."""
    from pos.curriculum_pdf_profiles import (
        DEFAULT_PROFILE,
        PROFILE_EVALUATION_5COL,
        detect_table_profile,
        infer_evaluation_col_map,
    )

    if not table:
        return {}, profile or DEFAULT_PROFILE
    resolved_profile = profile or detect_table_profile(table)
    w = _table_width(table)
    hri, header_score = _detect_header_row_index_and_score(table)
    col_map: dict[str, int] = {}
    if header_score >= 2:
        row = table[hri] if hri < len(table) else []
        for j in range(w):
            cell = _norm_cell(row[j]) if j < len(row) else ""
            cat = _header_cell_category(cell)
            if cat and cat not in col_map:
                col_map[cat] = j
    elif _first_row_looks_like_pos_course_grid_data(table[0]):
        if resolved_profile == PROFILE_EVALUATION_5COL:
            inferred = infer_evaluation_col_map(w)
        else:
            inferred = _infer_pos_grid_col_map(w)
        if inferred:
            return inferred, resolved_profile
        row = table[0] if table else []
        for j in range(w):
            cell = _norm_cell(row[j]) if j < len(row) else ""
            cat = _header_cell_category(cell)
            if cat and cat not in col_map:
                col_map[cat] = j
    else:
        row = table[hri] if hri < len(table) else []
        for j in range(w):
            cell = _norm_cell(row[j]) if j < len(row) else ""
            cat = _header_cell_category(cell)
            if cat and cat not in col_map:
                col_map[cat] = j
    if resolved_profile == PROFILE_EVALUATION_5COL and not col_map and w >= 4:
        col_map = infer_evaluation_col_map(w)
    if "units" not in col_map:
        warnings.append("Table header: no UNITS column detected; units may use fallback heuristics.")
    return col_map, resolved_profile


def _get_cell(row: list[Any | None] | None, j: int | None) -> str:
    if row is None or j is None or j < 0:
        return ""
    if j < len(row):
        return _norm_cell(row[j])
    return ""


@dataclass
class _TermState:
    term_index: int = 0
    term_label: str = ""
    last_header_norm: str = ""
    pending_year_raw: str = ""

    def bump_if_new_section(self, label: str) -> None:
        label_clean = re.sub(r"\s+", " ", label).strip()
        if len(label_clean) < 6:
            return
        norm = label_clean.upper()
        if norm == self.last_header_norm:
            return
        self.last_header_norm = norm
        self.term_index += 1
        self.term_label = label_clean

    def clear_pending_year(self) -> None:
        self.pending_year_raw = ""

    def flush_pending_year_as_section(self) -> None:
        """If a year line was held for a following Semester/Midterm, commit it alone."""
        if self.pending_year_raw:
            self.bump_if_new_section(self.pending_year_raw)
            self.pending_year_raw = ""


def _collapse_heading_tokens(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").strip().lower())


# BSU-style PDFs often emit "FIRSTYEAR" / "FirstSemester" with no spaces in extract_text().
_RUNON_YEAR_ONLY = re.compile(r"^(?:first|second|third|fourth|fifth|sixth|1st|2nd|3rd|4th|5th|6th)year$")
_RUNON_SEM_ONLY = re.compile(r"^(?:first|second|1st|2nd)sem(ester)?$")
_RUNON_MIDTERM_ONLY = re.compile(r"^midterm$")


def _consume_runon_year_sem_heading_line(lt: str, state: _TermState) -> bool:
    """
    If lt is a run-on year / semester / midterm banner line, update state and return True.
    Pairs FIRSTYEAR + FirstSemester (or + Midterm) into a single bump with a combined label.
    """
    c = _collapse_heading_tokens(lt)
    if _RUNON_YEAR_ONLY.match(c):
        state.pending_year_raw = lt.strip()
        return True
    if _RUNON_SEM_ONLY.match(c):
        if state.pending_year_raw:
            state.bump_if_new_section(f"{state.pending_year_raw} {lt.strip()}")
            state.pending_year_raw = ""
        else:
            state.bump_if_new_section(lt.strip())
        return True
    if _RUNON_MIDTERM_ONLY.match(c):
        if state.pending_year_raw:
            state.bump_if_new_section(f"{state.pending_year_raw} {lt.strip()}")
            state.pending_year_raw = ""
        else:
            state.bump_if_new_section(lt.strip())
        return True
    return False


def _loose_year_sem_heading_line(line: str) -> bool:
    """BSU-style headings that split oddly in extract_text; no course code; short line."""
    lt = line.strip()
    if not lt or len(lt) > 140:
        return False
    if _find_code_in_text(lt):
        return False
    li = lt.lower()
    if "year" not in li or "sem" not in li:
        return False
    if not re.search(
        r"(?:first|second|third|fourth|fifth|sixth|1st|2nd|3rd|4th|5th|6th)\s+year",
        li,
    ):
        return False
    if not re.search(r"(?:first|second|1st|2nd)\s+sem(?:ester)?", li):
        return False
    return True


def _normalize_for_band_signal(text: str) -> str:
    """Collapse pdfplumber table cell separators so regexes see a single-line heading."""
    s = (text or "").strip()
    return re.sub(r"\s*\|\s*", " ", s)


def _expand_runon_year_sem_spaces(s: str) -> str:
    """Insert spaces so 'FIRSTYEAR' / 'FirstSemester' match spaced heading regexes."""
    x = (s or "").strip()
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
    return re.sub(r"\s+", " ", x).strip()


def _term_line_match_is_spurious(norm_text: str) -> bool:
    """
    _TERM_LINE includes loose tokens ('curriculum', 'course code') that appear in
    letterheads, emails, and repeated table column headers (BSEE-style POS PDFs).
    """
    low = norm_text.lower()
    if re.search(r"(?i)\bcourse\s+code\b", norm_text):
        if "course title" in low or ("credit" in low and "unit" in low):
            return True
        if "prerequisite" in low and "lec" in low:
            return True
    if "curriculum" in low:
        if re.search(r"curriculum\.[a-z0-9-]", low):
            return True
        if "office of curriculum" in low:
            return True
        if "@" in norm_text:
            return True
    return False


def _band_heading_signal(norm_text: str) -> bool:
    """Core year/semester/midterm signals for structural band headings (no guards)."""
    if not norm_text.strip():
        return False
    if _SECTION_HEADER.search(norm_text):
        return True
    if _loose_year_sem_heading_line(norm_text):
        return True
    expanded = _expand_runon_year_sem_spaces(norm_text)
    if expanded != norm_text.strip() and (
        _SECTION_HEADER.search(expanded) or _loose_year_sem_heading_line(expanded)
    ):
        return True
    if (
        _TERM_LINE.search(norm_text)
        and len(norm_text) <= _BAND_TERM_LINE_SIGNAL_MAX_LEN
        and not _term_line_match_is_spurious(norm_text)
    ):
        return True
    return False


def _is_section_header_joined(joined: str, _cells: list[str]) -> bool:
    """Table row is a section banner (merged cells), not a course row."""
    if _find_code_in_text(joined):
        return False
    lt = joined.strip()
    if re.search(r"(?i)\b(total|subtotal)\b", lt):
        return False
    if len(lt) > _STRUCTURAL_TABLE_JOINED_MAX_LEN:
        return False
    return _band_heading_signal(_normalize_for_band_signal(joined))


def _extract_codes_from_text(text: str) -> list[str]:
    if not text or not str(text).strip():
        return []
    t = str(text).strip()
    if re.match(r"(?i)^(none|n/?a|—|-|–|\.)$", t):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in _CODE_TOKEN.finditer(t):
        code = _canonical_course_code(f"{m.group(1).strip()} {m.group(2).strip()}")
        if code not in seen:
            seen.add(code)
            out.append(code)
    return out


def _strip_standing_progression_phrases(s: str) -> str:
    """Remove class-year / standing text and common connectors."""
    t = s
    for _ in range(12):
        t2 = _STANDING_OR_PROGRESSION.sub(" ", t)
        if t2 == t:
            break
        t = t2
    t = re.sub(r"(?i)\b(?:and|or)\b", " ", t)
    t = re.sub(r"[\s,;|/&+.-]+", " ", t).strip()
    return t


def _requisite_text_is_standing_or_progression_only(raw: str) -> bool:
    """
    True when a prereq/coreq cell has no course-code tokens and only
    year-standing / progression wording (not a parse failure worth warning on).
    """
    s = (raw or "").strip()
    if not s:
        return False
    after_codes = _CODE_TOKEN.sub(" ", s)
    remainder = _strip_standing_progression_phrases(after_codes)
    return len(remainder) < 2


def _normalize_units_value(
    raw: str,
    code: str,
    title: str,
    warnings: list[str],
) -> str:
    if not raw:
        return ""
    s = raw.strip()
    m = re.match(r"^(\d+(?:\.\d+)?)$", s)
    if not m:
        return ""
    v = float(m.group(1))
    joined = f"{code} {title}"
    if re.search(r"(?i)ojt|on-?the-?job|training\s*hours|internship", joined) and v >= 100:
        warnings.append(f"{code}: numeric {raw} looks like OJT hours, not credit units; units cleared.")
        return ""
    if v > 24:
        warnings.append(f"{code}: suspicious units {raw} (>24); cleared.")
        return ""
    if v == int(v):
        return str(int(v))
    return str(v)


def _pick_units_fallback(
    cells: list[str],
    col_map: dict[str, int],
    warnings: list[str],
    code: str,
    *,
    profile: str | None = None,
) -> tuple[str, bool]:
    """If no units column, prefer small numeric in columns after LEC/LAB."""
    from pos.curriculum_pdf_profiles import looks_like_grade_not_units

    lec_j = col_map.get("lec")
    lab_j = col_map.get("lab")
    grade_j = col_map.get("grade")
    tail_start = 0
    if lab_j is not None:
        tail_start = lab_j + 1
    elif lec_j is not None:
        tail_start = lec_j + 1
    skip = {
        col_map.get("prereq"),
        col_map.get("coreq"),
        col_map.get("course"),
        col_map.get("title"),
        grade_j,
    }
    for j in range(tail_start, len(cells)):
        if j in skip:
            continue
        c = cells[j].strip()
        if looks_like_grade_not_units(c, profile=profile or ""):
            continue
        if re.fullmatch(r"\d+(?:\.\d+)?", c):
            v = float(c)
            if v <= 24:
                return c, False
    return "", False


def _finalize_row(
    row: dict[str, Any],
    *,
    units_from_header_map: bool,
    prereq_from_map: bool,
    coreq_from_map: bool,
    warnings: list[str],
) -> None:
    """Add prerequisite_codes, corequisite_codes, term mirrors; adjust confidence."""
    raw_pre = row.get("raw_prerequisites") or ""
    raw_co = row.get("raw_corequisites") or ""
    pc = _extract_codes_from_text(raw_pre) if raw_pre else []
    cc = _extract_codes_from_text(raw_co) if raw_co else []
    row["prerequisite_codes"] = pc
    row["corequisite_codes"] = cc
    if raw_pre and len(raw_pre.strip()) >= 5 and not pc:
        if not _requisite_text_is_standing_or_progression_only(raw_pre):
            if len(raw_pre) > 12:
                warnings.append(f"{row.get('code', '?')}: prerequisite cell has text but no course codes parsed.")
    if raw_co and len(raw_co.strip()) >= 5 and not cc:
        if not _requisite_text_is_standing_or_progression_only(raw_co):
            if len(raw_co) > 12:
                warnings.append(f"{row.get('code', '?')}: corequisite cell has text but no course codes parsed.")

    row["term_label"] = row.get("term_label") or row.get("term") or ""
    if int(row.get("term_index") or 0) > 0:
        row["term"] = row["term_label"]
    else:
        row.setdefault("term", "")

    conf = float(row.get("confidence") or 0.5)
    if not units_from_header_map:
        conf *= 0.88
    if not prereq_from_map:
        conf *= 0.95
    if not coreq_from_map:
        conf *= 0.95
    if row.get("source", {}).get("ocr"):
        conf *= 0.82
    row["confidence"] = round(min(1.0, max(0.05, conf)), 3)


def _confidence_for_row(code: str, title: str, units: str) -> float:
    c = 0.35
    if code and len(code) <= 16:
        c += 0.2
    if title and 4 <= len(title) <= 200:
        c += 0.25
    if units and re.match(r"^\d+(\.\d+)?$", str(units).strip()):
        c += 0.2
    return min(1.0, c)


def _find_code_in_text(text: str) -> tuple[str, str] | None:
    m = _CODE_TOKEN.search(text)
    if not m:
        return None
    code = _canonical_course_code(f"{m.group(1).strip()} {m.group(2).strip()}")
    return code, m.group(0)


def _is_structural_band_heading_line(line: str) -> bool:
    """
    Single-line extract_text candidate for a positional band (term_index) bump.
    Guards: length cap, no course code, not totals, not a table column header row.
    """
    lt = line.strip()
    if not lt or len(lt) > _STRUCTURAL_HEADING_LINE_MAX_LEN:
        return False
    if re.search(r"(?i)\b(total|subtotal)\b", lt):
        return False
    if _find_code_in_text(lt):
        return False
    if len(lt) < 120 and _header_cell_category(lt) is not None:
        return False
    return _band_heading_signal(_normalize_for_band_signal(lt))


def _page_text_course_timeline(text: str, _warnings: list[str]) -> list[dict[str, Any]]:
    state = _TermState()
    out: list[dict[str, Any]] = []
    for line in (text or "").splitlines():
        lt = line.strip()
        if not lt:
            continue
        if re.search(r"(?i)\b(total|subtotal)\b", lt):
            state.clear_pending_year()
            continue
        hit = _find_code_in_text(lt)
        if hit:
            state.flush_pending_year_as_section()
            code = hit[0]
            out.append(
                {
                    "code": code,
                    "term_index": state.term_index,
                    "term_label": state.term_label,
                }
            )
            continue
        if len(lt) < 120 and _header_cell_category(lt) is not None:
            c0 = _collapse_heading_tokens(lt)
            if not (
                _RUNON_YEAR_ONLY.match(c0)
                or _RUNON_SEM_ONLY.match(c0)
                or _RUNON_MIDTERM_ONLY.match(c0)
            ):
                continue
        if _is_structural_band_heading_line(lt):
            state.clear_pending_year()
            state.bump_if_new_section(lt)
            continue
        if _consume_runon_year_sem_heading_line(lt, state):
            continue
    return out


def _timeline_has_section_context(timeline: list[dict[str, Any]]) -> bool:
    return any(int(e.get("term_index") or 0) > 0 for e in timeline)


def _align_table_rows_to_text_timeline(
    table_rows: list[dict[str, Any]],
    timeline: list[dict[str, Any]],
    warnings: list[str],
    *,
    page: int,
    backfill_source: str,
) -> None:
    """Sequence-align table rows to timeline; fills term_* only where term_index is 0 (table wins if set)."""
    ti = 0
    miss = 0
    max_miss_warn = 5
    for row in table_rows:
        code = row.get("code")
        if not code:
            continue
        while ti < len(timeline) and timeline[ti]["code"] != code:
            ti += 1
        if ti >= len(timeline):
            if int(row.get("term_index") or 0) == 0 and miss < max_miss_warn:
                if page < 0:
                    loc = "Document"
                elif page > 0:
                    loc = f"Page {page}"
                else:
                    loc = "OCR output"
                warnings.append(
                    f"{loc}: could not align table row code {code!r} to text timeline for term backfill."
                )
                miss += 1
            continue
        if int(row.get("term_index") or 0) == 0:
            row["term_index"] = int(timeline[ti]["term_index"] or 0)
            row["term_label"] = str(timeline[ti].get("term_label") or "")
            if row["term_index"] > 0:
                row["term"] = row["term_label"]
            else:
                row.setdefault("term", "")
            src = row.setdefault("source", {})
            src["term_backfill"] = backfill_source
            row["confidence"] = round(float(row.get("confidence") or 0.5) * 0.97, 3)
        ti += 1


def _parse_table_rows(
    table: list[list[Any | None]],
    term_state: _TermState,
    warnings: list[str],
    *,
    page: int,
    table_idx: int,
    doc_profile: str | None = None,
) -> tuple[list[dict[str, Any]], str]:
    from pos.curriculum_pdf_profiles import (
        DEFAULT_PROFILE,
        PROFILE_EVALUATION_5COL,
        is_footer_or_signature_row,
        is_semester_total_only_row,
        looks_like_grade_not_units,
        split_combined_requisite_cell,
    )

    rows_out: list[dict[str, Any]] = []
    if not table:
        return rows_out, doc_profile or DEFAULT_PROFILE

    col_map, table_profile = _build_col_map(table, warnings, profile=doc_profile)
    profile = table_profile
    course_j = col_map.get("course")
    title_j = col_map.get("title")
    pending_prereq_append: str | None = None

    for ri, raw_row in enumerate(table):
        if not raw_row:
            continue
        cells = [_norm_cell(c) for c in raw_row]
        joined = " | ".join(c for c in cells if c)
        if not joined.strip():
            continue

        if re.search(r"(?i)\b(total|subtotal)\b", joined):
            term_state.clear_pending_year()
            pending_prereq_append = None
            continue

        if is_footer_or_signature_row(joined):
            pending_prereq_append = None
            continue

        if is_semester_total_only_row(cells, has_course_col=course_j is not None):
            pending_prereq_append = None
            continue

        if _consume_runon_year_sem_heading_line(joined, term_state):
            continue

        if _is_section_header_joined(joined, cells):
            term_state.clear_pending_year()
            term_state.bump_if_new_section(joined)
            continue

        code = ""
        if course_j is not None:
            ccell = _get_cell(raw_row, course_j)
            if not ccell.strip():
                pre_cont = _get_cell(raw_row, col_map.get("prereq"))
                if (
                    profile == PROFILE_EVALUATION_5COL
                    and rows_out
                    and pre_cont.strip()
                    and not _get_cell(raw_row, title_j).strip()
                ):
                    prev = rows_out[-1]
                    prev_pre = str(prev.get("raw_prerequisites") or "").strip()
                    prev["raw_prerequisites"] = f"{prev_pre} {pre_cont}".strip() if prev_pre else pre_cont
                    pending_prereq_append = None
                continue
            hit = _find_code_in_text(ccell)
            if hit:
                code = hit[0]
        if not code:
            hit = _find_code_in_text(joined)
            if not hit:
                continue
            code = hit[0]

        term_state.flush_pending_year_as_section()

        title = ""
        if title_j is not None:
            title = _get_cell(raw_row, title_j)
        units_j = col_map.get("units")
        if not title:
            for j, c in enumerate(cells):
                if not c or code in c:
                    continue
                if units_j is not None and j == units_j:
                    continue
                cand = c.replace(code, "").strip(" -–|:\t")
                if len(cand) > len(title):
                    title = cand

        allow_title_merge = profile == PROFILE_EVALUATION_5COL or (
            col_map.get("lec") is not None or col_map.get("lab") is not None
        )
        if (
            not (title or "").strip()
            and title_j is not None
            and course_j is not None
            and ri > 0
            and allow_title_merge
        ):
            prev = table[ri - 1]
            if not _get_cell(prev, course_j).strip():
                prev_t = _get_cell(prev, title_j).strip()
                if prev_t and not _find_code_in_text(prev_t):
                    if not re.search(r"(?i)\b(total|subtotal)\b", prev_t):
                        title = prev_t
            if ri + 1 < len(table):
                nxt = table[ri + 1]
                if not _get_cell(nxt, course_j).strip():
                    nxt_t = _get_cell(nxt, title_j).strip()
                    if nxt_t and not _find_code_in_text(nxt_t):
                        if not re.search(r"(?i)\b(total|subtotal)\b", nxt_t):
                            title = f"{title} {nxt_t}".strip() if title else nxt_t

        lec_raw = _get_cell(raw_row, col_map.get("lec"))
        lab_raw = _get_cell(raw_row, col_map.get("lab"))

        units_raw = _get_cell(raw_row, col_map.get("units"))
        if looks_like_grade_not_units(units_raw, profile=profile):
            units_raw = ""
        units_from_header = bool(units_raw and col_map.get("units") is not None)
        units = _normalize_units_value(units_raw, code, title, warnings)
        if not units and col_map.get("units") is None:
            fb, _fb_ok = _pick_units_fallback(cells, col_map, warnings, code, profile=profile)
            if looks_like_grade_not_units(fb, profile=profile):
                fb = ""
            units = _normalize_units_value(fb, code, title, warnings)

        prereq_j = col_map.get("prereq")
        coreq_j = col_map.get("coreq")
        raw_pre = _get_cell(raw_row, prereq_j)
        raw_co = _get_cell(raw_row, coreq_j)
        if profile == PROFILE_EVALUATION_5COL and raw_pre and coreq_j is None:
            raw_pre, raw_co_split = split_combined_requisite_cell(raw_pre)
            if raw_co_split and not raw_co:
                raw_co = raw_co_split
        prereq_from_map = prereq_j is not None and bool(raw_pre)
        coreq_from_map = coreq_j is not None and bool(raw_co)

        raw_max = ""
        mj = col_map.get("max")
        if mj is not None:
            raw_max = _get_cell(raw_row, mj)

        conf = _confidence_for_row(code, title, units)
        row: dict[str, Any] = {
            "code": code,
            "title": title[:240] if title else "",
            "units": units,
            "lec": lec_raw,
            "lab": lab_raw,
            "term": term_state.term_label,
            "term_index": term_state.term_index,
            "term_label": term_state.term_label,
            "raw_prerequisites": raw_pre,
            "raw_corequisites": raw_co,
            "raw_max_units_text": raw_max,
            "confidence": round(conf, 3),
            "source": {
                "kind": "pdf_table",
                "row_index": ri,
                "page": page,
                "table": table_idx,
                "template_profile": profile,
            },
        }
        _finalize_row(
            row,
            units_from_header_map=units_from_header and bool(units),
            prereq_from_map=prereq_from_map,
            coreq_from_map=coreq_from_map,
            warnings=warnings,
        )
        rows_out.append(row)
        pending_prereq_append = None
    return rows_out, profile


def _merge_unique_rows_by_code(primary: list[dict[str, Any]], secondary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep primary rows; add secondary rows only for codes not already present (prefer pdfplumber over OCR)."""
    seen = {r["code"] for r in primary if r.get("code")}
    out = list(primary)
    for r in secondary:
        c = r.get("code")
        if not c or c in seen:
            continue
        seen.add(c)
        out.append(r)
    return out


def _should_run_ocr(extracted_count: int, total_text_chars: int, page_count: int) -> bool:
    if page_count <= 0:
        return False
    if extracted_count == 0:
        return True
    if total_text_chars < 500:
        return True
    if extracted_count < 5 and total_text_chars < 2500:
        return True
    return False


def _ocr_pdf_to_text_pymupdf_tesseract(pdf_path: Path, warnings: list[str]) -> str:
    """Rasterize PDF pages with PyMuPDF and run Tesseract OCR (for scanned / image-based pages)."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        warnings.append("PyMuPDF (pymupdf) is not installed; OCR skipped.")
        return ""

    try:
        import io

        import pytesseract
        from PIL import Image
    except ImportError as e:
        warnings.append(f"OCR dependency missing (pytesseract/Pillow): {e}")
        return ""

    parts: list[str] = []
    max_pages = 40
    try:
        doc = fitz.open(str(pdf_path))
        n = doc.page_count
        if n > max_pages:
            warnings.append(f"OCR is limited to the first {max_pages} pages (PDF has {n} pages).")
        for i in range(min(n, max_pages)):
            page = doc[i]
            mat = fitz.Matrix(2.0, 2.0)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            try:
                text = pytesseract.image_to_string(img) or ""
            except pytesseract.TesseractNotFoundError:
                warnings.append(
                    "Tesseract is not installed or not on PATH. "
                    "Install from https://github.com/tesseract-ocr/tesseract (Windows: add install dir to PATH)."
                )
                doc.close()
                return ""
            if text.strip():
                parts.append(text)
        doc.close()
    except Exception as e:
        warnings.append(f"OCR (PyMuPDF + Tesseract) failed: {e}")
    return "\n".join(parts)


def _parse_text_page(
    text: str,
    term_state: _TermState,
    warnings: list[str],
    *,
    ocr: bool = False,
) -> list[dict[str, Any]]:
    rows_out: list[dict[str, Any]] = []
    for line in (text or "").splitlines():
        lt = line.strip()
        if not lt:
            continue
        if re.search(r"(?i)\b(total|subtotal)\b", lt):
            term_state.clear_pending_year()
            continue
        hit = _find_code_in_text(lt)
        if hit:
            term_state.flush_pending_year_as_section()
            code, _ = hit
            remainder = lt
            for part in code.split():
                remainder = remainder.replace(part, "", 1)
            remainder = re.sub(r"^[\s|\-–:]+", "", remainder).strip()
            title = remainder
            um = _UNITS_HINT.search(lt)
            units_raw = um.group(1) if um else ""
            units = _normalize_units_value(units_raw, code, title, warnings)
            if not units:
                mnum = re.search(r"\b(\d+(?:\.\d+)?)\s*$", lt)
                if mnum:
                    units = _normalize_units_value(mnum.group(1), code, title, warnings)
            mm = _MAX_UNITS_HINT.search(lt)
            raw_max = mm.group(0) if mm else ""
            row = {
                "code": code,
                "title": title[:240],
                "units": units,
                "lec": "",
                "lab": "",
                "term": term_state.term_label,
                "term_index": term_state.term_index,
                "term_label": term_state.term_label,
                "raw_prerequisites": "",
                "raw_corequisites": "",
                "raw_max_units_text": raw_max,
                "confidence": round(_confidence_for_row(code, title, units) * (0.75 if ocr else 0.85), 3),
                "source": {"kind": "ocr_tesseract" if ocr else "pdf_text_line"},
            }
            _finalize_row(
                row,
                units_from_header_map=bool(units_raw),
                prereq_from_map=False,
                coreq_from_map=False,
                warnings=warnings,
            )
            if ocr:
                row["source"]["ocr"] = True
            rows_out.append(row)
            continue
        if len(lt) < 120 and _header_cell_category(lt) is not None:
            c0 = _collapse_heading_tokens(lt)
            if not (
                _RUNON_YEAR_ONLY.match(c0)
                or _RUNON_SEM_ONLY.match(c0)
                or _RUNON_MIDTERM_ONLY.match(c0)
            ):
                continue
        if _is_structural_band_heading_line(lt):
            term_state.clear_pending_year()
            term_state.bump_if_new_section(lt)
            continue
        if _consume_runon_year_sem_heading_line(lt, term_state):
            continue
    return rows_out


def _page_extract_text_for_timeline(page: Any) -> str:
    """Prefer layout-preserving extract_text when pdfplumber supports it."""
    try:
        t = page.extract_text(layout=True)
    except Exception:
        t = None
    if isinstance(t, str) and t.strip():
        return t
    return page.extract_text() or ""


def _apply_term_label_normalization(
    rows: list[dict[str, Any]],
    profile: str,
) -> int:
    """Rewrite term_label/term to canonical grid strings where possible; return count changed."""
    from pos.curriculum_pdf_profiles import normalize_term_label

    changed = 0
    for row in rows:
        raw = str(row.get("term_label") or row.get("term") or "").strip()
        if not raw:
            continue
        norm = normalize_term_label(raw, profile)
        if norm and norm != raw:
            row["term_label"] = norm
            row["term"] = norm
            changed += 1
    return changed


def extract_curriculum_rows_from_pdf(
    pdf_path: Path,
    *,
    template_hint: str | None = None,
) -> dict[str, Any]:
    """
    Return a draft_json-shaped extraction dict (not yet merged with DB wrapper).

    Skips pdfplumber tables that look like CMO “Existing / Proposed / Remarks” comparison
    matrices and truncates document text at Comparison-of-CMO headings so timelines and
    backfill stay on Program-of-Study content.

    Keys:
      - phase: 5
      - extracted_rows: list of normalized row dicts
      - warnings: list[str]
      - extraction_meta: dict (pages, library, path hint)
      - suggested_graph: null (Phase 6)
    """
    from pos.curriculum_pdf_profiles import (
        DEFAULT_PROFILE,
        KNOWN_PROFILES,
        PROFILE_EVALUATION_5COL,
        merge_document_profile,
        should_skip_ocr_for_profile,
    )

    warnings: list[str] = []
    extracted: list[dict[str, Any]] = []
    meta: dict[str, Any] = {"library": "pdfplumber", "path": str(pdf_path.name)}
    hint_profile: str | None = None
    if template_hint and template_hint in KNOWN_PROFILES:
        hint_profile = template_hint
    elif template_hint == "evaluation":
        hint_profile = PROFILE_EVALUATION_5COL
    elif template_hint == "catalog":
        hint_profile = DEFAULT_PROFILE
    table_profiles: list[str] = []

    try:
        import pdfplumber
    except ImportError:
        warnings.append("pdfplumber is not installed; cannot extract PDF text.")
        return _wrap_draft(extracted, warnings, meta, pages=0)

    if not pdf_path.is_file():
        warnings.append(f"PDF path is not a file: {pdf_path}")
        return _wrap_draft(extracted, warnings, meta, pages=0)

    term_state = _TermState()
    total_text_chars = 0
    doc_text_parts: list[str] = []
    skipped_cmp_tables = 0
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            meta["page_count"] = len(pdf.pages)
            for pi, page in enumerate(pdf.pages):
                text = _page_extract_text_for_timeline(page)
                doc_text_parts.append(text)
                total_text_chars += len(text)
                tables = page.extract_tables() or []
                page_rows = 0
                if tables:
                    for ti, table in enumerate(tables):
                        if not table:
                            continue
                        if _table_looks_like_cmo_comparison_matrix(table):
                            skipped_cmp_tables += 1
                            continue
                        part, tbl_profile = _parse_table_rows(
                            table,
                            term_state,
                            warnings,
                            page=pi + 1,
                            table_idx=ti,
                            doc_profile=hint_profile,
                        )
                        table_profiles.append(tbl_profile)
                        extracted.extend(part)
                        page_rows += len(part)
                if page_rows == 0:
                    part = _parse_text_page(text, term_state, warnings, ocr=False)
                    for r in part:
                        r.setdefault("source", {})["page"] = pi + 1
                    extracted.extend(part)

            combined_doc_text = "\n".join(doc_text_parts)
            combined_for_timeline, pos_trunc = _truncate_combined_text_at_pos_section_end(combined_doc_text)
            if pos_trunc:
                meta["pos_section_text_truncated"] = True
                warnings.append(
                    "Stopped text timeline at a Comparison-of-CMO section so semester alignment uses Program-of-Study content only."
                )
            if skipped_cmp_tables:
                meta["skipped_cmo_comparison_tables"] = skipped_cmp_tables
                warnings.append(
                    f"Skipped {skipped_cmp_tables} CMO comparison matrix table(s); extracted Program-of-Study course grids only."
                )
            global_timeline = _page_text_course_timeline(combined_for_timeline, warnings)
            table_rows_ordered = [
                r for r in extracted if (r.get("source") or {}).get("kind") == "pdf_table"
            ]
            if (
                table_rows_ordered
                and _timeline_has_section_context(global_timeline)
                and any(int(r.get("term_index") or 0) == 0 for r in table_rows_ordered)
            ):
                _align_table_rows_to_text_timeline(
                    table_rows_ordered,
                    global_timeline,
                    warnings,
                    page=-1,
                    backfill_source="document_text_timeline",
                )

            doc_profile = hint_profile or merge_document_profile(table_profiles)
            meta["template_profile"] = doc_profile
            if doc_profile == PROFILE_EVALUATION_5COL:
                meta["grade_column_ignored"] = True
            norm_n = _apply_term_label_normalization(extracted, doc_profile)
            if norm_n:
                meta["term_labels_normalized"] = norm_n

        doc_profile = meta.get("template_profile") or hint_profile or DEFAULT_PROFILE
        skip_ocr = should_skip_ocr_for_profile(
            str(doc_profile),
            len(extracted),
            total_text_chars,
        )
        if skip_ocr:
            meta["ocr_skipped"] = "evaluation_native_text"
        if _should_run_ocr(len(extracted), total_text_chars, int(meta.get("page_count") or 0)) and not skip_ocr:
            ocr_text = _ocr_pdf_to_text_pymupdf_tesseract(pdf_path, warnings)
            if ocr_text.strip():
                ocr_text, _ocr_pos_trunc = _truncate_combined_text_at_pos_section_end(ocr_text)
                ocr_rows = _parse_text_page(ocr_text, term_state, warnings, ocr=True)
                for r in ocr_rows:
                    r.setdefault("source", {})["ocr"] = True
                tl_ocr = _page_text_course_timeline(ocr_text, warnings)
                if ocr_rows and _timeline_has_section_context(tl_ocr):
                    if any(int(r.get("term_index") or 0) == 0 for r in ocr_rows):
                        _align_table_rows_to_text_timeline(
                            ocr_rows,
                            tl_ocr,
                            warnings,
                            page=0,
                            backfill_source="ocr_text_timeline",
                        )
                before = len(extracted)
                extracted = _merge_unique_rows_by_code(extracted, ocr_rows)
                if len(extracted) > before or (before == 0 and len(ocr_rows) > 0):
                    meta["library"] = "pdfplumber+tesseract"
                    meta["ocr_used"] = True
                    meta["ocr_engine"] = "tesseract+pymupdf"
            elif not any("Tesseract" in w for w in warnings):
                warnings.append("OCR ran but produced no usable text (blank pages or unreadable scan).")

        if not extracted:
            warnings.append(
                "No course-like rows were detected after text + OCR passes. "
                "Tables may be unreadable, or course codes do not match expected patterns."
            )
    except Exception as e:
        warnings.append(f"pdfplumber extraction failed: {e}")

    return _wrap_draft(extracted, warnings, meta, pages=meta.get("page_count", 0))


def _wrap_draft(
    extracted: list[dict[str, Any]],
    warnings: list[str],
    meta: dict[str, Any],
    *,
    pages: int,
) -> dict[str, Any]:
    meta = dict(meta)
    meta.setdefault("page_count", pages)
    return {
        "phase": 5,
        "extracted_rows": extracted,
        "warnings": warnings,
        "extraction_meta": meta,
        "suggested_graph": None,
    }
