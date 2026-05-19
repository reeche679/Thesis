"""Tests for placement diagnostics, standing parity, trace resolution, and category resolution."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from pos.solver_course_reference import resolve_course_category
from pos.standing_parity import cp_sat_prefix_standing_numbers


class StandingParityTests(SimpleTestCase):
    def test_cp_sat_prefix_remaining_need(self) -> None:
        courses = {
            "A": {"units": 10, "orig_sem": 1, "pre-requisites": []},
            "B": {"units": 10, "orig_sem": 2, "pre-requisites": ["2nd Year Standing"]},
        }
        tot, ear, req, rem = cp_sat_prefix_standing_numbers("B", courses, passed_courses=["A"])
        self.assertEqual(tot, 10.0)
        self.assertEqual(ear, 10.0)
        self.assertEqual(req, 9.0)
        self.assertEqual(rem, 0.0)

        tot2, ear2, req2, rem2 = cp_sat_prefix_standing_numbers("B", courses, passed_courses=[])
        self.assertGreater(rem2, 0.0)


class TraceDefaultsStringTests(SimpleTestCase):
    def test_trace_defaults_bare_string_resolves_course(self) -> None:
        thesis_root = Path(__file__).resolve().parents[3]
        script = thesis_root / "Solver-Side" / "Main-Solver" / "CP-SAT.py"
        spec = importlib.util.spec_from_file_location("cpsat_trace_t", script)
        assert spec and spec.loader
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        courses = {"MATH 101": {"units": 3, "orig_sem": 1, "category": "Rigid"}}
        m._TRACE_DEFAULTS = "MATH 101"
        s = m._resolve_trace_course_codes({}, courses)
        self.assertEqual(set(s), {"MATH 101"})
        m._TRACE_DEFAULTS = ("MATH 101",)
        s2 = m._resolve_trace_course_codes({"cp_sat_constraint_trace_courses": ["MATH 101"]}, courses)
        self.assertEqual(set(s2), {"MATH 101"})


class PlacementDiagnosticsGlobalCapacityTests(SimpleTestCase):
    def test_global_capacity_fail_small_horizon(self) -> None:
        thesis_root = Path(__file__).resolve().parents[3]
        solver_root = thesis_root / "Solver-Side"
        sys.path.insert(0, str(solver_root / "Main-Solver"))
        sys.path.insert(0, str(solver_root))
        from algorithms_and_helpers.placement_diagnostics import run_placement_diagnostics
        from algorithms_and_helpers.pos_helpers import _get_req_list, _normalize_code

        courses = {
            "A": {"units": 10, "orig_sem": 1, "category": "Flexible", "pre-requisites": [], "co-requisites": []},
            "B": {"units": 10, "orig_sem": 1, "category": "Flexible", "pre-requisites": [], "co-requisites": []},
            "C": {"units": 10, "orig_sem": 1, "category": "Flexible", "pre-requisites": [], "co-requisites": []},
        }

        def max_u(_s: int) -> int:
            return 5

        def vs(_code: str, _info: dict, **_kwargs):
            return [1, 2, 3]

        def fs(info):
            if not isinstance(info, dict):
                return None
            return int(info.get("orig_sem", 1))

        out = run_placement_diagnostics(
            courses=courses,
            passed_courses=[],
            failed_courses=[],
            current_sem=1,
            with_petition=False,
            max_semester=3,
            max_units_for_sem=max_u,
            valid_semesters_fn=lambda c, i: vs(c, i),
            first_semester_fn=fs,
            normalize_code=_normalize_code,
            get_req_list=_get_req_list,
            prereq_aliases={},
            year_standing_rules={},
            configuration="test_fixture",
        )
        snap = out["capacity_feasibility_snapshot"]
        self.assertFalse(snap["global_capacity_ok"])
        self.assertEqual(snap["total_remaining_units"], 30)


class ResolveCourseCategoryTests(SimpleTestCase):
    """Tests for solver_course_reference.resolve_course_category."""

    _FAKE_STATIC = {"MATH 102": "Bottleneck", "PHYS 102": "Bottleneck", "ENGG 105": "Flexible"}

    def _patched(self, code, explicit=None):
        with patch(
            "pos.solver_course_reference.get_static_category_by_code",
            return_value=dict(self._FAKE_STATIC),
        ):
            return resolve_course_category(code, explicit)

    def test_no_explicit_falls_back_to_static_bottleneck(self) -> None:
        self.assertEqual(self._patched("MATH 102"), "Bottleneck")

    def test_no_explicit_falls_back_to_static_flexible(self) -> None:
        self.assertEqual(self._patched("ENGG 105"), "Flexible")

    def test_no_explicit_unknown_code_defaults_rigid(self) -> None:
        self.assertEqual(self._patched("EE 307"), "Rigid")

    def test_explicit_rigid_overrides_static(self) -> None:
        self.assertEqual(self._patched("MATH 102", "Rigid"), "Rigid")

    def test_explicit_bottleneck_respected(self) -> None:
        self.assertEqual(self._patched("EE 307", "Bottleneck"), "Bottleneck")

    def test_explicit_invalid_falls_back_to_static(self) -> None:
        self.assertEqual(self._patched("MATH 102", "Unknown"), "Bottleneck")

    def test_explicit_none_falls_back_to_static(self) -> None:
        self.assertEqual(self._patched("PHYS 102", None), "Bottleneck")


class RuntimeCategoryNormalizationTests(SimpleTestCase):
    """Tests for curriculum_runtime._normalize_categories_from_static runtime upgrade."""

    def _load_normalize(self):
        thesis_root = Path(__file__).resolve().parents[3]
        solver_root = thesis_root / "Solver-Side"
        if str(solver_root) not in sys.path:
            sys.path.insert(0, str(solver_root))
        from algorithms_and_helpers.curriculum_runtime import (
            _normalize_categories_from_static,
            context_from_approved_bundle,
        )
        return _normalize_categories_from_static, context_from_approved_bundle

    def test_rigid_upgraded_to_bottleneck_from_static(self) -> None:
        normalize, _ = self._load_normalize()
        courses = {
            "MATH 102": {"units": 4, "category": "Rigid", "orig_sem": 2, "pre-requisites": []},
        }
        normalize(courses)
        self.assertEqual(courses["MATH 102"]["category"], "Bottleneck")

    def test_explicit_flexible_not_downgraded(self) -> None:
        normalize, _ = self._load_normalize()
        courses = {
            "MATH 101": {"units": 4, "category": "Flexible", "orig_sem": 1, "pre-requisites": []},
        }
        normalize(courses)
        self.assertIn(courses["MATH 101"]["category"], ("Flexible", "Bottleneck"))

    def test_unknown_code_stays_rigid(self) -> None:
        normalize, _ = self._load_normalize()
        courses = {
            "EE 999": {"units": 3, "category": "Rigid", "orig_sem": 7, "pre-requisites": []},
        }
        normalize(courses)
        self.assertEqual(courses["EE 999"]["category"], "Rigid")

    def test_bundle_math102_rigid_becomes_bottleneck(self) -> None:
        _, from_bundle = self._load_normalize()
        bundle = {
            "courses": {
                "MATH 101": {
                    "course_title": "Differential Calculus", "units": 4,
                    "orig_sem": 1, "category": "Rigid",
                    "pre-requisites": [], "co-requisites": [], "year_level": 1,
                },
                "MATH 102": {
                    "course_title": "Integral Calculus", "units": 4,
                    "orig_sem": 2, "category": "Rigid",
                    "pre-requisites": ["MATH 101"], "co-requisites": [], "year_level": 1,
                },
            },
            "max_units_by_semester_index": [24, 24],
        }
        ctx = from_bundle(bundle)
        self.assertEqual(ctx.courses["MATH 102"]["category"], "Bottleneck")


class HorizonOverflowTests(SimpleTestCase):
    """Verify that uploaded curricula get overflow semesters for scheduling slack."""

    def _load_runtime(self):
        thesis_root = Path(__file__).resolve().parents[3]
        solver_root = thesis_root / "Solver-Side"
        if str(solver_root) not in sys.path:
            sys.path.insert(0, str(solver_root))
        from algorithms_and_helpers.curriculum_runtime import context_from_approved_bundle
        return context_from_approved_bundle

    def test_short_bundle_gets_overflow_semesters(self) -> None:
        from_bundle = self._load_runtime()
        bundle = {
            "courses": {
                "A": {"units": 3, "orig_sem": 10, "category": "Rigid",
                       "pre-requisites": [], "co-requisites": [], "year_level": 4},
                "B": {"units": 3, "orig_sem": 11, "category": "Rigid",
                       "pre-requisites": ["A"], "co-requisites": [], "year_level": 4},
            },
            "max_units_by_semester_index": [24] * 11,
        }
        ctx = from_bundle(bundle)
        self.assertEqual(ctx.max_semester, 17)
        self.assertEqual(ctx.max_units_for_sem(17), 24)
        self.assertEqual(ctx.max_units_for_sem(18), 0)

    def test_already_long_bundle_not_extended(self) -> None:
        from_bundle = self._load_runtime()
        bundle = {
            "courses": {
                "A": {"units": 3, "orig_sem": 5, "category": "Rigid",
                       "pre-requisites": [], "co-requisites": [], "year_level": 2},
            },
            "max_units_by_semester_index": [24] * 20,
        }
        ctx = from_bundle(bundle)
        self.assertEqual(ctx.max_semester, 20)

    def test_draft_to_approved_includes_overflow(self) -> None:
        from pos.curriculum_draft_to_approved import draft_to_approved_json, OVERFLOW_SEMESTERS
        caps_map = {str(y): {"1": 24, "2": 24, "3": 24} for y in range(1, 7)}
        draft = {
            "extracted_rows": [
                {"code": "A", "draft_row_id": "r1", "units": 3, "title": "Course A",
                 "term_index": 1, "year_level": 4, "prerequisite_codes": [],
                 "corequisite_codes": [], "raw_prerequisites": "", "raw_corequisites": ""},
                {"code": "B", "draft_row_id": "r2", "units": 3, "title": "Course B",
                 "term_index": 2, "year_level": 4, "prerequisite_codes": ["A"],
                 "corequisite_codes": [], "raw_prerequisites": "A", "raw_corequisites": ""},
            ],
            "max_units_by_year_term": caps_map,
        }
        approved = draft_to_approved_json(
            draft,
            program_slug="test",
            curriculum_version_label="v1",
            source_pdf_sha256=None,
        )
        caps = approved["max_units_by_semester_index"]
        max_orig = max(c["orig_sem"] for c in approved["courses"].values())
        self.assertGreaterEqual(len(caps), max_orig + OVERFLOW_SEMESTERS)


class BottleneckPetitionTests(SimpleTestCase):
    """Verify that Bottleneck offering windows differ between petition modes."""

    def _load_valid_semesters(self):
        thesis_root = Path(__file__).resolve().parents[3]
        solver_root = thesis_root / "Solver-Side"
        if str(solver_root / "Main-Solver") not in sys.path:
            sys.path.insert(0, str(solver_root / "Main-Solver"))
        if str(solver_root) not in sys.path:
            sys.path.insert(0, str(solver_root))
        import importlib
        m = importlib.import_module("CP-SAT")
        return m._valid_semesters_for_course, m

    def test_bottleneck_no_petition_is_term_type_only(self) -> None:
        """Without petition and no failures, Bottleneck uses Rigid-like term-type slots."""
        _valid, m = self._load_valid_semesters()
        old_cfg = m.CONFIGURATION
        try:
            m.CONFIGURATION = "config2_weighted"
            info = {"units": 4, "orig_sem": 2, "category": "Bottleneck"}
            no_pet = _valid("MATH 102", info, 2, with_petition=False, failed_courses=[])
            with_pet = _valid("MATH 102", info, 2, with_petition=True, failed_courses=[])
            self.assertTrue(set(no_pet).issubset(set(with_pet)))
            self.assertGreater(len(with_pet), len(no_pet),
                               "With petition must widen Bottleneck offering beyond term-type slots")
            for s in no_pet:
                self.assertEqual(s % 3, 2 % 3,
                                 f"No-petition Bottleneck sem {s} should match orig_sem term type")
        finally:
            m.CONFIGURATION = old_cfg

    def test_bottleneck_with_failed_prereq_no_petition(self) -> None:
        """Failed MATH 101: MATH 102 (non-failed Bottleneck) still restricted without petition."""
        _valid, m = self._load_valid_semesters()
        old_cfg = m.CONFIGURATION
        try:
            m.CONFIGURATION = "config2_weighted"
            info_102 = {"units": 4, "orig_sem": 2, "category": "Bottleneck"}
            info_101 = {"units": 4, "orig_sem": 1, "category": "Bottleneck"}
            failed = ["MATH 101"]

            no_pet_102 = _valid("MATH 102", info_102, 2, with_petition=False, failed_courses=failed)
            with_pet_102 = _valid("MATH 102", info_102, 2, with_petition=True, failed_courses=failed)
            self.assertGreater(len(with_pet_102), len(no_pet_102),
                               "Non-failed Bottleneck must differ between petition modes even with other failures")
            for s in no_pet_102:
                self.assertEqual(s % 3, 2 % 3)

            no_pet_101 = _valid("MATH 101", info_101, 2, with_petition=False, failed_courses=failed)
            with_pet_101 = _valid("MATH 101", info_101, 2, with_petition=True, failed_courses=failed)
            self.assertGreater(len(with_pet_101), len(no_pet_101),
                               "Failed Bottleneck must widen only with petition")
            for s in no_pet_101:
                self.assertEqual(s % 3, 1 % 3,
                                 "Failed Bottleneck without petition uses Rigid term-type slots only")
        finally:
            m.CONFIGURATION = old_cfg

    def test_failed_rigid_stays_in_orig_term_type_band(self) -> None:
        """
        Failed Rigid courses must still retake in the same term-type band as orig_sem,
        even without petition and even when failed.
        With petition does NOT change Rigid — only Bottleneck gets extra freedom from petition.
        """
        _valid, m = self._load_valid_semesters()
        old_cfg = m.CONFIGURATION
        try:
            m.CONFIGURATION = "config2_weighted"
            # orig_sem=2 → term-type band = 2nd semester (s % 3 == 2)
            info = {"units": 3, "orig_sem": 2, "category": "Rigid"}
            for wp in (False, True):
                sems = _valid("ENGG 402", info, 3, with_petition=wp, failed_courses=["ENGG 402"])
                for s in sems:
                    self.assertEqual(
                        s % 3, 2,
                        f"Failed Rigid course must stay in 2nd-sem band "
                        f"(s={s}, s%3={s%3}, with_petition={wp})",
                    )
                # Must still have valid slots (the band is not empty from current_sem=3 onward)
                self.assertTrue(len(sems) > 0, "Rigid failed course must have at least one valid semester")
        finally:
            m.CONFIGURATION = old_cfg

    def test_failed_bottleneck_no_petition_term_type_only(self) -> None:
        """Failed Bottleneck without petition: Rigid-like, not next arbitrary term."""
        _valid, m = self._load_valid_semesters()
        old_cfg = m.CONFIGURATION
        try:
            m.CONFIGURATION = "config2_weighted"
            info = {"units": 4, "orig_sem": 1, "category": "Bottleneck"}
            sems = _valid("MATH 101", info, 2, with_petition=False, failed_courses=["MATH 101"])
            self.assertNotEqual(sems, list(range(2, m.u.MAX_SEMESTER + 1)))
            for s in sems:
                self.assertEqual(s % 3, 1 % 3)
        finally:
            m.CONFIGURATION = old_cfg


class ElectiveCodeMappingTests(SimpleTestCase):
    """CpEE 401 (UI) vs Elective Course 1 (static solver) must match the active bundle."""

    def test_db_bundle_keeps_cpee_passed_code(self) -> None:
        from pos.views import _to_solver_pass_fail_codes

        keys = {"CpEE 401", "CpEE 402", "MATH 101"}
        passed = _to_solver_pass_fail_codes(["CpEE 401"], keys)
        self.assertEqual(passed, ["CpEE 401"])
        self.assertNotIn("Elective Course 1", passed)

    def test_static_bundle_maps_ui_elective_to_solver_name(self) -> None:
        from pos.views import _to_solver_pass_fail_codes

        keys = {"Elective Course 1", "Elective Course 2", "MATH 101"}
        passed = _to_solver_pass_fail_codes(["CpEE 401"], keys)
        self.assertEqual(passed, ["Elective Course 1"])
