"""Tests for canonical curriculum section labels."""

from django.test import SimpleTestCase

from pos.curriculum_section_labels import section_meta_for_index


class SectionLabelTests(SimpleTestCase):
    def test_first_year_labels_preserve_ordinal_suffix(self) -> None:
        _yl, _sem, label0 = section_meta_for_index(0)
        self.assertEqual(label0, "1st Year - First Semester")
        _yl, _sem, label1 = section_meta_for_index(1)
        self.assertEqual(label1, "1st Year - Second Semester")

    def test_fourth_year_second_sem_label(self) -> None:
        _yl, _sem, label = section_meta_for_index(10)
        self.assertEqual(label, "4th Year - Second Semester")
