"""Unit tests for curriculum PDF term timeline and table backfill."""

from django.test import SimpleTestCase

from pos.curriculum_pdf_extract import (
    _align_table_rows_to_text_timeline,
    _finalize_row,
    _header_cell_category,
    _is_section_header_joined,
    _is_structural_band_heading_line,
    _page_text_course_timeline,
    _parse_table_rows,
    _table_looks_like_cmo_comparison_matrix,
    _TermState,
    _timeline_has_section_context,
    _truncate_combined_text_at_pos_section_end,
)


class PageTextCourseTimelineTests(SimpleTestCase):
    def test_timeline_two_sections_distinct_term_indices(self) -> None:
        text = (
            "FIRST YEAR - FIRST SEMESTER\n"
            "MATH 101 College Algebra\n"
            "SECOND YEAR - FIRST SEMESTER\n"
            "CpE 201 Logic Circuits\n"
        )
        tl = _page_text_course_timeline(text, [])
        self.assertTrue(_timeline_has_section_context(tl))
        self.assertEqual(len(tl), 2)
        self.assertEqual(tl[0]["code"], "MATH 101")
        self.assertEqual(tl[0]["term_index"], 1)
        self.assertEqual(tl[1]["code"], "CpE 201")
        self.assertEqual(tl[1]["term_index"], 2)

    def test_duplicate_codes_preserve_order_and_same_band(self) -> None:
        text = (
            "FIRST YEAR - FIRST SEMESTER\n"
            "CpE 401 Elective A\n"
            "CpE 401 Elective B\n"
        )
        tl = _page_text_course_timeline(text, [])
        self.assertEqual(len(tl), 2)
        self.assertEqual(tl[0]["code"], "CpE 401")
        self.assertEqual(tl[1]["code"], "CpE 401")
        self.assertEqual(tl[0]["term_index"], tl[1]["term_index"])

    def test_reverse_order_semester_year_heading(self) -> None:
        text = (
            "First Semester, First Year\n"
            "GEd 108 Art Appreciation\n"
        )
        tl = _page_text_course_timeline(text, [])
        self.assertTrue(_timeline_has_section_context(tl))
        self.assertEqual(len(tl), 1)
        self.assertEqual(tl[0]["code"], "GEd 108")
        self.assertEqual(tl[0]["term_index"], 1)

    def test_no_section_no_context_flag(self) -> None:
        text = "MATH 101 lone line without heading\n"
        tl = _page_text_course_timeline(text, [])
        self.assertEqual(len(tl), 1)
        self.assertFalse(_timeline_has_section_context(tl))

    def test_identical_heading_lines_dedupe_one_band(self) -> None:
        text = (
            "FIRST YEAR - FIRST SEMESTER\n"
            "FIRST YEAR - FIRST SEMESTER\n"
            "MATH 101 Algebra\n"
            "ENGG 101 Eng math\n"
        )
        tl = _page_text_course_timeline(text, [])
        self.assertEqual(len(tl), 2)
        self.assertEqual(tl[0]["term_index"], 1)
        self.assertEqual(tl[1]["term_index"], 1)

    def test_course_line_with_semester_words_not_a_heading(self) -> None:
        self.assertFalse(
            _is_structural_band_heading_line("MATH 101 Explaining first semester policies and rules"),
        )

    def test_bsee_style_table_header_row_not_section_banner(self) -> None:
        joined = (
            "Course Code | Course Title | Credit Units | Lec | lab | "
            "Prerequisite(s) | Co-requisite(s) | Category"
        )
        self.assertFalse(_is_section_header_joined(joined, []))

    def test_letterhead_curriculum_not_band_heading(self) -> None:
        self.assertFalse(
            _is_structural_band_heading_line(
                "E-mail Address: curriculum.instruction@g.batstate-u.edu.ph | Website Address: http://example.edu/",
            ),
        )
        self.assertFalse(_is_structural_band_heading_line("Office of Curriculum and Instruction"))

    def test_runon_firstyear_firstsemester_bs_pdf_style(self) -> None:
        text = (
            "PROGRAMOFSTUDY\n"
            "FIRSTYEAR\n"
            "FirstSemester\n"
            "No.ofHour/s\n"
            "ENGG401 IntroductiontoEngineering\n"
            "FIRSTYEAR\n"
            "SecondSemester\n"
            "CpE401 ComputerProgramming1\n"
        )
        tl = _page_text_course_timeline(text, [])
        self.assertTrue(_timeline_has_section_context(tl))
        self.assertEqual(len(tl), 2)
        self.assertEqual(tl[0]["code"], "ENGG 401")
        self.assertEqual(tl[0]["term_index"], 1)
        self.assertIn("FIRSTYEAR", tl[0]["term_label"])
        self.assertEqual(tl[1]["code"], "CpE 401")
        self.assertEqual(tl[1]["term_index"], 2)

        page_a = "FIRST YEAR - FIRST SEMESTER\n"
        page_b = "MATH 101 College Algebra\n"
        tl = _page_text_course_timeline(f"{page_a}\n{page_b}", [])
        self.assertTrue(_timeline_has_section_context(tl))
        self.assertEqual(len(tl), 1)
        self.assertEqual(tl[0]["code"], "MATH 101")
        self.assertEqual(tl[0]["term_index"], 1)


