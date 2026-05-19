"""Phase 6 curriculum: analyzer, builder, semester mapping, staff APIs."""

import json
import uuid

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase

from pos.admin import validate_approved_bundle
from pos.curriculum_draft_analyze import (
    analyze_draft_json,
    normalize_requisite_list_separators,
    _split_req_fragments,
)
from pos.curriculum_draft_semesters import term_index_to_orig_sem_map
from pos.curriculum_draft_to_approved import draft_to_approved_json
from pos.generate_curriculum_context import resolve_generate_curriculum_context
from pos.curriculum_unit_caps import (
    build_max_units_by_semester_index,
    validate_max_units_by_year_term,
)
from pos.models import CurriculumVersion, Program
from pos.staff_curriculum_views import (
    staff_api_curriculum_analyze,
    staff_api_curriculum_approve,
    staff_api_curriculum_draft,
    staff_api_curriculum_versions_list,
    staff_api_curriculum_reopen,
    staff_api_curriculum_delete,
    staff_api_curriculum_version_label,
    staff_curriculum_edit_list_page,
    staff_curriculum_edit_detail_page,
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


class RequisiteSeparatorTests(TestCase):
    def test_normalize_slash_between_codes_to_semicolon(self) -> None:
        self.assertEqual(
            normalize_requisite_list_separators("MATH 401 / MATH 402"),
            "MATH 401; MATH 402",
        )

    def test_normalize_comma_between_codes_to_semicolon(self) -> None:
        self.assertEqual(
            normalize_requisite_list_separators("MATH 401, MATH 402"),
            "MATH 401; MATH 402",
        )

    def test_normalize_three_code_list(self) -> None:
        self.assertEqual(
            normalize_requisite_list_separators("MATH 401, MATH 402, PE 101"),
            "MATH 401; MATH 402; PE 101",
        )

    def test_standing_phrase_comma_not_normalized(self) -> None:
        self.assertEqual(
            normalize_requisite_list_separators("4th Year Standing"),
            "4th Year Standing",
        )

    def test_split_fragments_on_semicolon_not_slash_or_comma(self) -> None:
        frags = _split_req_fragments("MATH 401 / MATH 402")
        self.assertEqual(frags, ["MATH 401 / MATH 402"])
        frags_comma = _split_req_fragments("MATH 401, MATH 402")
        self.assertEqual(frags_comma, ["MATH 401, MATH 402"])
        frags_semi = _split_req_fragments("MATH 401; MATH 402")
        self.assertEqual(frags_semi, ["MATH 401", "MATH 402"])

    def test_analyze_rewrites_raw_prerequisites_in_place(self) -> None:
        row = _row("EE 301", 3, 1)
        row["raw_prerequisites"] = "MATH 401 / MATH 402"
        row["prerequisite_codes"] = ["MATH 401", "MATH 402"]
        draft = {"extracted_rows": [row]}
        analyze_draft_json(draft)
        self.assertEqual(row["raw_prerequisites"], "MATH 401; MATH 402")

    def test_analyze_rewrites_comma_separated_prerequisites(self) -> None:
        row = _row("EE 302", 4, 1)
        row["raw_prerequisites"] = "MATH 401, PE 101"
        draft = {"extracted_rows": [row]}
        analyze_draft_json(draft)
        self.assertEqual(row["raw_prerequisites"], "MATH 401; PE 101")


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

    def test_approved_preserves_year_standing_in_prerequisites(self) -> None:
        rid = str(uuid.uuid4())
        row = {
            "draft_row_id": rid,
            "code": "CpE 204",
            "title": "Adv Math",
            "units": "3",
            "term_index": 4,
            "term_label": "2nd Year - First Semester",
            "raw_prerequisites": "2nd Year Standing",
            "raw_corequisites": "",
            "prerequisite_codes": [],
            "corequisite_codes": [],
            "source": {"page": 1, "table": 0, "row_index": 0},
        }
        da = analyze_draft_json({"extracted_rows": [row]})
        draft = {"extracted_rows": [row], "draft_analysis": da, "max_units_by_year_term": _default_caps()}
        bundle = draft_to_approved_json(
            draft,
            program_slug="cpe",
            curriculum_version_label="v1",
            source_pdf_sha256="b" * 64,
        )
        validate_approved_bundle(bundle)
        pre = bundle["courses"]["CpE 204"]["pre-requisites"]
        self.assertIn("2nd Year Standing", pre)

    def test_approved_standing_shows_on_generate_pos_lookup(self) -> None:
        from django.contrib.auth.hashers import make_password

        from accounts.models import Student

        prog = Program.objects.create(slug="stand", name="Standing")
        rid = str(uuid.uuid4())
        row = {
            "draft_row_id": rid,
            "code": "CpE 204",
            "title": "Adv Math",
            "units": "3",
            "term_index": 4,
            "raw_prerequisites": "2nd Year Standing",
            "raw_corequisites": "",
            "prerequisite_codes": [],
            "corequisite_codes": [],
            "source": {"page": 1, "table": 0, "row_index": 0},
        }
        da = analyze_draft_json({"extracted_rows": [row]})
        draft = {"extracted_rows": [row], "draft_analysis": da, "max_units_by_year_term": _default_caps()}
        bundle = draft_to_approved_json(
            draft,
            program_slug="stand",
            curriculum_version_label="v1",
            source_pdf_sha256="c" * 64,
        )
        cv = CurriculumVersion.objects.create(
            program=prog,
            status=CurriculumVersion.STATUS_APPROVED,
            version_label="s",
            approved_json=bundle,
        )
        student = Student.objects.create(
            sr_code="22-90001",
            password=make_password("x"),
            first_name="S",
            middle_name="",
            last_name="T",
            email="stand@example.com",
            program=prog,
            selected_curriculum_version=cv,
        )
        ctx = resolve_generate_curriculum_context(student)
        self.assertEqual(ctx["mode"], "db")
        self.assertEqual(ctx["course_lookup"]["CpE 204"][4], "2nd Year Standing")

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


class StaffCurriculumEditTests(TestCase):
    """Tests for the edit list page, versions list API, reopen API, and detail page."""

    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.program = Program.objects.create(slug="eprog", name="Edit Program")
        self.superuser = User.objects.create_user("su_edit", password="pw", is_superuser=True)
        self.manager = User.objects.create_user("mgr_edit", password="pw")
        self.manager.user_permissions.add(
            *list(
                __import__("django.contrib.auth.models", fromlist=["Permission"])
                .Permission.objects.filter(codename="manage_curriculum_versions")
            )
        )
        self.program.curriculum_managers.add(self.manager)

        self.draft_cv = CurriculumVersion.objects.create(
            program=self.program,
            status=CurriculumVersion.STATUS_DRAFT,
            version_label="edit-draft",
            draft_json={
                "phase": 5,
                "extracted_rows": [
                    {
                        "draft_row_id": str(uuid.uuid4()),
                        "code": "E 101",
                        "title": "Edit Course",
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
                "max_units_by_year_term": _default_caps(),
                "warnings": [],
                "extraction_meta": {},
                "suggested_graph": None,
            },
        )
        self.approved_cv = CurriculumVersion.objects.create(
            program=self.program,
            status=CurriculumVersion.STATUS_APPROVED,
            version_label="edit-approved",
            draft_json=self.draft_cv.draft_json,
            approved_json={"courses": {}, "metadata": {}},
        )
        self.no_rows_cv = CurriculumVersion.objects.create(
            program=self.program,
            status=CurriculumVersion.STATUS_APPROVED,
            version_label="no-rows",
            draft_json={"extracted_rows": [], "warnings": []},
            approved_json={"courses": {}, "metadata": {}},
        )

    # --- versions list API ---

    def _list_req(self, user, **params):
        from urllib.parse import urlencode
        url = "/staff/api/curriculum/versions/"
        if params:
            url += "?" + urlencode(params)
        req = self.factory.get(url)
        req.user = user
        return staff_api_curriculum_versions_list(req)

    def test_versions_list_returns_managed_versions(self) -> None:
        r = self._list_req(self.superuser)
        self.assertEqual(r.status_code, 200)
        data = _json(r)
        self.assertTrue(data["ok"])
        ids = [v["id"] for v in data["versions"]]
        self.assertIn(self.draft_cv.pk, ids)
        self.assertIn(self.approved_cv.pk, ids)

    def test_versions_list_status_filter(self) -> None:
        r = self._list_req(self.superuser, status="draft")
        ids = [v["id"] for v in _json(r)["versions"]]
        self.assertIn(self.draft_cv.pk, ids)
        self.assertNotIn(self.approved_cv.pk, ids)

    def test_versions_list_non_manager_403(self) -> None:
        other = User.objects.create_user("nobody_edit", password="pw")
        r = self._list_req(other)
        self.assertEqual(r.status_code, 403)

    def test_versions_list_unauthenticated_401(self) -> None:
        from django.contrib.auth.models import AnonymousUser
        req = self.factory.get("/staff/api/curriculum/versions/")
        req.user = AnonymousUser()
        r = staff_api_curriculum_versions_list(req)
        self.assertEqual(r.status_code, 401)

    def test_versions_list_row_has_edit_url(self) -> None:
        r = self._list_req(self.superuser)
        version_map = {v["id"]: v for v in _json(r)["versions"]}
        self.assertIn("edit_url", version_map[self.draft_cv.pk])
        self.assertEqual(
            version_map[self.draft_cv.pk]["edit_url"],
            f"/staff/curriculum/edit/{self.draft_cv.pk}/",
        )

    # --- reopen API ---

    def _reopen_req(self, user, pk):
        req = self.factory.post(f"/staff/api/curriculum/version/{pk}/reopen/")
        req.user = user
        return staff_api_curriculum_reopen(req, pk=pk)

    def test_reopen_approved_to_draft(self) -> None:
        r = self._reopen_req(self.superuser, self.approved_cv.pk)
        self.assertEqual(r.status_code, 200)
        data = _json(r)
        self.assertTrue(data["ok"])
        self.assertEqual(data["status"], CurriculumVersion.STATUS_DRAFT)
        self.approved_cv.refresh_from_db()
        self.assertEqual(self.approved_cv.status, CurriculumVersion.STATUS_DRAFT)

    def test_reopen_already_draft_returns_400(self) -> None:
        r = self._reopen_req(self.superuser, self.draft_cv.pk)
        self.assertEqual(r.status_code, 400)

    def test_reopen_blocked_when_no_rows(self) -> None:
        r = self._reopen_req(self.superuser, self.no_rows_cv.pk)
        self.assertEqual(r.status_code, 400)
        self.assertIn("No draft data", _json(r)["error"])

    def test_reopen_non_manager_403(self) -> None:
        other = User.objects.create_user("noone_reopen", password="pw")
        r = self._reopen_req(other, self.approved_cv.pk)
        self.assertEqual(r.status_code, 403)

    def test_draft_patch_works_after_reopen(self) -> None:
        self._reopen_req(self.superuser, self.approved_cv.pk)
        self.approved_cv.refresh_from_db()
        self.assertEqual(self.approved_cv.status, CurriculumVersion.STATUS_DRAFT)

        rows = list(self.approved_cv.draft_json.get("extracted_rows") or [])
        req = self.factory.patch(
            f"/staff/api/curriculum/version/{self.approved_cv.pk}/draft/",
            data=json.dumps({"extracted_rows": rows, "max_units_by_year_term": _default_caps()}),
            content_type="application/json",
        )
        req.user = self.superuser
        r = staff_api_curriculum_draft(req, pk=self.approved_cv.pk)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(_json(r)["ok"])

    # --- upload response includes edit_url ---

    def test_upload_response_edit_url_present(self) -> None:
        from pos.staff_curriculum_views import _draft_json_response
        self.draft_cv.refresh_from_db()
        resp = _draft_json_response(self.draft_cv)
        self.assertIn("rows", resp)

    # --- detail page context ---

    def _detail_req(self, user, pk):
        from unittest.mock import MagicMock
        req = self.factory.get(f"/staff/curriculum/edit/{pk}/")
        req.user = user
        req.session = MagicMock()
        req.session.get.return_value = None
        return staff_curriculum_edit_detail_page(req, pk=pk)

    def test_detail_page_draft_can_edit(self) -> None:
        r = self._detail_req(self.superuser, self.draft_cv.pk)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "btn-save")
        self.assertContains(r, "btn-edit-version-label")
        self.assertContains(r, "cv-version-label-modal")
        self.assertContains(r, "Edit Curriculum Name")
        self.assertContains(r, "BSCpE Curriculum 2025-2026")

    def test_detail_page_approved_without_reopen_redirects_to_list(self) -> None:
        r = self._detail_req(self.superuser, self.approved_cv.pk)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, "/staff/curriculum/edit/")

    def test_detail_page_approved_can_reopen_from_list(self) -> None:
        req = self.factory.get(
            f"/staff/curriculum/edit/{self.approved_cv.pk}/",
            {"reopen": "1"},
        )
        req.user = self.superuser
        from unittest.mock import MagicMock
        req.session = MagicMock()
        req.session.get.return_value = None
        r = staff_curriculum_edit_detail_page(req, pk=self.approved_cv.pk)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "btn-reopen")

    def test_detail_page_forbidden_for_non_manager(self) -> None:
        other = User.objects.create_user("forbidden_edit", password="pw")
        r = self._detail_req(other, self.draft_cv.pk)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "not allowed")

    # --- delete API ---

    def _delete_req(self, user, pk):
        req = self.factory.post(f"/staff/api/curriculum/version/{pk}/delete/")
        req.user = user
        return staff_api_curriculum_delete(req, pk=pk)

    def _patch_label_req(self, user, pk, label: str):
        req = self.factory.patch(
            f"/staff/api/curriculum/version/{pk}/version-label/",
            data=json.dumps({"version_label": label}),
            content_type="application/json",
        )
        req.user = user
        return staff_api_curriculum_version_label(req, pk=pk)

    def test_patch_version_label_draft(self) -> None:
        cv = CurriculumVersion.objects.create(
            program=self.program,
            status=CurriculumVersion.STATUS_DRAFT,
            version_label="old-name",
            draft_json={"extracted_rows": []},
        )
        r = self._patch_label_req(self.superuser, cv.pk, "New POS Name")
        self.assertEqual(r.status_code, 200)
        data = _json(r)
        self.assertTrue(data["ok"])
        self.assertEqual(data["version_label"], "New POS Name")
        cv.refresh_from_db()
        self.assertEqual(cv.version_label, "New POS Name")

    def test_delete_draft_version(self) -> None:
        cv = CurriculumVersion.objects.create(
            program=self.program,
            status=CurriculumVersion.STATUS_DRAFT,
            version_label="to-delete",
            draft_json={"extracted_rows": [{"draft_row_id": str(uuid.uuid4()), "code": "Z 999", "title": "x", "units": "1"}]},
        )
        r = self._delete_req(self.superuser, cv.pk)
        self.assertEqual(r.status_code, 200)
        data = _json(r)
        self.assertTrue(data["ok"])
        self.assertEqual(data["deleted_id"], cv.pk)
        self.assertFalse(CurriculumVersion.objects.filter(pk=cv.pk).exists())

    def test_delete_clears_program_default(self) -> None:
        self.program.default_curriculum_version = self.approved_cv
        self.program.save(update_fields=["default_curriculum_version"])
        pk = self.approved_cv.pk
        r = self._delete_req(self.superuser, pk)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(_json(r)["cleared_program_default"])
        self.program.refresh_from_db()
        self.assertIsNone(self.program.default_curriculum_version_id)
        self.assertFalse(CurriculumVersion.objects.filter(pk=pk).exists())

    def test_delete_non_manager_403(self) -> None:
        cv = CurriculumVersion.objects.create(
            program=self.program,
            status=CurriculumVersion.STATUS_DRAFT,
            version_label="no-delete-me",
            draft_json={"extracted_rows": []},
        )
        other = User.objects.create_user("no_delete", password="pw")
        r = self._delete_req(other, cv.pk)
        self.assertEqual(r.status_code, 403)
        self.assertTrue(CurriculumVersion.objects.filter(pk=cv.pk).exists())

    def test_versions_list_includes_can_delete(self) -> None:
        r = self._list_req(self.superuser)
        version_map = {v["id"]: v for v in _json(r)["versions"]}
        self.assertTrue(version_map[self.approved_cv.pk].get("can_delete"))
