"""Phase 6 curriculum: analyzer, builder, semester mapping, staff APIs."""

import json
import uuid

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase

from pos.admin import validate_approved_bundle
from pos.curriculum_draft_analyze import analyze_draft_json
from pos.curriculum_draft_semesters import term_index_to_orig_sem_map
from pos.curriculum_draft_to_approved import draft_to_approved_json
from pos.models import CurriculumVersion, Program
from pos.staff_curriculum_views import (
    staff_api_curriculum_analyze,
    staff_api_curriculum_approve,
    staff_api_curriculum_draft,
)


def _json(resp):
    return json.loads(resp.content.decode())


def _row(code: str, term_index: int, page: int, table: int = 0, row_index: int = 0) -> dict:
    return {
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
        }
        bundle = draft_to_approved_json(
            draft,
            program_slug="test_prog",
            curriculum_version_label="v1",
            source_pdf_sha256="a" * 64,
        )
        validate_approved_bundle(bundle)
        self.assertEqual(bundle["schema_version"], 1)
        self.assertEqual(bundle["courses"]["MATH 101"]["category"], "Rigid")
        self.assertEqual(bundle["max_units_by_semester_index"], [24])


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
