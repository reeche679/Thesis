"""Template profile detection and evaluation-format helpers (BSEE catalog regression)."""

from django.test import SimpleTestCase

from pos.canonical_term_grid import orig_sem_from_term_label
from pos.curriculum_draft_analyze import analyze_draft_json
from pos.curriculum_pdf_extract import _build_col_map, _header_cell_category, _parse_table_rows
from pos.curriculum_pdf_profiles import (
    PROFILE_CATALOG_8COL,
    PROFILE_EVALUATION_5COL,
    detect_table_profile,
    looks_like_grade_not_units,
    merge_document_profile,
    normalize_term_label,
    split_combined_requisite_cell,
)
from pos.curriculum_pdf_extract import _TermState


class ProfileDetectionTests(SimpleTestCase):
    def test_bsee_catalog_table(self) -> None:
        table = [
            ["Course Code", "Course Title", "Credit Units", "Lec", "lab", "Prerequisite(s)", "Co-requisite(s)", "Category"],
            ["MATH 101", "Algebra", "3", "3", "0", "", "", "TC"],
        ]
        self.assertEqual(detect_table_profile(table), PROFILE_CATALOG_8COL)

    def test_evaluation_table(self) -> None:
        table = [
            ["Course Code", "Course Title", "Unit/s", "Pre-requisite / Co-requisite", "Grade"],
            ["MATH 401", "Differential Calculus", "3", "MATH 401", ""],
        ]
        self.assertEqual(detect_table_profile(table), PROFILE_EVALUATION_5COL)

    def test_merge_defaults_to_catalog_on_tie(self) -> None:
        self.assertEqual(
            merge_document_profile([PROFILE_CATALOG_8COL, PROFILE_EVALUATION_5COL]),
            PROFILE_CATALOG_8COL,
        )


class EvaluationColMapTests(SimpleTestCase):
    def test_unit_slash_header_maps_units(self) -> None:
        self.assertEqual(_header_cell_category("Unit/s"), "units")

    def test_build_col_map_evaluation(self) -> None:
        table = [
            ["Course Code", "Course Title", "Unit/s", "Pre-requisite / Co-requisite", "Grade"],
            ["MATH 401", "Differential Calculus", "3", "", ""],
        ]
        w: list[str] = []
        col_map, profile = _build_col_map(table, w)
        self.assertEqual(profile, PROFILE_EVALUATION_5COL)
        self.assertEqual(col_map.get("units"), 2)
        self.assertEqual(col_map.get("grade"), 4)
        self.assertFalse(any("no UNITS column" in x for x in w))


class EvaluationRowParseTests(SimpleTestCase):
    def test_grade_not_used_as_units(self) -> None:
        table = [
            ["Course Code", "Course Title", "Unit/s", "Pre-requisite / Co-requisite", "Grade"],
            ["MATH 401", "Differential Calculus", "3", "", "1.25"],
        ]
        state = _TermState()
        state.term_index = 1
        state.term_label = "FIRST YEAR First Semester"
        w: list[str] = []
        rows, profile = _parse_table_rows(table, state, w, page=1, table_idx=0)
        self.assertEqual(profile, PROFILE_EVALUATION_5COL)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["units"], "3")

    def test_semester_total_row_skipped(self) -> None:
        table = [
            ["", "FIRST YEAR", "", "", ""],
            ["", "First Semester", "", "", ""],
            ["Course Code", "Course Title", "Unit/s", "Pre-requisite / Co-requisite", "Grade"],
            ["MATH 401", "Algebra", "3", "", ""],
            ["25", "", "", "", ""],
        ]
        state = _TermState()
        w: list[str] = []
        rows, _ = _parse_table_rows(table, state, w, page=1, table_idx=0)
        codes = [r["code"] for r in rows]
        self.assertIn("MATH 401", codes)
        self.assertNotIn("25", codes)

    def test_combined_prereq_split(self) -> None:
        pre, co = split_combined_requisite_cell("MATH 401 / MATH 402")
        self.assertIn("MATH 401", pre)
        self.assertIn("MATH 402", pre)


class TermLabelNormalizeTests(SimpleTestCase):
    def test_runon_firstyear_first_semester(self) -> None:
        norm = normalize_term_label("FIRSTYEAR First Semester", PROFILE_EVALUATION_5COL)
        self.assertEqual(norm, "1st Year - First Semester")
        self.assertEqual(orig_sem_from_term_label(norm), 1)

    def test_catalog_heading_unchanged_when_canonical(self) -> None:
        norm = normalize_term_label("1st Year - First Semester", PROFILE_CATALOG_8COL)
        self.assertEqual(norm, "1st Year - First Semester")


class AnalyzeProfileTests(SimpleTestCase):
    def test_analyze_uses_normalized_label_for_evaluation(self) -> None:
        draft = {
            "extracted_rows": [
                {
                    "draft_row_id": "r1",
                    "code": "MATH 401",
                    "term_index": 1,
                    "term_label": "1st Year - First Semester",
                    "raw_prerequisites": "",
                    "raw_corequisites": "",
                    "confidence": 0.9,
                }
            ],
            "extraction_meta": {
                "template_profile": PROFILE_EVALUATION_5COL,
                "term_labels_normalized": 1,
            },
        }
        da = analyze_draft_json(draft)
        self.assertEqual(da["template_profile"], PROFILE_EVALUATION_5COL)
        self.assertEqual(da["rows"]["r1"]["orig_sem_source"], "label")
        self.assertEqual(da["rows"]["r1"]["orig_sem_suggested"], 1)


class GradeHeuristicTests(SimpleTestCase):
    def test_grade_value_on_evaluation_profile(self) -> None:
        self.assertTrue(looks_like_grade_not_units("1.25", profile=PROFILE_EVALUATION_5COL))

    def test_units_not_grade_on_catalog(self) -> None:
        self.assertFalse(looks_like_grade_not_units("3", profile=PROFILE_CATALOG_8COL))
