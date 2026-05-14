"""
Resolve curriculum sections for Generate POS: DB approved_json vs legacy BSCpE static POS_CURRICULUM.

v1 mapping: group courses by ``orig_sem`` (1-based global semester). Section index = ``orig_sem - 1``.
Labels use ``curriculum_section_labels.INDEX_TO_YEAR_SEM`` when in range; otherwise synthetic
year/semester strings. If any course lacks a valid ``orig_sem``, fall back to static curriculum.
"""

from __future__ import annotations

from typing import Any, TypedDict

from pos.curriculum_section_labels import INDEX_TO_YEAR_SEM
from pos.models import CurriculumVersion, Program


class GenerateCurriculumContext(TypedDict, total=False):
    mode: str  # "db" | "static"
    curriculum_version_id: int | None
    curriculum_sections: list[dict[str, Any]]
    course_lookup: dict[str, tuple[Any, ...]]
    display_title: str
    subtitle: str
    curriculum_truncated: bool


def _curriculum_version_id_for_generate(student) -> int | None:
    """Match pos.views._curriculum_version_id_for_student (duplicated to avoid circular imports)."""
    if student is None:
        return None
    sel = getattr(student, "selected_curriculum_version_id", None)
    if sel:
        return int(sel)
    pid = getattr(student, "program_id", None)
    if not pid:
        return None
    dcv = Program.objects.filter(pk=pid).values_list("default_curriculum_version_id", flat=True).first()
    return int(dcv) if dcv else None


def _static_sections_and_lookup() -> tuple[list[dict[str, Any]], dict[str, tuple[Any, ...]]]:
    from pos.pos_pdf import POS_CURRICULUM, display_pos_course_code

    sections: list[dict[str, Any]] = []
    lookup: dict[str, tuple[Any, ...]] = {}
    for title1, title2, _tu, _lec, _lab, rows in POS_CURRICULUM:
        section_label = "{} - {}".format(
            title1.replace(" ", " ").title(),
            title2.replace(" ", " ").title(),
        )
        courses = [
            {
                "code": r[0],
                "display_code": display_pos_course_code(r[0]),
                "title": r[1],
                "units": r[2],
                "lec": r[3],
                "lab": r[4],
                "prereq": r[5],
                "coreq": r[6],
            }
            for r in rows
        ]
        sections.append({"year": title1, "semester": title2, "label": section_label, "courses": courses})
        for r in rows:
            code = r[0]
            if code not in lookup:
                lookup[code] = (r[1], r[2], r[3], r[4], r[5] or "", r[6] or "")
    return sections, lookup


def _ordinal_year(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf} Year"


def _synthetic_year_sem(section_index: int) -> tuple[str, str, str]:
    """Beyond INDEX_TO_YEAR_SEM: assume 3 bands per year (First, Second, Midterm)."""
    rel = section_index - len(INDEX_TO_YEAR_SEM)
    year_num = rel // 3 + 11
    band = rel % 3
    sem_names = ("First Semester", "Second Semester", "Midterm")
    yl = _ordinal_year(year_num)
    sem = sem_names[band]
    label = f"{yl} - {sem}"
    return yl, sem, label


def _section_label_for_index(i: int) -> tuple[str, str, str]:
    """Return (year, semester, label)."""
    if 0 <= i < len(INDEX_TO_YEAR_SEM):
        yl, sem = INDEX_TO_YEAR_SEM[i]
        label = "{} - {}".format(yl.replace(" ", " ").title(), sem.replace(" ", " ").title())
        return yl, sem, label
    return _synthetic_year_sem(i)


def _format_req_token(tok: Any) -> str:
    if isinstance(tok, str):
        return tok.strip()
    if isinstance(tok, list) and tok:
        parts = [_format_req_token(t) for t in tok if t]
        return " OR ".join(p for p in parts if p)
    return ""


def _format_req_list(arr: Any) -> str:
    if not isinstance(arr, list):
        return ""
    parts = [_format_req_token(x) for x in arr]
    return "; ".join(p for p in parts if p)