class AlignTableRowsToTimelineTests(SimpleTestCase):
    def test_backfill_zero_term_rows(self) -> None:
        text = (
            "FIRST YEAR - FIRST SEMESTER\n"
            "MATH 101 x\n"
            "SECOND YEAR - FIRST SEMESTER\n"
            "CpE 201 y\n"
        )
        tl = _page_text_course_timeline(text, [])
        rows: list[dict] = [
            {
                "code": "MATH 101",
                "term_index": 0,
                "term_label": "",
                "term": "",
                "confidence": 0.9,
                "source": {"kind": "pdf_table", "page": 1},
            },
            {
                "code": "CpE 201",
                "term_index": 0,
                "term_label": "",
                "term": "",
                "confidence": 0.9,
                "source": {"kind": "pdf_table", "page": 1},
            },
        ]
        w: list[str] = []
        _align_table_rows_to_text_timeline(rows, tl, w, page=1, backfill_source="page_text_timeline")
        self.assertEqual(rows[0]["term_index"], 1)
        self.assertEqual(rows[1]["term_index"], 2)
        self.assertTrue(rows[0]["term_label"])
        self.assertEqual(rows[0]["source"].get("term_backfill"), "page_text_timeline")
        self.assertLessEqual(rows[0]["confidence"], 0.9)

    def test_align_two_duplicate_cp401_rows(self) -> None:
        text = (
            "FIRST YEAR - FIRST SEMESTER\n"
            "CpE 401 Elective A\n"
            "CpE 401 Elective B\n"
        )
        tl = _page_text_course_timeline(text, [])
        rows: list[dict] = [
            {
                "code": "CpE 401",
                "term_index": 0,
                "term_label": "",
                "term": "",
                "confidence": 0.85,
                "source": {"kind": "pdf_table"},
            },
            {
                "code": "CpE 401",
                "term_index": 0,
                "term_label": "",
                "term": "",
                "confidence": 0.85,
                "source": {"kind": "pdf_table"},
            },
        ]
        w: list[str] = []
        _align_table_rows_to_text_timeline(rows, tl, w, page=-1, backfill_source="document_text_timeline")
        self.assertEqual(rows[0]["term_index"], 1)
        self.assertEqual(rows[1]["term_index"], 1)
        self.assertEqual(rows[0]["source"].get("term_backfill"), "document_text_timeline")
        self.assertEqual(rows[1]["source"].get("term_backfill"), "document_text_timeline")

    def test_does_not_overwrite_existing_term(self) -> None:
        text = "FIRST YEAR - FIRST SEMESTER\nMATH 101 x\n"
        tl = _page_text_course_timeline(text, [])
        rows: list[dict] = [
            {
                "code": "MATH 101",
                "term_index": 5,
                "term_label": "Already set",
                "term": "Already set",
                "confidence": 0.95,
                "source": {"kind": "pdf_table"},
            },
        ]
        w: list[str] = []
        _align_table_rows_to_text_timeline(rows, tl, w, page=1, backfill_source="page_text_timeline")
        self.assertEqual(rows[0]["term_index"], 5)
        self.assertEqual(rows[0]["term_label"], "Already set")


