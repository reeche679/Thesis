"""
Phase 5 — machine-assisted draft extraction from curriculum PDFs.

Uses pdfplumber (already in project requirements). Output is intentionally lossy;
callers persist warnings + per-row confidence in draft_json.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# Course codes like "MATH 101", "CpE 203", "GEd 108", "ENGG 101"
_CODE_TOKEN = re.compile(
    r"\b([A-Za-z]{2,6})\s*(\d{3}[A-Za-z]?)\b",
)
# Lines that look like a semester / year band header (not course rows)
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


def _norm_cell(x: Any) -> str:
    if x is None:
        return ""
    return re.sub(r"\s+", " ", str(x)).strip()


def _looks_like_term(text: str) -> bool:
    t = text.strip()
    if len(t) < 8 or len(t) > 120:
        return False
    return bool(_TERM_LINE.search(t))


def _find_code_in_text(text: str) -> tuple[str, str] | None:
    m = _CODE_TOKEN.search(text)
    if not m:
        return None
    code = f"{m.group(1).strip()} {m.group(2).strip()}"
    return code, m.group(0)


def _confidence_for_row(code: str, title: str, units: str) -> float:
    c = 0.35
    if code and len(code) <= 16:
        c += 0.2
    if title and 4 <= len(title) <= 200:
        c += 0.25
    if units and re.match(r"^\d+(\.\d+)?$", units.strip()):
        c += 0.2
    return min(1.0, c)


def _parse_table_rows(
    table: list[list[Any | None]],
    current_term: str,
    warnings: list[str],
) -> tuple[list[dict[str, Any]], str]:
    rows_out: list[dict[str, Any]] = []
    term = current_term
    for ri, raw_row in enumerate(table):
        if not raw_row:
            continue
        cells = [_norm_cell(c) for c in raw_row]
        joined = " | ".join(c for c in cells if c)
        if not joined.strip():
            continue
        if _looks_like_term(joined):
            term = joined
            continue
        code_hit = _find_code_in_text(joined)
        if not code_hit:
            continue
        code, _match_span = code_hit
        title = ""
        for c in cells:
            if not c:
                continue
            cand = c.replace(code, "").strip(" -–|:\t")
            if len(cand) > len(title):
                title = cand
        units = ""
        for c in cells:
            if re.fullmatch(r"\d+(\.\d+)?", c.strip() or ""):
                units = c.strip()
                break
        raw_pre = ""
        raw_co = ""
        raw_max = ""
        for c in cells:
            cl = c.lower()
            if "pre" in cl or "req" in cl:
                raw_pre = c
            if "co" in cl and "req" in cl:
                raw_co = c
            if "max" in cl or "limit" in cl:
                raw_max = c
        conf = _confidence_for_row(code, title, units)
        rows_out.append(
            {
                "code": code,
                "title": title,
                "units": units,
                "term": term,
                "raw_prerequisites": raw_pre,
                "raw_corequisites": raw_co,
                "raw_max_units_text": raw_max,
                "confidence": round(conf, 3),
                "source": {"kind": "pdf_table", "row_index": ri},
            }
        )
    return rows_out, term


def _parse_text_page(text: str, current_term: str, warnings: list[str]) -> tuple[list[dict[str, Any]], str]:
    rows_out: list[dict[str, Any]] = []
    term = current_term
    for line in (text or "").splitlines():
        lt = line.strip()
        if not lt:
            continue
        if _looks_like_term(lt):
            term = lt
            continue
        hit = _find_code_in_text(lt)
        if not hit:
            continue
        code, _ = hit
        remainder = lt
        for part in code.split():
            remainder = remainder.replace(part, "", 1)
        remainder = re.sub(r"^[\s|\-–:]+", "", remainder).strip()
        title = remainder
        um = _UNITS_HINT.search(lt)
        units = um.group(1) if um else ""
        mm = _MAX_UNITS_HINT.search(lt)
        raw_max = mm.group(0) if mm else ""
        rows_out.append(
            {
                "code": code,
                "title": title[:240],
                "units": units,
                "term": term,
                "raw_prerequisites": "",
                "raw_corequisites": "",
                "raw_max_units_text": raw_max,
                "confidence": round(_confidence_for_row(code, title, units) * 0.85, 3),
                "source": {"kind": "pdf_text_line"},
            }
        )
    return rows_out, term


def extract_curriculum_rows_from_pdf(pdf_path: Path) -> dict[str, Any]:
    """
    Return a draft_json-shaped extraction dict (not yet merged with DB wrapper).

    Keys:
      - phase: 5
      - extracted_rows: list of normalized row dicts
      - warnings: list[str]
      - extraction_meta: dict (pages, library, path hint)
      - suggested_graph: null (Phase 6)
    """
    warnings: list[str] = []
    extracted: list[dict[str, Any]] = []
    meta: dict[str, Any] = {"library": "pdfplumber", "path": str(pdf_path.name)}

    try:
        import pdfplumber
    except ImportError as e:
        warnings.append("pdfplumber is not installed; cannot extract PDF text.")
        return _wrap_draft(extracted, warnings, meta, pages=0)

    if not pdf_path.is_file():
        warnings.append(f"PDF path is not a file: {pdf_path}")
        return _wrap_draft(extracted, warnings, meta, pages=0)

    current_term = ""
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            meta["page_count"] = len(pdf.pages)
            for pi, page in enumerate(pdf.pages):
                page_rows = 0
                tables = page.extract_tables() or []
                if tables:
                    for ti, table in enumerate(tables):
                        if not table:
                            continue
                        part, current_term = _parse_table_rows(table, current_term, warnings)
                        for r in part:
                            r["source"]["page"] = pi + 1
                            r["source"]["table"] = ti
                        extracted.extend(part)
                        page_rows += len(part)
                if page_rows == 0:
                    text = page.extract_text() or ""
                    part, current_term = _parse_text_page(text, current_term, warnings)
                    for r in part:
                        r["source"]["page"] = pi + 1
                    extracted.extend(part)
        if not extracted:
            warnings.append(
                "No course-like rows were detected. Tables may be images, or the layout does not match heuristics."
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
