"""
Phase 6 — build Phase 0 approved_json from staff draft rows + analyzer output.
"""

from __future__ import annotations

from typing import Any

from pos.curriculum_draft_semesters import orig_sem_for_course_row, term_index_to_orig_sem_map
from pos.curriculum_pdf_extract import _canonical_course_code

# v1 policy: uniform semester caps until UI allows editing.
MAX_UNITS_PER_SEMESTER_V1 = 24


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

        pre_tokens: list[str] = []
        if row_a:
            for tok in row_a.get("resolved_pre") or []:
                if isinstance(tok, str) and tok.strip():
                    pre_tokens.append(tok.strip())
        if not pre_tokens:
            for c in row.get("prerequisite_codes") or []:
                cc = _canonical_course_code(str(c))
                if cc:
                    pre_tokens.append(cc)

        pre_req_out: list[str | list[str]] = []
        seen_pre: set[str] = set()
        for tok in pre_tokens:
            ct = _canonical_course_code(tok)
            if ct in known_codes:
                key = ct
            else:
                key = tok.strip()
            if key not in seen_pre:
                seen_pre.add(key)
                pre_req_out.append(ct if ct in known_codes else tok.strip())

        co_out: list[str] = []
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
            "category": "Rigid",
            "year_level": _int_year_level(row),
            "pre-requisites": pre_req_out,
            "co-requisites": co_out,
        }

    max_orig = max((c["orig_sem"] for c in courses.values()), default=1)
    max_units = [MAX_UNITS_PER_SEMESTER_V1] * int(max_orig)

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
