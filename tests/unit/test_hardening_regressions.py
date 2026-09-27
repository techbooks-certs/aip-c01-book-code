from decimal import Decimal
import unittest
from claimsassist.baseline import BoundaryError
from claimsassist.optimization import (
    WorkflowSample,
    summarize,
    choose_candidate,
    required_concurrency,
    latency_waterfall,
)
from claimsassist.observability import (
    sanitize_event,
    diagnose_answer,
    incident_recovery,
)


class HardeningRegressionTests(unittest.TestCase):
    def test_valid_numeric_strings_are_normalized_before_gate_comparison(self):
        metrics = dict(
            cost_per_accepted="0.1",
            p95_total_ms="10",
            acceptance_rate="0.9",
            mean_quality="0.9",
            safety_failures=0,
        )
        self.assertEqual(
            choose_candidate(
                "baseline",
                {"candidate": metrics},
                max_cost_per_accepted="1",
                max_p95_ms="100",
                min_acceptance_rate="0.8",
                min_quality="0.8",
            ),
            "candidate",
        )

    def test_capacity_rejects_nonfinite_and_boolean_values(self):
        for value in [float("nan"), float("inf"), True, -1]:
            with self.subTest(value=value), self.assertRaises(BoundaryError):
                required_concurrency(value, 2)

    def test_quality_floor_is_a_finite_rate(self):
        sample = WorkflowSample(
            1, 1, Decimal(0), Decimal(0), Decimal(0), True, Decimal(".9"), True, 1, 2
        )
        rates = dict(input_per_million=1, output_per_million=1, human_per_minute=1)
        for floor in ["NaN", 1.1, -0.1]:
            with self.subTest(floor=floor), self.assertRaises(BoundaryError):
                summarize([sample], rates, floor)

    def test_safety_failure_cannot_be_averaged_away_in_optimization(self):
        sample = WorkflowSample(
            1, 1, Decimal(0), Decimal(0), Decimal(0), True, Decimal(".9"), True, 1, 2
        )
        unsafe = WorkflowSample(
            1, 1, Decimal(0), Decimal(0), Decimal(0), True, Decimal(".9"), False, 1, 2
        )
        metrics = summarize(
            [sample] * 99 + [unsafe],
            dict(input_per_million=1, output_per_million=1, human_per_minute=1),
            0.8,
        )
        limits = dict(
            max_cost_per_accepted=1,
            max_p95_ms=100,
            min_acceptance_rate=0.9,
            min_quality=0.8,
        )
        self.assertEqual(metrics["safety_failures"], 1)
        self.assertEqual(
            choose_candidate("baseline", {"unsafe": metrics}, **limits), "baseline"
        )
        del metrics["safety_failures"]
        self.assertEqual(
            choose_candidate("baseline", {"unknown": metrics}, **limits), "baseline"
        )

    def test_waterfall_rejects_invalid_duration_types(self):
        for value in [True, float("nan"), "100", -1]:
            with self.subTest(value=value), self.assertRaises(BoundaryError):
                latency_waterfall({"tool": value})

    def test_nested_payload_cannot_hide_inside_allowed_telemetry_field(self):
        event = dict(
            correlation_id="corr-example", stage="model", status="OK", duration_ms=1
        )
        for key, value in [
            ("model_ref", {"secret": "sensitive"}),
            ("document_ids", [{"text": "private"}]),
            ("input_tokens", True),
        ]:
            with self.subTest(key=key), self.assertRaises(BoundaryError):
                sanitize_event(event | {key: value})

    def test_truthy_strings_cannot_close_incident_or_pass_diagnosis(self):
        with self.assertRaises(BoundaryError):
            incident_recovery(
                capability_disabled="false",
                rollback_verified=True,
                impact_recorded=True,
                regression_added=True,
            )
        fields = {
            k: True
            for k in [
                "source_current",
                "retrieval_contains_required",
                "prompt_contains_retrieval",
                "output_supported",
                "tool_authorized",
            ]
        }
        with self.assertRaises(BoundaryError):
            diagnose_answer(fields | {"source_current": "false"})