def _map_approved_json_to_sections(
    approved: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, tuple[Any, ...]], bool] | None:
    """
    Build curriculum_sections + course_lookup from approved_json.
    Returns None if mapping should fall back to static (missing orig_sem, empty courses, etc.).
    """
    from pos.pos_pdf import display_pos_course_code

    raw_courses = approved.get("courses")
    if not isinstance(raw_courses, dict) or not raw_courses:
        return None

    caps = approved.get("max_units_by_semester_index")
    cap_len = len(caps) if isinstance(caps, list) and caps else 0
    ui_cap = max(len(INDEX_TO_YEAR_SEM), cap_len)
    if ui_cap < 1:
        ui_cap = len(INDEX_TO_YEAR_SEM)

    per_sem: dict[int, list[dict[str, Any]]] = {}
    lookup: dict[str, tuple[Any, ...]] = {}
    max_orig = 0

    for code, rec in raw_courses.items():
        if not isinstance(rec, dict):
            return None
        try:
            orig = int(rec.get("orig_sem"))
        except (TypeError, ValueError):
            return None
        if orig < 1:
            return None
        max_orig = max(max_orig, orig)
        title = str(rec.get("course_title") or code).strip() or code
        try:
            units = rec.get("units")
            u = float(units) if units is not None and str(units).strip() != "" else 0.0
        except (TypeError, ValueError):
            u = 0.0
        if u == int(u):
            u_disp: int | float = int(u)
        else:
            u_disp = u
        pre = _format_req_list(rec.get("pre-requisites"))
        co = _format_req_list(rec.get("co-requisites"))
        lec, lab = 0, 0
        row = {
            "code": code,
            "display_code": display_pos_course_code(code),
            "title": title,
            "units": u_disp,
            "lec": lec,
            "lab": lab,
            "prereq": pre,
            "coreq": co,
        }
        per_sem.setdefault(orig, []).append(row)
        if code not in lookup:
            lookup[code] = (title, u_disp, lec, lab, pre, co)

    max_index = max_orig
    truncated = max_index > ui_cap
    n_sections = min(max(max_index, 1), ui_cap)

    sections: list[dict[str, Any]] = []
    for i in range(n_sections):
        yl, sem, label = _section_label_for_index(i)
        orig = i + 1
        courses = list(per_sem.get(orig, []))
        sections.append({"year": yl, "semester": sem, "label": label, "courses": courses})

    return sections, lookup, truncated


def resolve_generate_curriculum_context(student) -> GenerateCurriculumContext:
    """
    Return curriculum UI context for Generate POS.

    ``mode`` is ``db`` when an approved CurriculumVersion for the student's program is loaded
    and successfully mapped; otherwise ``static`` (legacy BSCpE ``POS_CURRICULUM``).
    """
    static_sections, static_lookup = _static_sections_and_lookup()
    base_static: GenerateCurriculumContext = {
        "mode": "static",
        "curriculum_version_id": None,
        "curriculum_sections": static_sections,
        "course_lookup": static_lookup,
        "display_title": "BSCpE (reference curriculum)",
        "subtitle": "Legacy static curriculum — no approved DB version resolved for your account.",
        "curriculum_truncated": False,
    }

    vid = _curriculum_version_id_for_generate(student)
    if vid is None or not getattr(student, "program_id", None):
        return base_static

    cv = (
        CurriculumVersion.objects.filter(
            pk=int(vid),
            program_id=int(student.program_id),
            status=CurriculumVersion.STATUS_APPROVED,
        )
        .only("approved_json", "pk")
        .first()
    )
    if cv is None or not isinstance(cv.approved_json, dict):
        return base_static

    mapped = _map_approved_json_to_sections(cv.approved_json)
    if mapped is None:
        return base_static

    sections, lookup, truncated = mapped
    if not any(sec.get("courses") for sec in sections):
        return base_static

    meta = cv.approved_json.get("metadata") if isinstance(cv.approved_json.get("metadata"), dict) else {}
    label = (meta.get("curriculum_version_label") or "").strip() or f"Version {cv.pk}"
    prog = getattr(student, "program", None)
    pname = getattr(prog, "name", None) or getattr(prog, "slug", None) or ""
    display = f"{label}" + (f" — {pname}" if pname else "")

    return {
        "mode": "db",
        "curriculum_version_id": int(cv.pk),
        "curriculum_sections": sections,
        "course_lookup": lookup,
        "display_title": display,
        "subtitle": "From your selected or default approved curriculum.",
        "curriculum_truncated": truncated,
    }
