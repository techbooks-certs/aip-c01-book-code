import unittest
from decimal import Decimal
from claimsassist.baseline import BoundaryError
from claimsassist.evaluation import *


class EvaluationTests(unittest.TestCase):
    def case(self, **kw):
        v = dict(
            case_id="c1",
            split="holdout",
            category="coverage",
            subgroup="standard",
            required_sources=frozenset({"s1"}),
            answerable=True,
        )
        v.update(kw)
        return EvalCase(**v)

    def result(self, **kw):
        v = dict(
            retrieved_sources=["s1"],
            cited_sources=["s1"],
            support_verified=True,
            abstained=False,
            tool_valid=True,
            approval_respected=True,
            safety_pass=True,
            latency_ms=100,
            cost=".01",
        )
        v.update(kw)
        return v

    def test_supported_case_passes(self):
        self.assertTrue(score_case(self.case(), self.result())["accepted"])

    def test_missing_evidence_fails(self):
        self.assertFalse(
            score_case(
                self.case(), self.result(retrieved_sources=[], cited_sources=[])
            )["accepted"]
        )

    def test_uncited_answer_fails_grounding(self):
        self.assertFalse(
            score_case(self.case(), self.result(cited_sources=[]))["grounded"]
        )

    def test_unanswerable_requires_abstention(self):
        self.assertFalse(
            score_case(
                self.case(answerable=False, required_sources=frozenset()),
                self.result(abstained=False, cited_sources=[]),
            )["accepted"]
        )

    def test_valid_abstention_passes(self):
        self.assertTrue(
            score_case(
                self.case(answerable=False, required_sources=frozenset()),
                self.result(abstained=True, cited_sources=[]),
            )["accepted"]
        )

    def test_tool_contract_is_gate(self):
        self.assertFalse(
            score_case(self.case(), self.result(tool_valid=False))["accepted"]
        )

    def test_approval_is_gate(self):
        self.assertFalse(
            score_case(self.case(), self.result(approval_respected=False))["accepted"]
        )

    def test_safety_is_gate(self):
        self.assertFalse(
            score_case(self.case(), self.result(safety_pass=False))["accepted"]
        )

    def test_scorecard_reports_subgroups(self):
        cases = [self.case(), self.case(case_id="c2", subgroup="rare")]
        card = scorecard(cases, {"c1": self.result(), "c2": self.result()})
        self.assertEqual(set(card["subgroup_acceptance"]), {"standard", "rare"})

    def test_release_approves_all_floors(self):
        card = scorecard([self.case()], {"c1": self.result()})
        self.assertEqual(
            release_decision(
                card,
                min_acceptance=1,
                min_evidence=1,
                max_cost=0.02,
                max_latency=200,
                min_subgroup=1,
            )["decision"],
            "APPROVE",
        )

    def test_release_rejects_quality_regression(self):
        card = scorecard([self.case()], {"c1": self.result(safety_pass=False)})
        self.assertIn(
            "acceptance",
            release_decision(
                card,
                min_acceptance=1,
                min_evidence=1,
                max_cost=0.02,
                max_latency=200,
                min_subgroup=1,
            )["failures"],
        )

    def test_release_rejects_subgroup_failure(self):
        cases = [self.case(), self.case(case_id="c2", subgroup="rare")]
        card = scorecard(
            cases, {"c1": self.result(), "c2": self.result(safety_pass=False)}
        )
        self.assertIn(
            "subgroup",
            release_decision(
                card,
                min_acceptance=0.5,
                min_evidence=1,
                max_cost=0.02,
                max_latency=200,
                min_subgroup=1,
            )["failures"],
        )

    def test_holdout_leak_rejected(self):
        with self.assertRaises(BoundaryError):
            assert_no_holdout_leak([self.case()], {"c1"})

    def test_development_tuning_allowed(self):
        self.assertTrue(
            assert_no_holdout_leak([self.case(split="development")], {"c1"})
        )

    def test_pack_reports_missing_artifact(self):
        self.assertIn("runbook", validate_evidence_pack({})["missing"])

    def test_pack_complete(self):
        keys = {
            "architecture_decision",
            "release_manifest",
            "dataset_manifest",
            "evaluation_report",
            "threat_control_review",
            "cost_assumptions",
            "runbook",
            "cleanup_verification",
        }
        self.assertTrue(validate_evidence_pack({k: "x" for k in keys})["complete"])

    def test_absent_required_subgroup_cannot_disappear_from_release_gate(self):
        card = scorecard([self.case()], {"c1": self.result()})
        decision = release_decision(
            card,
            min_acceptance=1,
            min_evidence=1,
            max_cost=0.02,
            max_latency=200,
            min_subgroup=1,
            required_subgroups={"standard", "rare"},
        )
        self.assertIn("missing_subgroup", decision["failures"])
        self.assertEqual(decision["decision"], "REJECT")

    def test_perfect_score_with_insufficient_cases_is_rejected(self):
        card = scorecard([self.case()], {"c1": self.result()})
        decision = release_decision(
            card,
            min_acceptance=1,
            min_evidence=1,
            max_cost=0.02,
            max_latency=200,
            min_subgroup=1,
            min_cases_per_subgroup=2,
        )
        self.assertIn("subgroup_sample_size", decision["failures"])

    def test_subgroup_count_cannot_be_truthy_boolean_or_missing(self):
        card = scorecard([self.case()], {"c1": self.result()})
        for counts in [None, {"standard": True}, {"standard": 2}]:
            with self.subTest(counts=counts), self.assertRaises(BoundaryError):
                release_decision(
                    card | {"subgroup_counts": counts},
                    min_acceptance=1,
                    min_evidence=1,
                    max_cost=0.02,
                    max_latency=200,
                    min_subgroup=1,
                )
