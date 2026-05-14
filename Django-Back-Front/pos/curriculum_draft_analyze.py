"""
Phase 6 — conservative draft analyzer (prereq/coreq hints, standing tokens, unresolved text).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from pos.curriculum_draft_semesters import orig_sem_suggested_for_row, term_index_to_orig_sem_map
from pos.curriculum_pdf_extract import (
    _STANDING_OR_PROGRESSION,
    _canonical_course_code,
    _extract_codes_from_text,
    _requisite_text_is_standing_or_progression_only,
)

ANALYZER_VERSION = "phase6-v1"


def _standing_year_guess(match_text: str) -> int | None:
    s = match_text.lower()
    if "sixth" in s or "6th" in s or " sixth" in s:
        return 6
    if "fifth" in s or "5th" in s:
        return 5
    if "fourth" in s or "4th" in s or "senior" in s or "graduating" in s:
        return 4
    if "third" in s or "3rd" in s or "junior" in s:
        return 3
    if "second" in s or "2nd" in s:
        return 2
    if "first" in s or "1st" in s:
        return 1
    if "advanced" in s:
        return 3
    if "regular" in s:
        return 2
    return None


def _collect_standing_phrases(raw: str) -> list[str]:
    out: list[str] = []
    for m in _STANDING_OR_PROGRESSION.finditer(raw or ""):
        phrase = " ".join((m.group(0) or "").split())
        if phrase and phrase not in out:
            out.append(phrase)
    return out


def _split_req_fragments(raw: str) -> list[str]:
    if not (raw or "").strip():
        return []
    parts = re.split(r"[;,/\n]+", raw)
    return [" ".join(p.split()) for p in parts if p and p.strip()]


def _analyze_row(
    row: dict[str, Any],
    *,
    all_codes: set[str],
    ti_map: dict[int, int],
) -> dict[str, Any]:
    code = str(row.get("code") or "").strip()
    raw_pre = str(row.get("raw_prerequisites") or "")
    raw_co = str(row.get("raw_corequisites") or "")

    pc = [_canonical_course_code(c) for c in (row.get("prerequisite_codes") or [])]
    cc = [_canonical_course_code(c) for c in (row.get("corequisite_codes") or [])]

    resolved_pre: list[str] = []
    for c in pc:
        if c in all_codes:
            resolved_pre.append(c)
        else:
            resolved_pre.append(c)

    standing_pre = _collect_standing_phrases(raw_pre)
    for st in standing_pre:
        if st not in resolved_pre:
            resolved_pre.append(st)

    resolved_co = [c for c in cc if c in all_codes]

    unresolved: list[str] = []
    known = set(resolved_pre) | set(resolved_co)
    for frag in _split_req_fragments(raw_pre):
        if not frag or frag in known:
            continue
        if _extract_codes_from_text(frag):
            continue
        if _STANDING_OR_PROGRESSION.search(frag):
            continue
        if _requisite_text_is_standing_or_progression_only(frag):
            continue
        if len(frag) >= 2:
            unresolved.append(frag)
    for frag in _split_req_fragments(raw_co):
        if not frag or frag in known:
            continue
        if _extract_codes_from_text(frag):
            continue
        if len(frag) >= 2 and frag not in unresolved:
            unresolved.append(frag)

    conf = float(row.get("confidence") or 0.5)
    if unresolved:
        conf *= 0.85

    return {
        "orig_sem_suggested": orig_sem_suggested_for_row(row, ti_map),
        "resolved_pre": resolved_pre,
        "resolved_co": resolved_co,
        "unresolved": unresolved,
        "confidence": round(min(1.0, max(0.05, conf)), 3),
        "course_code": code,
    }


def _bundle_suggestions(rows_analysis: dict[str, dict[str, Any]]) -> tuple[dict[str, int], dict[str, str]]:
    year_rules: dict[str, int] = {}
    for rid, block in rows_analysis.items():
        for tok in block.get("resolved_pre") or []:
            if tok in year_rules:
                continue
            if _STANDING_OR_PROGRESSION.search(tok):
                y = _standing_year_guess(tok)
                if y is not None:
                    year_rules[tok] = y
    return year_rules, {}


def analyze_draft_json(draft_json: dict[str, Any]) -> dict[str, Any]:
    """Return draft_analysis dict (does not mutate draft_json)."""
    rows = list(draft_json.get("extracted_rows") or [])
    all_codes = {str(r.get("code") or "").strip() for r in rows if r.get("code")}
    all_codes = {_canonical_course_code(c) for c in all_codes if c}
    ti_map = term_index_to_orig_sem_map(rows)

    per_row: dict[str, Any] = {}
    for row in rows:
        rid = str(row.get("draft_row_id") or "").strip()
        if not rid:
            continue
        per_row[rid] = _analyze_row(row, all_codes=all_codes, ti_map=ti_map)

    ystand, _aliases = _bundle_suggestions(per_row)

    return {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "analyzer_version": ANALYZER_VERSION,
        "rows": per_row,
        "year_standing_rules_suggested": ystand,
        "prereq_aliases_suggested": {},
    }
