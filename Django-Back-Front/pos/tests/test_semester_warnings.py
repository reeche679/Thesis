"""Tests for low-units semester warnings."""

from django.test import SimpleTestCase

from pos.semester_warnings import (
    LOW_UNITS_WARNING_MESSAGE,
    REPEAT_FAIL_WARNING_MESSAGE,
    is_midterm_solver_semester,
    is_midterm_ui_section,
    low_units_warning_for_display_sections,
    low_units_warning_for_solver_pos,
    repeat_fail_warning_for_grades,
)


class SemesterWarningsTests(SimpleTestCase):
    def test_midterm_detection(self) -> None:
        self.assertFalse(is_midterm_ui_section(0))
        self.assertFalse(is_midterm_ui_section(1))
        self.assertTrue(is_midterm_ui_section(2))
        self.assertTrue(is_midterm_solver_semester(3))
        self.assertFalse(is_midterm_solver_semester(2))

    def test_solver_pos_warns_low_first_sem(self) -> None:
        pos = [
            {"semester_index": 1, "total_units": 14, "courses": [{"code": "A"}]},
        ]
        self.assertEqual(low_units_warning_for_solver_pos(pos), LOW_UNITS_WARNING_MESSAGE)

    def test_solver_pos_skips_midterm(self) -> None:
        pos = [
            {"semester_index": 3, "total_units": 7, "courses": [{"code": "A"}]},
        ]
        self.assertIsNone(low_units_warning_for_solver_pos(pos))

    def test_solver_pos_ok_at_threshold(self) -> None:
        pos = [
            {"semester_index": 2, "total_units": 15, "courses": [{"code": "A"}]},
        ]
        self.assertIsNone(low_units_warning_for_solver_pos(pos))

    def test_display_sections_warns(self) -> None:
        sections = [
            {"section_index": 1, "total_units": 12, "section": {"label": "1st Year - Second Semester"}},
        ]
        self.assertEqual(low_units_warning_for_display_sections(sections), LOW_UNITS_WARNING_MESSAGE)

    def test_solver_pos_skips_ojt_semester(self) -> None:
        pos = [
            {
                "semester_index": 11,
                "total_units": 8,
                "courses": [{"code": "ENGG 117", "title": "OJT/Internship (320 hrs)"}],
            },
        ]
        self.assertIsNone(low_units_warning_for_solver_pos(pos))

    def test_display_sections_skips_ojt(self) -> None:
        sections = [
            {
                "section_index": 10,
                "total_units": 8,
                "section": {"label": "4th Year - Second Semester"},
                "courses_with_grades": [
                    {"course": {"code": "ENGG 117", "title": "On-the-Job Training"}},
                ],
            },
        ]
        self.assertIsNone(low_units_warning_for_display_sections(sections))

    def test_repeat_fail_warns_at_three(self) -> None:
        grades = {
            "0": {"MATH 101": "5.0"},
            "3": {"MATH 101": "5"},
            "6": {"MATH 101": "5.00"},
        }
        lookup = {"MATH 101": {"title": "Differential Calculus"}}
        msg = repeat_fail_warning_for_grades(grades, lookup)
        self.assertIn("Differential Calculus", msg or "")
        self.assertIn("3 times", msg or "")

    def test_repeat_fail_ok_below_threshold(self) -> None:
        grades = {
            "0": {"MATH 101": "5.0"},
            "3": {"MATH 101": "5"},
        }
        self.assertIsNone(repeat_fail_warning_for_grades(grades))

    def test_repeat_fail_multiple_courses_generic_message(self) -> None:
        grades = {
            "0": {"MATH 101": "5", "MATH 102": "5.0"},
            "3": {"MATH 101": "5", "MATH 102": "5.0"},
            "6": {"MATH 101": "5", "MATH 102": "5.0"},
        }
        self.assertEqual(repeat_fail_warning_for_grades(grades), REPEAT_FAIL_WARNING_MESSAGE)
