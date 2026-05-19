"""Tests for Generate POS semester horizon beyond natural program length."""

from django.test import SimpleTestCase

from pos.curriculum_section_labels import section_meta_for_index
from pos.views import _extend_curriculum_sections_through


class ExtendCurriculumSectionsTests(SimpleTestCase):
    def test_extends_through_fifth_year_second_sem(self) -> None:
        sections = [
            {"year": "1st Year", "semester": "First Semester", "label": "1st Year - First Semester", "courses": []},
        ]
        _extend_curriculum_sections_through(sections, 13)
        self.assertEqual(len(sections), 14)
        yl, sem, _ = section_meta_for_index(13)
        self.assertEqual(sections[13]["year"], yl)
        self.assertEqual(sections[13]["semester"], sem)

    def test_noop_for_none(self) -> None:
        sections = [{"year": "1st Year", "semester": "First Semester", "label": "x", "courses": []}]
        _extend_curriculum_sections_through(sections, None)
        self.assertEqual(len(sections), 1)