class PosRegionGateTests(SimpleTestCase):
    def test_truncates_at_comparison_of_cmo_heading(self) -> None:
        text = (
            "FIRST YEAR - FIRST SEMESTER\n"
            "MATH 101 Algebra\n"
            "XIII. Comparison of Proposed Curriculum Structure with CMO of the Program\n"
            "MATH 999 Should Not Appear\n"
        )
        cut, did = _truncate_combined_text_at_pos_section_end(text)
        self.assertTrue(did)
        self.assertIn("MATH 101", cut)
        self.assertNotIn("MATH 999", cut)

    def test_no_truncation_when_no_heading(self) -> None:
        text = "FIRST YEAR - FIRST SEMESTER\nMATH 101 x\n"
        cut, did = _truncate_combined_text_at_pos_section_end(text)
        self.assertFalse(did)
        self.assertEqual(cut, text)

    def test_detects_cmo_comparison_matrix_table(self) -> None:
        table = [
            ["", "", "", "", "", "Existing", "", "Proposed", "Remarks"],
            ["1", "MATH 101", "Algebra", "3", "3", "3", "3", "3", "note"],
        ]
        self.assertTrue(_table_looks_like_cmo_comparison_matrix(table))

    def test_pos_course_table_not_comparison_matrix(self) -> None:
        table = [
            ["Course Code", "Course Title", "Credit Units", "Lec", "lab"],
            ["MATH 101", "Algebra", "3", "3", "0"],
        ]
        self.assertFalse(_table_looks_like_cmo_comparison_matrix(table))


class ParseTableSplitTitleTests(SimpleTestCase):
    """BSEE-style rows where pdfplumber splits one course across three lines (EE 303 / EE 305)."""

    def test_merges_split_title_onto_following_code_row(self) -> None:
        table = [
            ["", "THIRD YEAR", None, None, None, None, None, None, None, ""],
            ["", "SECOND SEMESTER", None, None, None, None, None, None, None, ""],
            [
                "Course\nCode",
                None,
                "Course Title",
                "Credit\nUnits",
                "Lec",
                "lab",
                "Prerequisite(s)",
                "Co-\nrequisite(s)",
                "Category",
                None,
            ],
            ["", None, "Electrical Standards and", "", "0", "3", "EE 303", "", "TC-E", None],
            ["EE 305", None, None, "1", None, None, None, None, None, None],
            [None, None, "Practices", None, None, None, None, None, None, None],
            [
                "EE 306",
                None,
                "Power System Analysis",
                "5",
                "4",
                "3",
                "EE 301, EE 302",
                "EE 305",
                "TC-E",
                None,
            ],
        ]
        state = _TermState()
        state.term_index = 1
        state.term_label = "THIRD YEAR SECOND SEMESTER"
        w: list[str] = []
        rows, _profile = _parse_table_rows(table, state, w, page=3, table_idx=1)
        by_code = {r["code"]: r for r in rows}
        self.assertIn("EE 305", by_code)
        self.assertIn("Electrical Standards and Practices", by_code["EE 305"]["title"])
        self.assertEqual(by_code["EE 305"]["units"], "1")


