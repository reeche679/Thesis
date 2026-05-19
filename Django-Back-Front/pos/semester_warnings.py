"""Non-blocking POS warnings shown to students (Generate / Preview)."""

from __future__ import annotations

import re
from typing import Any

LOW_UNITS_THRESHOLD = 15
LOW_UNITS_WARNING_MESSAGE = (
    "One or more semesters have fewer than 15 units. Please consult your academic adviser."
)

REPEAT_FAIL_WARNING_THRESHOLD = 3
REPEAT_FAIL_WARNING_MESSAGE = (
    "One or more courses have been failed three or more times. "
    "Please consult your academic adviser."
)

_OJT_COURSE_PATTERN = re.compile(
    r"on[-\s]?the[-\s]?job\s+training|\bojt\b|internship",
    re.IGNORECASE,
)


def is_midterm_ui_section(section_index: int) -> bool:
    """0-based curriculum section index: 0/1 = 1st/2nd sem, 2 = midterm per year band."""
    return int(section_index) % 3 == 2


def is_midterm_solver_semester(semester_index: int) -> bool:
    """1-based solver semester_index: 3, 6, 9, … are midterm terms."""
    return int(semester_index) % 3 == 0


def _parse_units(value: Any) -> float:
    try:
        return float(value if value is not None and str(value).strip() != "" else 0)
    except (TypeError, ValueError):
        return 0.0


def _label_is_midterm(label: str) -> bool:
    return "midterm" in (label or "").lower()


def _course_text_is_ojt(code: Any, title: Any) -> bool:
    text = f"{code or ''} {title or ''}".strip()
    if not text:
        return False
    return bool(_OJT_COURSE_PATTERN.search(text))


def _semester_has_ojt_course(courses: list[Any] | None) -> bool:
    for course in courses or []:
        if not isinstance(course, dict):
            continue
        if _course_text_is_ojt(course.get("code"), course.get("title")):
            return True
    return False


def _display_section_has_ojt(item: dict[str, Any]) -> bool:
    for row in item.get("courses_with_grades") or []:
        if not isinstance(row, dict):
            continue
        course = row.get("course") if isinstance(row.get("course"), dict) else row
        if isinstance(course, dict) and _course_text_is_ojt(course.get("code"), course.get("title")):
            return True
    sec = item.get("section") if isinstance(item.get("section"), dict) else {}
    return _semester_has_ojt_course(sec.get("courses"))


def low_units_warning_for_display_sections(display_sections: list[dict[str, Any]] | None) -> str | None:
    """Check Generate-page section tables (0-based section_index)."""
    for item in display_sections or []:
        if not isinstance(item, dict):
            continue
        si = item.get("section_index")
        sec = item.get("section") if isinstance(item.get("section"), dict) else {}
        label = str(item.get("label") or sec.get("label") or "")
        if si is not None:
            try:
                if is_midterm_ui_section(int(si)):
                    continue
            except (TypeError, ValueError):
                pass
        elif _label_is_midterm(label):
            continue
        if _display_section_has_ojt(item):
            continue
        units = _parse_units(item.get("total_units"))
        if units < LOW_UNITS_THRESHOLD:
            return LOW_UNITS_WARNING_MESSAGE
    return None


def _is_explicit_failing_grade(grade: Any) -> bool:
    """True only for an explicit failing grade (5.00), not blank rows."""
    if grade is None or (isinstance(grade, str) and not str(grade).strip()):
        return False
    s = str(grade).strip()
    if s in ("5", "5.0", "5.00"):
        return True
    try:
        return float(grade) >= 5.0
    except (TypeError, ValueError):
        return False


def failed_attempt_counts_by_course(
    grades_by_section: dict[str, Any] | None,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not isinstance(grades_by_section, dict):
        return counts
    for section_grades in grades_by_section.values():
        if not isinstance(section_grades, dict):
            continue
        for code, grade in section_grades.items():
            if not code or not _is_explicit_failing_grade(grade):
                continue
            code_s = str(code).strip()
            if code_s:
                counts[code_s] = counts.get(code_s, 0) + 1
    return counts


def _course_title_from_lookup(code: str, course_lookup: dict[str, Any] | None) -> str:
    if not course_lookup:
        return ""
    info = course_lookup.get(code)
    if isinstance(info, dict):
        return str(info.get("title") or "").strip()
    if isinstance(info, (list, tuple)) and info:
        return str(info[0] or "").strip()
    return ""


def repeat_fail_warning_for_grades(
    grades_by_section: dict[str, Any] | None,
    course_lookup: dict[str, Any] | None = None,
    *,
    threshold: int = REPEAT_FAIL_WARNING_THRESHOLD,
) -> str | None:
    """Non-blocking warning when any course has >= threshold explicit fails in entered tables."""
    counts = failed_attempt_counts_by_course(grades_by_section)
    violators = [(code, n) for code, n in counts.items() if n >= threshold]
    if not violators:
        return None
    violators.sort(key=lambda item: (-item[1], item[0]))
    if len(violators) == 1:
        code, count = violators[0]
        title = _course_title_from_lookup(code, course_lookup)
        label = f"{code} ({title})" if title else code
        return (
            f"You have failed {label} {count} times. "
            "Please consult your academic adviser."
        )
    return REPEAT_FAIL_WARNING_MESSAGE


def low_units_warning_for_solver_pos(pos_list: list[dict[str, Any]] | None) -> str | None:
    """Check solver output semesters (1-based semester_index). Skips empty semesters."""
    for sem in pos_list or []:
        if not isinstance(sem, dict):
            continue
        try:
            si = int(sem.get("semester_index"))
        except (TypeError, ValueError):
            continue
        if si < 1:
            continue
        if is_midterm_solver_semester(si):
            continue
        courses = sem.get("courses") or []
        if not isinstance(courses, list) or not any(
            isinstance(c, dict) and str(c.get("code") or "").strip() for c in courses
        ):
            continue
        if _semester_has_ojt_course(courses):
            continue
        units = _parse_units(sem.get("total_units"))
        if units < LOW_UNITS_THRESHOLD:
            return LOW_UNITS_WARNING_MESSAGE
    return None
