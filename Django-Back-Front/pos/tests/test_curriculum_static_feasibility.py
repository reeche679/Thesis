"""Tests for static prereq/co-req graph checks (no CP-SAT)."""

from django.test import SimpleTestCase

from pos.curriculum_static_feasibility import static_prereq_horizon_diagnosis


class StaticPrereqHorizonDiagnosisTests(SimpleTestCase):
    def test_cycle_among_remaining(self):
        courses = {
            "A": {"units": 3, "pre-requisites": "B"},
            "B": {"units": 3, "pre-requisites": "A"},
        }
        r = static_prereq_horizon_diagnosis(
            courses=courses,
            passed_courses=[],
            failed_courses=[],
            current_sem=1,
            max_semester=8,
            prereq_aliases={},
            year_standing_rules={},
        )
        self.assertTrue(r["graph_has_cycle_among_remaining"])
        self.assertGreaterEqual(r["prereq_coreq_edges_among_remaining"], 2)

    def test_longest_chain_exceeds_horizon(self):
        courses = {
            "A": {"units": 3},
            "B": {"units": 3, "pre-requisites": "A"},
            "C": {"units": 3, "pre-requisites": "B"},
            "D": {"units": 3, "pre-requisites": "C"},
            "E": {"units": 3, "pre-requisites": "D"},
        }
        r = static_prereq_horizon_diagnosis(
            courses=courses,
            passed_courses=[],
            failed_courses=[],
            current_sem=1,
            max_semester=3,
            prereq_aliases={},
            year_standing_rules={},
        )
        self.assertFalse(r["graph_has_cycle_among_remaining"])
        self.assertEqual(r["available_semester_slots"], 3)
        self.assertEqual(r["longest_remaining_chain_length_nodes"], 5)
        self.assertTrue(r["sequential_chain_longer_than_available_slots"])

    def test_unknown_and_prereq_token(self):
        courses = {
            "X": {"units": 3, "pre-requisites": "NOT_A_REAL_COURSE_CODE"},
        }
        r = static_prereq_horizon_diagnosis(
            courses=courses,
            passed_courses=[],
            failed_courses=[],
            current_sem=1,
            max_semester=10,
            prereq_aliases={},
            year_standing_rules={},
        )
        self.assertEqual(len(r["unknown_or_unresolved_prereq_tokens_sample"]), 1)
        self.assertEqual(r["unknown_or_unresolved_prereq_tokens_sample"][0]["token"], "NOT_A_REAL_COURSE_CODE")

    def test_or_group_unresolved(self):
        courses = {
            "X": {"units": 3, "pre-requisites": [["BAD1", "BAD2"]]},
        }
        r = static_prereq_horizon_diagnosis(
            courses=courses,
            passed_courses=[],
            failed_courses=[],
            current_sem=1,
            max_semester=10,
            prereq_aliases={},
            year_standing_rules={},
        )
        self.assertEqual(len(r["or_prereq_groups_with_no_course_and_no_year_standing"]), 1)
