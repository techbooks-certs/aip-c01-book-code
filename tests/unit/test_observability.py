import unittest
from claimsassist.baseline import BoundaryError
from claimsassist.observability import *


class ObservabilityTests(unittest.TestCase):
    def event(self, **changes):
        e = {
            "correlation_id": "corr-1",
            "stage": "retrieval",
            "status": "OK",
            "duration_ms": 20,
        }
        e.update(changes)
        return e

    def record(self, **changes):
        r = {
            "source_current": True,
            "retrieval_contains_required": True,
            "prompt_contains_retrieval": True,
            "output_supported": True,
            "tool_authorized": True,
        }
        r.update(changes)
        return r

    def test_event_allowlist_drops_payload(self):
        self.assertNotIn("prompt", sanitize_event(self.event(prompt="private")))

    def test_event_requires_correlation(self):
        e = self.event()
        e.pop("correlation_id")
        with self.assertRaises(BoundaryError):
            sanitize_event(e)

    def test_trace_rejects_mixed_requests(self):
        with self.assertRaises(BoundaryError):
            assemble_trace([self.event(), self.event(correlation_id="corr-2")])

    def test_trace_finds_first_failure(self):
        self.assertEqual(
            assemble_trace([self.event(), self.event(stage="model", status="TIMEOUT")])[
                "first_failure"
            ]["stage"],
            "model",
        )

    def test_trace_sums_observed_duration(self):
        self.assertEqual(
            assemble_trace(
                [self.event(duration_ms=2), self.event(stage="model", duration_ms=3)]
            )["total_observed_ms"],
            5,
        )

    def test_access_denied_classification(self):
        self.assertEqual(
            classify_api_failure(403, "AccessDeniedException"), "IDENTITY_OR_POLICY"
        )

    def test_throttle_classification(self):
        self.assertEqual(
            classify_api_failure(429, "ThrottlingException"), "QUOTA_OR_CAPACITY"
        )

    def test_validation_classification(self):
        self.assertEqual(
            classify_api_failure(400, "ValidationException"), "REQUEST_CONTRACT"
        )

    def test_stale_source_is_first_boundary(self):
        self.assertEqual(
            diagnose_answer(
                self.record(source_current=False, retrieval_contains_required=False)
            ),
            "STALE_SOURCE",
        )

    def test_retrieval_miss_differs_from_prompt_loss(self):
        self.assertEqual(
            diagnose_answer(self.record(retrieval_contains_required=False)),
            "RETRIEVAL_MISS",
        )
        self.assertEqual(
            diagnose_answer(self.record(prompt_contains_retrieval=False)),
            "CONTEXT_ASSEMBLY",
        )

    def test_unsupported_output_is_generation_boundary(self):
        self.assertEqual(
            diagnose_answer(self.record(output_supported=False)),
            "GENERATION_OR_VALIDATION",
        )

    def test_tool_auth_is_independent(self):
        self.assertEqual(
            diagnose_answer(self.record(tool_authorized=False)), "TOOL_AUTHORIZATION"
        )

    def test_runbook_requires_verification(self):
        with self.assertRaises(BoundaryError):
            Runbook("timeouts", ("trace",), "rollback", "", "page on-call")

    def test_incident_stays_open_until_regression(self):
        self.assertFalse(
            incident_recovery(
                capability_disabled=True,
                rollback_verified=True,
                impact_recorded=True,
                regression_added=False,
            )["ready_to_close"]
        )

    def test_incident_closes_with_all_evidence(self):
        self.assertTrue(
            incident_recovery(
                capability_disabled=True,
                rollback_verified=True,
                impact_recorded=True,
                regression_added=True,
            )["ready_to_close"]
        )
