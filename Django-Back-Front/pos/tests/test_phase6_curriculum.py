"""Phase 6 curriculum: analyzer, builder, semester mapping, staff APIs."""

import json
import uuid

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase

from pos.admin import validate_approved_bundle
from pos.curriculum_draft_analyze import analyze_draft_json
from pos.curriculum_draft_semesters import term_index_to_orig_sem_map
from pos.curriculum_draft_to_approved import draft_to_approved_json
from pos.curriculum_unit_caps import (
    build_max_units_by_semester_index,
    validate_max_units_by_year_term,
)
from pos.models import CurriculumVersion, Program
from pos.staff_curriculum_views import (
    staff_api_curriculum_analyze,
    staff_api_curriculum_approve,
    staff_api_curriculum_draft,
)


def _default_caps() -> dict:
    """Full 18-cell max_units_by_year_term with uniform 24 for tests."""
    return {str(y): {"1": 24, "2": 24, "3": 24} for y in range(1, 7)}


def _json(resp):
    return json.loads(resp.content.decode())


def _row(
    code: str,
    term_index: int,
    page: int,
    table: int = 0,
    row_index: int = 0,
    *,
    term_label: str = "",
) -> dict:
    d = {
        "code": code,
        "title": f"T {code}",
        "units": "3",
        "term_index": term_index,
        "raw_prerequisites": "",
        "raw_corequisites": "",
        "prerequisite_codes": [],
        "corequisite_codes": [],
        "draft_row_id": str(uuid.uuid4()),
        "source": {"kind": "pdf_table", "page": page, "table": table, "row_index": row_index},
    }
    if term_label:
        d["term_label"] = term_label
    return d


class OrigSemBandTests(TestCase):
    def test_term_index_ordering_same_band_same_orig_sem(self) -> None:
        a = _row("MATH 101", 1, page=2, row_index=1)
        b = _row("ENGL 101", 1, page=1, row_index=5)
        rows = [a, b]
        ti_map = term_index_to_orig_sem_map(rows)
        self.assertEqual(ti_map[1], 1)
        da = analyze_draft_json({"extracted_rows": rows})
        self.assertEqual(da["rows"][a["draft_row_id"]]["orig_sem_suggested"], 1)
        self.assertEqual(da["rows"][b["draft_row_id"]]["orig_sem_suggested"], 1)

    def test_distinct_term_indices_map_sequential_orig_sem(self) -> None:
        rows = [
            _row("A 101", 2, page=1, row_index=0),
            _row("B 101", 1, page=2, row_index=0),
        ]
        ti_map = term_index_to_orig_sem_map(rows)
        self.assertEqual(ti_map[1], 1)
        self.assertEqual(ti_map[2], 2)

    def test_term_label_canonical_orig_sem_not_compressed_by_term_index(self) -> None:
        """Missing midterm in PDF: term_index bands compress; term_label still maps to slot 7."""
        row = _row("EE 201", 5, 1, term_label="3rd Year - First Semester")
        rows = [row]
        ti_map = term_index_to_orig_sem_map(rows)
        self.assertEqual(ti_map[5], 1)
        da = analyze_draft_json({"extracted_rows": rows})
        block = da["rows"][row["draft_row_id"]]
        self.assertEqual(block["orig_sem_suggested"], 7)
        self.assertEqual(block["orig_sem_source"], "label")
        self.assertEqual(block["year_level_suggested"], 3)
        self.assertTrue(any("term_index-only" in w for w in block.get("calendar_warnings") or []))

    def test_no_term_label_falls_back_to_term_index_map(self) -> None:
        row = _row("MATH 101", 2, 1)
        da = analyze_draft_json({"extracted_rows": [row]})
        block = da["rows"][row["draft_row_id"]]
        self.assertEqual(block["orig_sem_source"], "term_index")
        self.assertEqual(block["orig_sem_suggested"], 1)

    def test_approved_uses_year_level_suggested_when_year_level_omitted(self) -> None:
        rid = str(uuid.uuid4())
        row = {
            "draft_row_id": rid,
            "code": "EE 201",
            "title": "Signals",
            "units": "4",
            "term_index": 4,
            "term_label": "3rd Year - First Semester",
            "raw_prerequisites": "",
            "raw_corequisites": "",
            "prerequisite_codes": [],
            "corequisite_codes": [],
            "source": {"page": 1, "table": 0, "row_index": 0},
        }
        da = analyze_draft_json({"extracted_rows": [row]})
        draft = {"extracted_rows": [row], "draft_analysis": da, "max_units_by_year_term": _default_caps()}
        bundle = draft_to_approved_json(
            draft,
            program_slug="ee",
            curriculum_version_label="v1",
            source_pdf_sha256="a" * 64,
        )
        validate_approved_bundle(bundle)
        self.assertEqual(bundle["courses"]["EE 201"]["orig_sem"], 7)
        self.assertEqual(bundle["courses"]["EE 201"]["year_level"], 3)


