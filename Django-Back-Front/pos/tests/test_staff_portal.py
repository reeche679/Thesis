"""
Tests for the staff portal home page and student assignment APIs:
  GET  /staff/api/students/
  PATCH /staff/api/students/<pk>/
  GET  /staff/api/programs/
  GET  /staff/        (page)
"""

import json
from unittest.mock import MagicMock

from django.contrib.auth.models import Permission, User
from django.test import RequestFactory, TestCase

from accounts.models import Student
from pos.models import CurriculumVersion, Program
from pos.staff_portal_views import (
    staff_api_program_delete,
    staff_api_program_patch,
    staff_api_programs_list,
    staff_api_student_patch,
    staff_api_students_list,
    staff_home_page,
)


def _json(resp):
    return json.loads(resp.content.decode())


def _make_approved_cv(program: Program, label: str = "v1") -> CurriculumVersion:
    import uuid
    rows = [{"code": "TEST 101", "title": "T", "units": "3", "draft_row_id": str(uuid.uuid4())}]
    cv = CurriculumVersion.objects.create(
        program=program,
        version_label=label,
        status=CurriculumVersion.STATUS_DRAFT,
        draft_json={"extracted_rows": rows, "max_units_by_year_term": {str(y): {"1": 24, "2": 24, "3": 24} for y in range(1, 7)}},
    )
    cv.status = CurriculumVersion.STATUS_APPROVED
    cv.approved_json = {"semesters": [], "max_units_by_semester_index": {}}
    cv.save()
    return cv


class StaffPortalSetupMixin:
    """Creates a superuser, a manager, a plain user, two programs, students."""

    def setUp(self):
        self.factory = RequestFactory()

        self.superuser = User.objects.create_superuser("portal_su", password="pw")
        self.manager = User.objects.create_user("portal_mgr", password="pw")
        perm = Permission.objects.get(codename="manage_curriculum_versions")
        self.manager.user_permissions.add(perm)
        self.manager = User.objects.get(pk=self.manager.pk)  # refresh perm cache
        self.plain_user = User.objects.create_user("portal_plain", password="pw")

        self.prog_ee = Program.objects.create(name="Electrical Engineering", slug="ee")
        self.prog_cpe = Program.objects.create(name="Computer Engineering", slug="cpe")

        self.cv_ee = _make_approved_cv(self.prog_ee, "ee-v1")
        self.cv_cpe = _make_approved_cv(self.prog_cpe, "cpe-v1")

        self.student_ee = Student.objects.create(
            sr_code="23-10001",
            password="x",
            first_name="Alice",
            middle_name="A",
            last_name="Reyes",
            email="alice@test.ph",
            program=self.prog_ee,
            selected_curriculum_version=self.cv_ee,
        )
        self.student_none = Student.objects.create(
            sr_code="23-10002",
            password="x",
            first_name="Bob",
            middle_name="",
            last_name="Santos",
            email="bob@test.ph",
        )


class StudentListAPITests(StaffPortalSetupMixin, TestCase):

    def _get(self, user, params=""):
        req = self.factory.get("/staff/api/students/" + (("?" + params) if params else ""))
        req.user = user
        return staff_api_students_list(req)

    def test_superuser_sees_all_students(self):
        r = self._get(self.superuser)
        self.assertEqual(r.status_code, 200)
        data = _json(r)
        self.assertTrue(data["ok"])
        ids = {s["sr_code"] for s in data["students"]}
        self.assertIn("23-10001", ids)
        self.assertIn("23-10002", ids)

    def test_manager_sees_all_students(self):
        """Managers have same visibility as superuser per spec."""
        r = self._get(self.manager)
        self.assertEqual(r.status_code, 200)
        data = _json(r)
        self.assertTrue(data["ok"])
        self.assertGreaterEqual(len(data["students"]), 2)

    def test_non_portal_user_403(self):
        r = self._get(self.plain_user)
        self.assertEqual(r.status_code, 403)

    def test_unauthenticated_401(self):
        from django.contrib.auth.models import AnonymousUser
        req = self.factory.get("/staff/api/students/")
        req.user = AnonymousUser()
        r = staff_api_students_list(req)
        self.assertEqual(r.status_code, 401)

    def test_search_by_sr_code(self):
        r = self._get(self.superuser, "q=23-10001")
        data = _json(r)
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["students"]), 1)
        self.assertEqual(data["students"][0]["sr_code"], "23-10001")

    def test_search_by_name(self):
        r = self._get(self.superuser, "q=reyes")
        data = _json(r)
        self.assertTrue(data["ok"])
        codes = [s["sr_code"] for s in data["students"]]
        self.assertIn("23-10001", codes)
        self.assertNotIn("23-10002", codes)

    def test_filter_by_program_id(self):
        r = self._get(self.superuser, f"program_id={self.prog_ee.pk}")
        data = _json(r)
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["students"]), 1)
        self.assertEqual(data["students"][0]["sr_code"], "23-10001")

    def test_response_includes_programs_list(self):
        r = self._get(self.superuser)
        data = _json(r)
        slugs = {p["slug"] for p in data["programs"]}
        self.assertIn("ee", slugs)
        self.assertIn("cpe", slugs)

    def test_student_row_shape(self):
        r = self._get(self.superuser, "q=23-10001")
        data = _json(r)
        row = data["students"][0]
        self.assertIn("id", row)
        self.assertIn("sr_code", row)
        self.assertIn("name", row)
        self.assertIn("program_id", row)
        self.assertIn("curriculum_version_id", row)
        self.assertEqual(row["program_id"], self.prog_ee.pk)
        self.assertEqual(row["curriculum_version_id"], self.cv_ee.pk)