class CourseCodeTokenTests(SimpleTestCase):
    def test_pathfit_single_digit_number(self) -> None:
        from pos.curriculum_pdf_extract import _canonical_course_code, _extract_codes_from_text, _find_code_in_text

        self.assertEqual(_extract_codes_from_text("PATHFit 3 — elective"), ["PATHFit 3"])
        self.assertEqual(_find_code_in_text("PATHFIT 1")[0], "PATHFit 1")
        self.assertEqual(
            _extract_codes_from_text("Prerequisites: MATH 101, PATHFit 2"),
            ["MATH 101", "PATHFit 2"],
        )
        self.assertEqual(_canonical_course_code("pathfit 4"), "PATHFit 4")
        self.assertEqual(_canonical_course_code("PATHFIT 1"), "PATHFit 1")

    def test_common_subject_casing_polish(self) -> None:
        from pos.curriculum_pdf_extract import _canonical_course_code, _extract_codes_from_text

        self.assertEqual(_canonical_course_code("nstp 111"), "NSTP 111")
        self.assertEqual(_canonical_course_code("GED 108"), "GEd 108")
        self.assertEqual(_canonical_course_code("gede 101"), "GEdE 101")
        self.assertEqual(_canonical_course_code("math 101"), "MATH 101")
        self.assertEqual(_canonical_course_code("engg 102"), "ENGG 102")
        self.assertEqual(_canonical_course_code("cpe 202"), "CpE 202")
        self.assertEqual(
            _extract_codes_from_text("Take nstp 121 after NSTP 111"),
            ["NSTP 121", "NSTP 111"],
        )

    def test_standard_three_digit_courses_unchanged(self) -> None:
        from pos.curriculum_pdf_extract import _extract_codes_from_text

        self.assertEqual(
            _extract_codes_from_text("ENGG 116, EE 306, CpE 301"),
            ["ENGG 116", "EE 306", "CpE 301"],
        )


class HeaderSynonymTests(SimpleTestCase):
    def test_course_title_cell_maps_to_title_not_course(self) -> None:
        self.assertEqual(_header_cell_category("Course Title"), "title")
        self.assertEqual(_header_cell_category("Course Code"), "course")

    def test_hyphenated_line_break_in_corequisite_header(self) -> None:
        self.assertEqual(_header_cell_category("Co-\nrequisite(s)"), "coreq")


class FinalizeRowStandingTests(SimpleTestCase):
    def _base_row(self) -> dict:
        return {
            "code": "TEST 101",
            "term_index": 1,
            "term_label": "FIRST",
            "term": "FIRST",
            "confidence": 0.9,
            "source": {"kind": "pdf_table"},
        }

    def test_no_warning_prerequisite_year_standing_only(self) -> None:
        row = self._base_row()
        row["raw_prerequisites"] = "4th Year Standing"
        row["raw_corequisites"] = ""
        w: list[str] = []
        _finalize_row(row, units_from_header_map=True, prereq_from_map=True, coreq_from_map=True, warnings=w)
        self.assertEqual(w, [])
        self.assertEqual(row["prerequisite_codes"], [])

    def test_no_warning_corequisite_graduating_only(self) -> None:
        row = self._base_row()
        row["raw_prerequisites"] = ""
        row["raw_corequisites"] = "Graduating"
        w: list[str] = []
        _finalize_row(row, units_from_header_map=True, prereq_from_map=True, coreq_from_map=True, warnings=w)
        self.assertEqual(w, [])

    def test_still_warns_when_prereq_is_unparsed_prose(self) -> None:
        row = self._base_row()
        row["raw_prerequisites"] = "Department chair approval required for this substitution request"
        row["raw_corequisites"] = ""
        w: list[str] = []
        _finalize_row(row, units_from_header_map=True, prereq_from_map=True, coreq_from_map=True, warnings=w)
        self.assertTrue(any("prerequisite cell has text" in x for x in w))