class BuilderSchemaTests(TestCase):
    def test_builder_passes_schema(self) -> None:
        rid = str(uuid.uuid4())
        draft = {
            "extracted_rows": [
                {
                    "draft_row_id": rid,
                    "code": "MATH 101",
                    "title": "Algebra",
                    "units": "3",
                    "term_index": 1,
                    "year_level": 1,
                    "raw_prerequisites": "",
                    "raw_corequisites": "",
                    "prerequisite_codes": [],
                    "corequisite_codes": [],
                    "source": {"page": 1, "table": 0, "row_index": 0},
                }
            ],
            "draft_analysis": analyze_draft_json(
                {
                    "extracted_rows": [
                        {
                            "draft_row_id": rid,
                            "code": "MATH 101",
                            "title": "Algebra",
                            "units": "3",
                            "term_index": 1,
                            "raw_prerequisites": "",
                            "raw_corequisites": "",
                            "prerequisite_codes": [],
                            "corequisite_codes": [],
                            "source": {"page": 1, "table": 0, "row_index": 0},
                        }
                    ]
                }
            ),
            "max_units_by_year_term": _default_caps(),
        }
        bundle = draft_to_approved_json(
            draft,
            program_slug="test_prog",
            curriculum_version_label="v1",
            source_pdf_sha256="a" * 64,
        )
        validate_approved_bundle(bundle)
        self.assertEqual(bundle["schema_version"], 1)
        self.assertIn(bundle["courses"]["MATH 101"]["category"], ("Rigid", "Bottleneck"))
        self.assertGreaterEqual(len(bundle["max_units_by_semester_index"]), 18)

    def test_builder_respects_staff_category_override(self) -> None:
        rid = str(uuid.uuid4())
        draft = {
            "extracted_rows": [
                {
                    "draft_row_id": rid,
                    "code": "MATH 101",
                    "title": "Algebra",
                    "units": "3",
                    "term_index": 1,
                    "year_level": 1,
                    "category": "Bottleneck",
                    "raw_prerequisites": "",
                    "raw_corequisites": "",
                    "prerequisite_codes": [],
                    "corequisite_codes": [],
                    "source": {"page": 1, "table": 0, "row_index": 0},
                }
            ],
            "draft_analysis": analyze_draft_json(
                {
                    "extracted_rows": [
                        {
                            "draft_row_id": rid,
                            "code": "MATH 101",
                            "title": "Algebra",
                            "units": "3",
                            "term_index": 1,
                            "raw_prerequisites": "",
                            "raw_corequisites": "",
                            "prerequisite_codes": [],
                            "corequisite_codes": [],
                            "source": {"page": 1, "table": 0, "row_index": 0},
                        }
                    ]
                }
            ),
            "max_units_by_year_term": _default_caps(),
        }
        bundle = draft_to_approved_json(
            draft,
            program_slug="test_prog",
            curriculum_version_label="v1",
            source_pdf_sha256="a" * 64,
        )
        validate_approved_bundle(bundle)
        self.assertEqual(bundle["courses"]["MATH 101"]["category"], "Bottleneck")

    def test_builder_fails_without_caps(self) -> None:
        rid = str(uuid.uuid4())
        draft = {
            "extracted_rows": [
                {
                    "draft_row_id": rid, "code": "A 100", "title": "A", "units": "3",
                    "term_index": 1, "year_level": 1,
                    "raw_prerequisites": "", "raw_corequisites": "",
                    "prerequisite_codes": [], "corequisite_codes": [],
                    "source": {"page": 1, "table": 0, "row_index": 0},
                }
            ],
            "draft_analysis": analyze_draft_json({"extracted_rows": [{
                "draft_row_id": rid, "code": "A 100", "title": "A", "units": "3",
                "term_index": 1, "raw_prerequisites": "", "raw_corequisites": "",
                "prerequisite_codes": [], "corequisite_codes": [],
                "source": {"page": 1},
            }]}),
        }
        with self.assertRaises(ValidationError):
            draft_to_approved_json(
                draft, program_slug="t", curriculum_version_label="v1", source_pdf_sha256=None,
            )

    def test_builder_uses_staff_caps_with_overflow(self) -> None:
        rid = str(uuid.uuid4())
        caps = {
            str(y): {"1": 20 + y, "2": 22 + y, "3": 5 + y}
            for y in range(1, 7)
        }
        draft = {
            "extracted_rows": [
                {
                    "draft_row_id": rid, "code": "X 100", "title": "X", "units": "3",
                    "term_index": 1, "year_level": 4,
                    "raw_prerequisites": "", "raw_corequisites": "",
                    "prerequisite_codes": [], "corequisite_codes": [],
                    "source": {"page": 1, "table": 0, "row_index": 0},
                }
            ],
            "draft_analysis": analyze_draft_json({"extracted_rows": [{
                "draft_row_id": rid, "code": "X 100", "title": "X", "units": "3",
                "term_index": 1, "raw_prerequisites": "", "raw_corequisites": "",
                "prerequisite_codes": [], "corequisite_codes": [],
                "source": {"page": 1},
            }]}),
            "max_units_by_year_term": caps,
        }
        bundle = draft_to_approved_json(
            draft, program_slug="t", curriculum_version_label="v1", source_pdf_sha256=None,
        )
        validate_approved_bundle(bundle)
        idx = bundle["max_units_by_semester_index"]
        self.assertEqual(idx[0], 21)
        self.assertEqual(idx[1], 23)
        self.assertEqual(idx[2], 6)
        self.assertGreater(len(idx), 18)
        self.assertEqual(idx[18], caps["6"]["1"])


