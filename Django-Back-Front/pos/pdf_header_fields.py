"""PDF program header text (department line, curriculum title, shortcut)."""

from __future__ import annotations

from typing import Any

DEFAULT_PDF_DEPARTMENT_LABEL = "College of Engineering — Department of Electrical Engineering"
DEFAULT_PDF_PROGRAM_CURRICULUM = "BACHELOR OF SCIENCE IN COMPUTER ENGINEERING"
DEFAULT_PDF_PROGRAM_SHORTCUT = "BS COMPUTER ENGINEERING"

_MAX_DEPARTMENT = 300
_MAX_CURRICULUM = 300
_MAX_SHORTCUT = 120


def default_pdf_header_fields() -> dict[str, str]:
    return {
        "department": DEFAULT_PDF_DEPARTMENT_LABEL,
        "program_curriculum": DEFAULT_PDF_PROGRAM_CURRICULUM,
        "program_shortcut": DEFAULT_PDF_PROGRAM_SHORTCUT,
    }


def pdf_header_fields_for_program(program: Any | None) -> dict[str, str]:
    """Defaults from a Program row, or global BSCpE-style defaults."""
    out = default_pdf_header_fields()
    if program is None:
        return out
    dept = (getattr(program, "pdf_department_label", None) or "").strip()
    curr = (getattr(program, "pdf_program_curriculum", None) or "").strip()
    short = (getattr(program, "pdf_program_shortcut", None) or "").strip()
    if dept:
        out["department"] = dept[:_MAX_DEPARTMENT]
    if curr:
        out["program_curriculum"] = curr[:_MAX_CURRICULUM]
    if short:
        out["program_shortcut"] = short[:_MAX_SHORTCUT]
    return out


def _clean_part(value: Any, max_len: int) -> str:
    return str(value or "").strip()[:max_len]


def normalize_pdf_header_fields(raw: dict[str, Any] | None) -> dict[str, str]:
    """Accept several key aliases; drop empty values."""
    if not isinstance(raw, dict):
        return {}
    aliases = {
        "department": ("department", "pdf_department", "pdf_department_label"),
        "program_curriculum": (
            "program_curriculum",
            "pdf_program_curriculum",
            "pdf_curriculum",
        ),
        "program_shortcut": (
            "program_shortcut",
            "pdf_program_shortcut",
            "pdf_shortcut",
        ),
    }
    out: dict[str, str] = {}
    for canonical, keys in aliases.items():
        for key in keys:
            if key in raw and str(raw.get(key) or "").strip():
                cap = _MAX_DEPARTMENT if canonical == "department" else (
                    _MAX_CURRICULUM if canonical == "program_curriculum" else _MAX_SHORTCUT
                )
                out[canonical] = _clean_part(raw.get(key), cap)
                break
    return out


def merge_pdf_header_fields(*layers: dict[str, Any] | None) -> dict[str, str]:
    base = default_pdf_header_fields()
    for layer in layers:
        normalized = normalize_pdf_header_fields(layer)
        for key, value in normalized.items():
            if value:
                base[key] = value
    return base
