"""
Phase 6 — build Phase 0 approved_json from staff draft rows + analyzer output.
"""

from __future__ import annotations

from typing import Any

from pos.curriculum_draft_analyze import _split_req_fragments
from pos.curriculum_draft_semesters import orig_sem_for_course_row, term_index_to_orig_sem_map
from pos.curriculum_pdf_extract import (
    _canonical_course_code,
    _extract_codes_from_text,
)
from pos.curriculum_unit_caps import (
    OVERFLOW_SEMESTERS,
    build_max_units_by_semester_index,
    validate_max_units_by_year_term,
)
from pos.solver_course_reference import resolve_course_category


def _float_units(row: dict[str, Any]) -> float:
    u = row.get("units")
    if u is None or u == "":
        return 0.0
    try:
        return max(0.0, float(u))
    except (TypeError, ValueError):
        return 0.0


def _int_year_level(row: dict[str, Any]) -> int:
    if "year_level" in row and row["year_level"] is not None and str(row["year_level"]).strip() != "":
        try:
            return max(1, int(row["year_level"]))
        except (TypeError, ValueError):
            pass
    return 1


def _year_level_for_approved(row: dict[str, Any], row_a: dict[str, Any] | None) -> int:
    """Staff row override, else analyzer year_level_suggested, else legacy default."""
    if "year_level" in row and row["year_level"] is not None and str(row["year_level"]).strip() != "":
        try:
            return max(1, int(row["year_level"]))
        except (TypeError, ValueError):
            pass
    if row_a and row_a.get("year_level_suggested") is not None:
        try:
            v = int(row_a["year_level_suggested"])
            if v >= 1:
                return min(10, v)
        except (TypeError, ValueError):
            pass
    return _int_year_level(row)


def draft_to_approved_json(
    draft_json: dict[str, Any],
    *,
    program_slug: str,
    curriculum_version_label: str,
    source_pdf_sha256: str | None,
) -> dict[str, Any]:
    rows = list(draft_json.get("extracted_rows") or [])
    analysis = draft_json.get("draft_analysis") or {}
    per_analysis: dict[str, Any] = {}
    if isinstance(analysis, dict):
        raw_rows = analysis.get("rows")
        if isinstance(raw_rows, dict):
            per_analysis = raw_rows

    known_codes = {
        _canonical_course_code(str(r.get("code") or "").strip())
        for r in rows
        if (r.get("code") or "").strip()
    }
    known_codes.discard("")

    ti_map = term_index_to_orig_sem_map(rows)
    courses: dict[str, Any] = {}

    for row in rows:
        code = _canonical_course_code(str(row.get("code") or "").strip())
        if not code:
            continue
        if code in courses:
            continue
        rid = str(row.get("draft_row_id") or "").strip()
        row_a = per_analysis.get(rid) if rid else None
        if not isinstance(row_a, dict):
            row_a = None
        orig_sem = orig_sem_for_course_row(row, ti_map, draft_analysis_row=row_a)
        title = str(row.get("title") or row.get("course_title") or "").strip() or code

        # raw_prerequisites is authoritative when non-empty; fall back to
        # prerequisite_codes (PDF extraction) only when the raw field is blank.
        raw_pre_text = str(row.get("raw_prerequisites") or "").strip()
        pre_tokens: list[str] = []
        if raw_pre_text:
            for frag in _split_req_fragments(raw_pre_text):
                for extracted in _extract_codes_from_text(frag):
                    cc = _canonical_course_code(extracted)
                    if cc and cc not in pre_tokens:
                        pre_tokens.append(cc)
            # Also keep standing phrases (e.g. "4th Year Standing") from analyzer
            if row_a:
                for tok in row_a.get("resolved_pre") or []:
                    if (
                        isinstance(tok, str)
                        and tok.strip()
                        and not _canonical_course_code(tok)
                        and tok not in pre_tokens
                    ):
                        pre_tokens.append(tok.strip())
        else:
            # No raw text — use analyzer output then PDF codes
            if row_a:
                for tok in row_a.get("resolved_pre") or []:
                    if isinstance(tok, str) and tok.strip():
                        pre_tokens.append(tok.strip())
            for c in row.get("prerequisite_codes") or []:
                cc = _canonical_course_code(str(c))
                if cc and cc not in pre_tokens:
                    pre_tokens.append(cc)

        pre_req_out: list[str | list[str]] = []
        seen_pre: set[str] = set()
        for tok in pre_tokens:
            ct = _canonical_course_code(tok)
            key = ct if ct in known_codes else tok.strip()
            if key not in seen_pre:
                seen_pre.add(key)
                pre_req_out.append(ct if ct in known_codes else tok.strip())

        # raw_corequisites is authoritative when non-empty; fall back to
        # corequisite_codes (PDF extraction) only when the raw field is blank.
        raw_co_text = str(row.get("raw_corequisites") or "").strip()
        co_out: list[str] = []
        if raw_co_text:
            for frag in _split_req_fragments(raw_co_text):
                for extracted in _extract_codes_from_text(frag):
                    cc = _canonical_course_code(extracted)
                    if cc in known_codes and cc not in co_out:
                        co_out.append(cc)
        else:
            if row_a:
                for c in row_a.get("resolved_co") or []:
                    cc = _canonical_course_code(str(c))
                    if cc in known_codes and cc not in co_out:
                        co_out.append(cc)
            for c in row.get("corequisite_codes") or []:
                cc = _canonical_course_code(str(c))
                if cc in known_codes and cc not in co_out:
                    co_out.append(cc)

        courses[code] = {
            "course_title": title[:500],
            "units": _float_units(row),
            "orig_sem": int(orig_sem),
            "category": resolve_course_category(code, row.get("category")),
            "year_level": _year_level_for_approved(row, row_a),
            "pre-requisites": pre_req_out,
            "co-requisites": co_out,
        }

    max_orig = max((c["orig_sem"] for c in courses.values()), default=1)
    raw_caps = draft_json.get("max_units_by_year_term")
    year_term_map = validate_max_units_by_year_term(raw_caps)
    max_units = build_max_units_by_semester_index(
        year_term_map, max_orig=max_orig, overflow_semesters=OVERFLOW_SEMESTERS,
    )

    meta_sha: str | None
    if source_pdf_sha256 and len(str(source_pdf_sha256).strip()) == 64:
        meta_sha = str(source_pdf_sha256).strip().lower()
    else:
        meta_sha = None

    ystand: dict[str, int] = {}
    if isinstance(analysis, dict):
        sug = analysis.get("year_standing_rules_suggested") or {}
        if isinstance(sug, dict):
            for k, v in sug.items():
                if isinstance(k, str) and k.strip():
                    try:
                        ystand[k.strip()] = max(1, int(v))
                    except (TypeError, ValueError):
                        pass

    course_keys = set(courses.keys())
    for crec in courses.values():
        for tok in crec.get("pre-requisites") or []:
            if isinstance(tok, str) and tok not in course_keys:
                ystand.setdefault(tok, 2)

    out: dict[str, Any] = {
        "schema_version": 1,
        "courses": courses,
        "max_units_by_semester_index": max_units,
        "metadata": {
            "program_slug": program_slug,
            "curriculum_version_label": (curriculum_version_label or "untitled").strip() or "untitled",
            "source_pdf_sha256": meta_sha,
        },
    }
    if ystand:
        out["year_standing_rules"] = ystand
    return out