class StaffCurriculumAPITests(TestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.program = Program.objects.create(slug="tprog", name="Test Program")
        self.user = User.objects.create_user("curric_staff", password="pw", is_superuser=True)
        self.cv = CurriculumVersion.objects.create(
            program=self.program,
            status=CurriculumVersion.STATUS_DRAFT,
            version_label="api-test",
            draft_json={
                "phase": 5,
                "extracted_rows": [
                    {
                        "draft_row_id": str(uuid.uuid4()),
                        "code": "X 100",
                        "title": "Intro",
                        "units": "2",
                        "term_index": 1,
                        "year_level": 1,
                        "raw_prerequisites": "",
                        "raw_corequisites": "",
                        "prerequisite_codes": [],
                        "corequisite_codes": [],
                        "source": {"page": 1, "table": 0, "row_index": 0},
                    }
                ],
                "max_units_by_year_term": _default_caps(),
                "warnings": [],
                "extraction_meta": {},
                "suggested_graph": None,
            },
        )

    def test_patch_analyze_approve(self) -> None:
        url_draft = f"/staff/api/curriculum/version/{self.cv.pk}/draft/"
        req0 = self.factory.get(url_draft)
        req0.user = self.user
        r0 = staff_api_curriculum_draft(req0, pk=self.cv.pk)
        self.assertEqual(r0.status_code, 200)
        self.assertTrue(_json(r0).get("ok"))
        self.assertIn("draft_analysis", _json(r0))

        req1 = self.factory.post(f"/staff/api/curriculum/version/{self.cv.pk}/analyze/")
        req1.user = self.user
        r1 = staff_api_curriculum_analyze(req1, pk=self.cv.pk)
        self.assertEqual(r1.status_code, 200)
        self.assertTrue(_json(r1).get("ok"))

        body = _json(r0)
        rows = body["extracted_rows"]
        rows[0]["year_level"] = 2
        req2 = self.factory.patch(
            url_draft,
            data=json.dumps({"extracted_rows": rows}),
            content_type="application/json",
        )
        req2.user = self.user
        r2 = staff_api_curriculum_draft(req2, pk=self.cv.pk)
        self.assertEqual(r2.status_code, 200, r2.content)

        req3 = self.factory.post(f"/staff/api/curriculum/version/{self.cv.pk}/approve/")
        req3.user = self.user
        r3 = staff_api_curriculum_approve(req3, pk=self.cv.pk)
        self.assertEqual(r3.status_code, 200, r3.content)
        self.cv.refresh_from_db()
        self.assertEqual(self.cv.status, CurriculumVersion.STATUS_APPROVED)
        validate_approved_bundle(self.cv.approved_json)

    def test_duplicate_code_patch_rejected(self) -> None:
        rid = str(uuid.uuid4())
        rows = [
            {
                "draft_row_id": rid,
                "code": "Z 100",
                "title": "A",
                "units": "1",
                "term_index": 1,
                "year_level": 1,
                "raw_prerequisites": "",
                "raw_corequisites": "",
                "prerequisite_codes": [],
                "corequisite_codes": [],
                "source": {"page": 1},
            },
            {
                "draft_row_id": str(uuid.uuid4()),
                "code": "Z 100",
                "title": "B",
                "units": "1",
                "term_index": 1,
                "year_level": 1,
                "raw_prerequisites": "",
                "raw_corequisites": "",
                "prerequisite_codes": [],
                "corequisite_codes": [],
                "source": {"page": 1},
            },
        ]
        req = self.factory.patch(
            f"/staff/api/curriculum/version/{self.cv.pk}/draft/",
            data=json.dumps({"extracted_rows": rows}),
            content_type="application/json",
        )
        req.user = self.user
        r = staff_api_curriculum_draft(req, pk=self.cv.pk)
        self.assertEqual(r.status_code, 400)

    def test_approve_fails_without_caps(self) -> None:
        cv_no_caps = CurriculumVersion.objects.create(
            program=self.program,
            status=CurriculumVersion.STATUS_DRAFT,
            version_label="no-caps",
            draft_json={
                "extracted_rows": [
                    {
                        "draft_row_id": str(uuid.uuid4()),
                        "code": "A 100", "title": "A", "units": "2",
                        "term_index": 1, "year_level": 1,
                        "raw_prerequisites": "", "raw_corequisites": "",
                        "prerequisite_codes": [], "corequisite_codes": [],
                        "source": {"page": 1, "table": 0, "row_index": 0},
                    }
                ],
            },
        )
        req = self.factory.post(f"/staff/api/curriculum/version/{cv_no_caps.pk}/approve/")
        req.user = self.user
        r = staff_api_curriculum_approve(req, pk=cv_no_caps.pk)
        self.assertEqual(r.status_code, 400)
        self.assertIn("max_units_by_year_term", _json(r).get("error", ""))

    def test_save_draft_with_caps_roundtrip(self) -> None:
        caps = {str(y): {"1": 20, "2": 22, "3": 9} for y in range(1, 7)}
        req = self.factory.patch(
            f"/staff/api/curriculum/version/{self.cv.pk}/draft/",
            data=json.dumps({"max_units_by_year_term": caps}),
            content_type="application/json",
        )
        req.user = self.user
        r = staff_api_curriculum_draft(req, pk=self.cv.pk)
        self.assertEqual(r.status_code, 200)
        data = _json(r)
        self.assertEqual(data["max_units_by_year_term"]["1"]["1"], 20)
        self.assertEqual(data["max_units_by_year_term"]["3"]["3"], 9)


class UnitCapsModuleTests(TestCase):
    def test_validate_complete_map(self) -> None:
        m = validate_max_units_by_year_term(_default_caps())
        self.assertEqual(len(m), 6)
        for y in range(1, 7):
            self.assertEqual(len(m[y]), 3)

    def test_validate_rejects_missing_year(self) -> None:
        caps = _default_caps()
        del caps["3"]
        with self.assertRaises(ValidationError):
            validate_max_units_by_year_term(caps)

    def test_validate_rejects_missing_term(self) -> None:
        caps = _default_caps()
        del caps["2"]["2"]
        with self.assertRaises(ValidationError):
            validate_max_units_by_year_term(caps)

    def test_validate_rejects_negative(self) -> None:
        caps = _default_caps()
        caps["1"]["1"] = -5
        with self.assertRaises(ValidationError):
            validate_max_units_by_year_term(caps)

    def test_builder_first_18_from_map(self) -> None:
        m = {y: {t: y * 10 + t for t in range(1, 4)} for y in range(1, 7)}
        result = build_max_units_by_semester_index(m, max_orig=1)
        self.assertEqual(result[0], 11)
        self.assertEqual(result[1], 12)
        self.assertEqual(result[2], 13)
        self.assertEqual(result[3], 21)
        self.assertEqual(result[17], 63)

    def test_builder_overflow_uses_year6(self) -> None:
        m = {y: {1: 20, 2: 22, 3: 9} for y in range(1, 7)}
        m[6] = {1: 30, 2: 32, 3: 15}
        result = build_max_units_by_semester_index(m, max_orig=18, overflow_semesters=3)
        self.assertEqual(result[18], 30)
        self.assertEqual(result[19], 32)
        self.assertEqual(result[20], 15)
