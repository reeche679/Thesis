"""Tests for per-program PDF header field helpers."""

from django.test import SimpleTestCase

from pos.pdf_header_fields import (
    DEFAULT_PDF_DEPARTMENT_LABEL,
    DEFAULT_PDF_PROGRAM_CURRICULUM,
    DEFAULT_PDF_PROGRAM_SHORTCUT,
    merge_pdf_header_fields,
    pdf_header_fields_for_program,
)


class _FakeProgram:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class PdfHeaderFieldsTests(SimpleTestCase):
    def test_defaults_without_program(self) -> None:
        fields = pdf_header_fields_for_program(None)
        self.assertEqual(fields["department"], DEFAULT_PDF_DEPARTMENT_LABEL)
        self.assertEqual(fields["program_curriculum"], DEFAULT_PDF_PROGRAM_CURRICULUM)
        self.assertEqual(fields["program_shortcut"], DEFAULT_PDF_PROGRAM_SHORTCUT)

    def test_program_overrides(self) -> None:
        prog = _FakeProgram(
            pdf_department_label="Department of Electrical Engineering",
            pdf_program_curriculum="BACHELOR OF SCIENCE IN ELECTRICAL ENGINEERING",
            pdf_program_shortcut="BS ELECTRICAL ENGINEERING",
        )
        fields = pdf_header_fields_for_program(prog)
        self.assertEqual(fields["department"], "Department of Electrical Engineering")
        self.assertIn("ELECTRICAL", fields["program_curriculum"])

    def test_merge_request_overrides_program(self) -> None:
        prog = _FakeProgram(pdf_department_label="Dept A")
        merged = merge_pdf_header_fields(
            pdf_header_fields_for_program(prog),
            {"pdf_department": "Dept B"},
        )
        self.assertEqual(merged["department"], "Dept B")
