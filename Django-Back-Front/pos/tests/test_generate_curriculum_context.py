"""Tests for Generate POS DB-backed curriculum resolution."""

from django.contrib.auth.hashers import make_password
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase

from accounts.models import Student
from pos.generate_curriculum_context import resolve_generate_curriculum_context
from pos.models import CurriculumVersion, Program
from pos.solver_bridge import get_solver_metadata
from pos.views import (
    _clamp_solver_current_sem_to_horizon,
    _curriculum_version_id_for_student,
    _units_for_policy_grade,
)


def _request_with_student_id(student_pk: int):
    rf = RequestFactory()
    req = rf.get("/")
    SessionMiddleware(lambda r: None).process_request(req)
    req.session["student_id"] = student_pk
    req.session.save()
    return req


class ResolveGenerateCurriculumContextTests(TestCase):
    def test_static_when_no_program(self) -> None:
        s = Student.objects.create(
            sr_code="22-00001",
            password=make_password("x"),
            first_name="A",
            middle_name="",
            last_name="B",
            email="a@example.com",
        )
        ctx = resolve_generate_curriculum_context(s)
        self.assertEqual(ctx["mode"], "static")
        self.assertIsNone(ctx.get("curriculum_version_id"))
        self.assertTrue(len(ctx["curriculum_sections"]) > 0)

    def test_get_solver_metadata_db_max_semester_matches_caps(self) -> None:
        prog = Program.objects.create(slug="tmeta", name="Meta")
        approved = {
            "schema_version": 1,
            "metadata": {"program_slug": "tmeta", "curriculum_version_label": "M", "source_pdf_sha256": None},
            "max_units_by_semester_index": [24, 24, 21],
            "courses": {
                "Z 1": {
                    "course_title": "One",
                    "units": 1,
                    "orig_sem": 1,
                    "category": "Rigid",
                    "year_level": 1,
                    "pre-requisites": [],
                    "co-requisites": [],
                },
            },
        }
        cv = CurriculumVersion.objects.create(
            program=prog,
            status=CurriculumVersion.STATUS_APPROVED,
            version_label="meta",
            approved_json=approved,
        )
        meta = get_solver_metadata(cv.pk)
        self.assertEqual(meta.get("max_semester"), 3)

    def test_get_solver_metadata_static_has_max_semester(self) -> None:
        meta = get_solver_metadata(None)
        self.assertGreater(int(meta.get("max_semester") or 0), 0)

    def test_clamp_current_sem_to_db_horizon(self) -> None:
        prog = Program.objects.create(slug="tclamp", name="Clamp")
        s = Student.objects.create(
            sr_code="22-00004",
            password=make_password("x"),
            first_name="C",
            middle_name="",
            last_name="Lamp",
            email="clamp@example.com",
            program=prog,
        )
        cv = CurriculumVersion.objects.create(
            program=prog,
            status=CurriculumVersion.STATUS_APPROVED,
            version_label="c",
            approved_json={
                "schema_version": 1,
                "metadata": {"program_slug": "tclamp", "curriculum_version_label": "C", "source_pdf_sha256": None},
                "max_units_by_semester_index": [24, 24],
                "courses": {
                    "A 1": {
                        "course_title": "A",
                        "units": 1,
                        "orig_sem": 1,
                        "category": "Rigid",
                        "year_level": 1,
                        "pre-requisites": [],
                        "co-requisites": [],
                    },
                },
            },
        )
        s.selected_curriculum_version = cv
        s.save(update_fields=["selected_curriculum_version"])
        req = _request_with_student_id(s.pk)
        self.assertEqual(_clamp_solver_current_sem_to_horizon(7, req), 2)
        self.assertEqual(_clamp_solver_current_sem_to_horizon(2, req), 2)
        self.assertEqual(_clamp_solver_current_sem_to_horizon(1, req), 1)

    def test_validate_pos_mixed_float_int_units_no_typeerror(self) -> None:
        """OR-Tools rejects float coefficients in unit-sum constraints (DB JSON uses 3.0)."""
        from pos.solver_bridge import validate_pos_via_solver

        bundle = {
            "schema_version": 1,
            "metadata": {"program_slug": "x", "curriculum_version_label": "t", "source_pdf_sha256": None},
            "max_units_by_semester_index": [24, 24, 24, 24],
            "courses": {
                "ENGG 102": {
                    "course_title": "E",
                    "units": 3.0,
                    "orig_sem": 1,
                    "category": "Rigid",
                    "year_level": 1,
                    "pre-requisites": [],
                    "co-requisites": [],
                },
                "MATH 101": {
                    "course_title": "M",
                    "units": 3,
                    "orig_sem": 1,
                    "category": "Rigid",
                    "year_level": 1,
                    "pre-requisites": [],
                    "co-requisites": [],
                },
                "OTHER": {
                    "course_title": "O",
                    "units": 4.0,
                    "orig_sem": 2,
                    "category": "Rigid",
                    "year_level": 1,
                    "pre-requisites": [],
                    "co-requisites": [],
                },
            },
        }
        res = validate_pos_via_solver(
            {
                "current_sem": 2,
                "passed_courses": [],
                "failed_courses": ["ENGG 102"],
                "with_petition": False,
                "curriculum_bundle": bundle,
            },
            with_petition=False,
            forced_semester_map=None,
        )
        self.assertIn(res.get("status"), ("ok", "infeasible", "probation"))

    def test_db_maps_orig_sem_and_matches_student_version_id(self) -> None:
        prog = Program.objects.create(slug="tgen", name="Test Gen")
        s = Student.objects.create(
            sr_code="22-00002",
            password=make_password("x"),
            first_name="T",
            middle_name="",
            last_name="Two",
            email="two@example.com",
            program=prog,
        )
        approved = {
            "schema_version": 1,
            "metadata": {"program_slug": "tgen", "curriculum_version_label": "EE v1", "source_pdf_sha256": None},
            "max_units_by_semester_index": [24, 24],
            "courses": {
                "EE 101": {
                    "course_title": "Circuits",
                    "units": 3,
                    "orig_sem": 1,
                    "category": "Rigid",
                    "year_level": 1,
                    "pre-requisites": [],
                    "co-requisites": [],
                },
                "EE 102": {
                    "course_title": "Fields",
                    "units": 3,
                    "orig_sem": 2,
                    "category": "Rigid",
                    "year_level": 1,
                    "pre-requisites": [],
                    "co-requisites": [],
                },
            },
        }
        cv = CurriculumVersion.objects.create(
            program=prog,
            status=CurriculumVersion.STATUS_APPROVED,
            version_label="ee",
            approved_json=approved,
        )
        s.selected_curriculum_version = cv
        s.save(update_fields=["selected_curriculum_version"])

        ctx = resolve_generate_curriculum_context(s)
        self.assertEqual(ctx["mode"], "db")
        self.assertEqual(ctx["curriculum_version_id"], cv.pk)
        self.assertEqual(_curriculum_version_id_for_student(s), cv.pk)
        self.assertEqual(len(ctx["curriculum_sections"]), 2)
        self.assertEqual(ctx["curriculum_sections"][0]["label"], "1st Year - First Semester")
        self.assertEqual(ctx["curriculum_sections"][1]["label"], "1st Year - Second Semester")
        self.assertEqual(len(ctx["curriculum_sections"][0]["courses"]), 1)
        self.assertEqual(ctx["curriculum_sections"][0]["courses"][0]["code"], "EE 101")
        self.assertIn("EE v1", ctx["display_title"])

    def test_static_when_orig_sem_missing(self) -> None:
        prog = Program.objects.create(slug="tbad", name="Bad")
        s = Student.objects.create(
            sr_code="22-00003",
            password=make_password("x"),
            first_name="T",
            middle_name="",
            last_name="Three",
            email="three@example.com",
            program=prog,
        )
        cv = CurriculumVersion.objects.create(
            program=prog,
            status=CurriculumVersion.STATUS_APPROVED,
            version_label="bad",
            approved_json={
                "schema_version": 1,
                "metadata": {"program_slug": "tbad", "curriculum_version_label": "x", "source_pdf_sha256": None},
                "max_units_by_semester_index": [24],
                "courses": {
                    "X 100": {
                        "course_title": "No sem",
                        "units": 1,
                        "category": "Rigid",
                        "year_level": 1,
                        "pre-requisites": [],
                        "co-requisites": [],
                    },
                },
            },
        )
        s.selected_curriculum_version = cv
        s.save(update_fields=["selected_curriculum_version"])
        ctx = resolve_generate_curriculum_context(s)
        self.assertEqual(ctx["mode"], "static")

    def test_units_for_policy_grade_db_fallback_without_lookup(self) -> None:
        """Fail-ratio must use section row units when flat lookup misses the grade key."""
        gctx = {
            "mode": "db",
            "curriculum_sections": [
                {
                    "year": "1st Year",
                    "semester": "First Semester",
                    "label": "1st Year - First Semester",
                    "courses": [
                        {
                            "code": "EE 101",
                            "title": "Circuits",
                            "units": 3,
                            "lec": 0,
                            "lab": 0,
                            "prereq": "",
                            "coreq": "",
                        }
                    ],
                }
            ],
        }
        self.assertEqual(_units_for_policy_grade("EE 101", 0, gctx, {}), 3.0)

    def test_units_for_policy_grade_static_fallback_without_lookup(self) -> None:
        gctx = {"mode": "static", "curriculum_sections": []}
        self.assertEqual(_units_for_policy_grade("MATH 101", 0, gctx, {}), 4.0)

    def test_units_for_policy_grade_db_does_not_use_static_curriculum(self) -> None:
        """DB mode must not read units from legacy POS_CURRICULUM when the row is absent."""
        gctx = {
            "mode": "db",
            "curriculum_sections": [
                {
                    "year": "1st Year",
                    "semester": "First Semester",
                    "label": "1st Year - First Semester",
                    "courses": [{"code": "EE 101", "title": "Circuits", "units": 3}],
                }
            ],
        }
        # MATH 101 exists in static POS_CURRICULUM[0] but not in this DB section.
        self.assertEqual(_units_for_policy_grade("MATH 101", 0, gctx, {}), 0.0)

    def test_validate_pos_full_static_curriculum_math101_fail_y1s1_feasible(self) -> None:
        """Regression: MATH 101 (Bottleneck) failed after Y1S1 must not make CP-SAT infeasible."""
        import sys
        from pathlib import Path

        from pos.solver_bridge import validate_pos_via_solver

        thesis_root = Path(__file__).resolve().parents[3]
        solver_root = thesis_root / "Solver-Side"
        sr = str(solver_root)
        if sr not in sys.path:
            sys.path.insert(0, sr)
        from tools.export_static_curriculum import build_bundle

        bundle = build_bundle()
        passed = [
            "ENGG 101",
            "GEd 108",
            "GEd 104",
            "CpE 101",
            "GEd 105",
            "CHEM 101",
            "PATHFit 1",
            "NSTP 111",
        ]
        res = validate_pos_via_solver(
            {
                "current_sem": 2,
                "passed_courses": passed,
                "failed_courses": ["MATH 101"],
                "with_petition": False,
                "curriculum_bundle": bundle,
            },
            with_petition=False,
            forced_semester_map=None,
        )
        self.assertEqual(res.get("status"), "ok", res.get("message") or "")

    def test_validate_pos_math101_fail_y1s1_feasible_when_marked_rigid(self) -> None:
        """Staff bundles default Diff Cal to Rigid; failed retake must still be schedulable."""
        import copy
        import sys
        from pathlib import Path

        from pos.solver_bridge import validate_pos_via_solver

        thesis_root = Path(__file__).resolve().parents[3]
        solver_root = thesis_root / "Solver-Side"
        sr = str(solver_root)
        if sr not in sys.path:
            sys.path.insert(0, sr)
        from tools.export_static_curriculum import build_bundle

        bundle = copy.deepcopy(build_bundle())
        self.assertIn("MATH 101", bundle["courses"])
        bundle["courses"]["MATH 101"] = dict(bundle["courses"]["MATH 101"])
        bundle["courses"]["MATH 101"]["category"] = "Rigid"
        passed = [
            "ENGG 101",
            "GEd 108",
            "GEd 104",
            "CpE 101",
            "GEd 105",
            "CHEM 101",
            "PATHFit 1",
            "NSTP 111",
        ]
        res = validate_pos_via_solver(
            {
                "current_sem": 2,
                "passed_courses": passed,
                "failed_courses": ["MATH 101"],
                "with_petition": False,
                "curriculum_bundle": bundle,
            },
            with_petition=False,
            forced_semester_map=None,
        )
        self.assertEqual(res.get("status"), "ok", res.get("message") or "")