class StudentPatchAPITests(StaffPortalSetupMixin, TestCase):

    def _patch(self, user, pk, payload: dict):
        req = self.factory.patch(
            f"/staff/api/students/{pk}/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        req.user = user
        return staff_api_student_patch(req, pk=pk)

    def test_assign_program_and_version(self):
        r = self._patch(self.superuser, self.student_none.pk, {
            "program_id": self.prog_cpe.pk,
            "curriculum_version_id": self.cv_cpe.pk,
        })
        self.assertEqual(r.status_code, 200)
        data = _json(r)
        self.assertTrue(data["ok"])
        self.assertEqual(data["student"]["program_id"], self.prog_cpe.pk)
        self.assertEqual(data["student"]["curriculum_version_id"], self.cv_cpe.pk)
        self.student_none.refresh_from_db()
        self.assertEqual(self.student_none.program_id, self.prog_cpe.pk)
        self.assertEqual(self.student_none.selected_curriculum_version_id, self.cv_cpe.pk)

    def test_clear_program_clears_version_too(self):
        r = self._patch(self.superuser, self.student_ee.pk, {"program_id": None})
        self.assertEqual(r.status_code, 200)
        self.student_ee.refresh_from_db()
        self.assertIsNone(self.student_ee.program_id)
        self.assertIsNone(self.student_ee.selected_curriculum_version_id)

    def test_version_wrong_program_rejected(self):
        r = self._patch(self.superuser, self.student_ee.pk, {
            "curriculum_version_id": self.cv_cpe.pk,
        })
        self.assertEqual(r.status_code, 400)
        data = _json(r)
        self.assertFalse(data["ok"])
        self.assertIn("program", data["error"])

    def test_non_approved_version_rejected(self):
        import uuid
        draft_cv = CurriculumVersion.objects.create(
            program=self.prog_ee,
            version_label="draft-test",
            status=CurriculumVersion.STATUS_DRAFT,
            draft_json={"extracted_rows": [], "max_units_by_year_term": {}},
        )
        r = self._patch(self.superuser, self.student_ee.pk, {"curriculum_version_id": draft_cv.pk})
        self.assertEqual(r.status_code, 400)
        data = _json(r)
        self.assertFalse(data["ok"])
        self.assertIn("approved", data["error"])

    def test_manager_can_patch(self):
        r = self._patch(self.manager, self.student_none.pk, {
            "program_id": self.prog_ee.pk,
            "curriculum_version_id": self.cv_ee.pk,
        })
        self.assertEqual(r.status_code, 200)
        self.assertTrue(_json(r)["ok"])

    def test_non_portal_user_403(self):
        r = self._patch(self.plain_user, self.student_none.pk, {"program_id": self.prog_ee.pk})
        self.assertEqual(r.status_code, 403)

    def test_student_not_found_404(self):
        r = self._patch(self.superuser, 999999, {"program_id": self.prog_ee.pk})
        self.assertEqual(r.status_code, 404)

    def test_change_program_clears_mismatched_version(self):
        """Switching program should auto-clear a version from the old program."""
        r = self._patch(self.superuser, self.student_ee.pk, {"program_id": self.prog_cpe.pk})
        self.assertEqual(r.status_code, 200)
        self.student_ee.refresh_from_db()
        self.assertEqual(self.student_ee.program_id, self.prog_cpe.pk)
        self.assertIsNone(self.student_ee.selected_curriculum_version_id)


class ProgramsAPITests(StaffPortalSetupMixin, TestCase):

    def _get(self, user):
        req = self.factory.get("/staff/api/programs/")
        req.user = user
        return staff_api_programs_list(req)

    def test_returns_programs(self):
        r = self._get(self.superuser)
        self.assertEqual(r.status_code, 200)
        data = _json(r)
        self.assertTrue(data["ok"])
        slugs = {p["slug"] for p in data["programs"]}
        self.assertIn("ee", slugs)

    def test_non_portal_403(self):
        r = self._get(self.plain_user)
        self.assertEqual(r.status_code, 403)

    def test_create_program(self):
        req = self.factory.post(
            "/staff/api/programs/",
            data=json.dumps({"name": "Mechanical Engineering", "slug": "mech"}),
            content_type="application/json",
        )
        req.user = self.superuser
        r = staff_api_programs_list(req)
        self.assertEqual(r.status_code, 201)
        data = _json(r)
        self.assertTrue(data["ok"])
        self.assertEqual(data["program"]["slug"], "mech")
        self.assertTrue(Program.objects.filter(slug="mech").exists())

    def test_create_program_slug_with_underscore(self):
        req = self.factory.post(
            "/staff/api/programs/",
            data=json.dumps({"name": "BSEE Batch", "slug": "bsee_2025"}),
            content_type="application/json",
        )
        req.user = self.superuser
        r = staff_api_programs_list(req)
        self.assertEqual(r.status_code, 201)
        data = _json(r)
        self.assertEqual(data["program"]["slug"], "bsee_2025")

    def test_create_program_duplicate_slug(self):
        req = self.factory.post(
            "/staff/api/programs/",
            data=json.dumps({"name": "Dup EE", "slug": "ee"}),
            content_type="application/json",
        )
        req.user = self.superuser
        r = staff_api_programs_list(req)
        self.assertEqual(r.status_code, 400)

    def test_patch_program_name(self):
        prog = Program.objects.create(name="Old Name", slug="patch-test")
        req = self.factory.patch(
            f"/staff/api/programs/{prog.pk}/",
            data=json.dumps({"name": "New Name"}),
            content_type="application/json",
        )
        req.user = self.superuser
        r = staff_api_program_patch(req, pk=prog.pk)
        self.assertEqual(r.status_code, 200)
        data = _json(r)
        self.assertTrue(data["ok"])
        self.assertEqual(data["program"]["name"], "New Name")
        prog.refresh_from_db()
        self.assertEqual(prog.name, "New Name")
        self.assertEqual(prog.slug, "patch-test")

    def test_patch_program_empty_name_rejected(self):
        req = self.factory.patch(
            f"/staff/api/programs/{self.prog_ee.pk}/",
            data=json.dumps({"name": "   "}),
            content_type="application/json",
        )
        req.user = self.superuser
        r = staff_api_program_patch(req, pk=self.prog_ee.pk)
        self.assertEqual(r.status_code, 400)

    def test_delete_program(self):
        prog = Program.objects.create(name="Temp Prog", slug="temp-prog")
        req = self.factory.post(f"/staff/api/programs/{prog.pk}/delete/")
        req.user = self.superuser
        r = staff_api_program_delete(req, pk=prog.pk)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Program.objects.filter(pk=prog.pk).exists())

    def test_delete_program_with_students_unassigns(self):
        prog_pk = self.prog_ee.pk
        student_pk = self.student_ee.pk
        req = self.factory.post(f"/staff/api/programs/{prog_pk}/delete/")
        req.user = self.superuser
        r = staff_api_program_delete(req, pk=prog_pk)
        self.assertEqual(r.status_code, 200)
        data = _json(r)
        self.assertTrue(data["ok"])
        self.assertGreaterEqual(data["unassigned_students"], 1)
        self.assertFalse(Program.objects.filter(pk=prog_pk).exists())
        self.student_ee.refresh_from_db()
        self.assertIsNone(self.student_ee.program_id)
        self.assertIsNone(self.student_ee.selected_curriculum_version_id)


class StaffHomePageTests(StaffPortalSetupMixin, TestCase):

    def _get(self, user):
        req = self.factory.get("/staff/")
        req.user = user
        req.session = MagicMock()
        req.session.get = MagicMock(return_value=None)
        return staff_home_page(req)

    def test_authenticated_manager_gets_redirect_or_200(self):
        """staff_home_page should return 200 for authorized user."""
        r = self._get(self.manager)
        self.assertIn(r.status_code, [200, 302])

    def test_non_portal_user_redirects_to_login(self):
        r = self._get(self.plain_user)
        self.assertEqual(r.status_code, 302)
        self.assertIn("login", r["Location"])
