from pathlib import Path
import tempfile
import unittest

from claimsassist.baseline import BoundaryError
from claimsassist.deployment import (
    DeploymentContext,
    JobLedger,
    LocalGateway,
    ReleaseRegistry,
    demonstration,
    stream_fixture,
    fixture_release_manifest,
)


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.context = DeploymentContext(
            "adjuster-ava",
            "tenant-amber",
            "corr-1",
            frozenset({"stream_summary", "queue_summary"}),
            2000,
        )

    def manifest(self, release="release-1.0.0", status="PASS"):
        return fixture_release_manifest(release, status, 20)

    def test_gateway_allowlist_budget_and_deadline(self):
        gate = LocalGateway({"bedrock/demo-v1"}, {"tenant-amber": 2})
        self.assertEqual(
            gate.authorize(self.context, "stream_summary", "bedrock/demo-v1", 2, 1000)[
                "tenant"
            ],
            "tenant-amber",
        )
        with self.assertRaises(BoundaryError):
            gate.authorize(self.context, "stream_summary", "bedrock/demo-v1", 1, 1000)
        with self.assertRaises(BoundaryError):
            LocalGateway({"bedrock/demo-v1"}, {"tenant-amber": 2}).authorize(
                self.context, "stream_summary", "bedrock/demo-v1", 1, 2000
            )

    def test_gateway_rejects_model_and_operation(self):
        gate = LocalGateway({"bedrock/demo-v1"}, {"tenant-amber": 5})
        with self.assertRaises(BoundaryError):
            gate.authorize(self.context, "admin", "bedrock/demo-v1", 1, 1000)
        with self.assertRaises(BoundaryError):
            gate.authorize(self.context, "stream_summary", "unapproved", 1, 1000)

    def test_stream_events_are_typed_and_reviewed(self):
        gate = LocalGateway({"bedrock/demo-v1"}, {"tenant-amber": 5})
        auth = gate.authorize(
            self.context, "stream_summary", "bedrock/demo-v1", 1, 1000
        )
        events = list(
            stream_fixture(self.context, auth, ["one", "two"], "release-1.0.0")
        )
        self.assertEqual(
            [e["event"] for e in events], ["start", "delta", "delta", "complete"]
        )
        self.assertTrue(events[-1]["review_required"])

    def test_stream_rejects_mismatched_authorization(self):
        with self.assertRaises(BoundaryError):
            list(
                stream_fixture(
                    self.context,
                    {"tenant": "tenant-birch", "correlation_id": "corr-1"},
                    ["x"],
                    "release-1.0.0",
                )
            )

    def test_submit_duplicate_is_one_job(self):
        ledger = JobLedger(self.root / "jobs.sqlite")
        a = ledger.submit(
            "tenant-amber", "req-1", {"claim_id": "claim-0001"}, "release-1.0.0"
        )
        b = ledger.submit(
            "tenant-amber", "req-1", {"claim_id": "claim-0001"}, "release-1.0.0"
        )
        self.assertEqual(a, b)

    def test_changed_payload_under_same_key_rejected(self):
        ledger = JobLedger(self.root / "jobs.sqlite")
        ledger.submit(
            "tenant-amber", "req-1", {"claim_id": "claim-0001"}, "release-1.0.0"
        )
        with self.assertRaises(BoundaryError):
            ledger.submit(
                "tenant-amber", "req-1", {"claim_id": "claim-0002"}, "release-1.0.0"
            )

    def test_success_is_not_invoked_twice(self):
        ledger = JobLedger(self.root / "jobs.sqlite")
        job = ledger.submit(
            "tenant-amber", "req-1", {"claim_id": "claim-0001"}, "release-1.0.0"
        )
        calls = []
        provider = lambda payload: (
            calls.append(payload) or {"summary": "done", "review_required": True}
        )
        self.assertEqual(
            ledger.process_delivery(job["job_id"], "tenant-amber", provider),
            ledger.process_delivery(job["job_id"], "tenant-amber", provider),
        )
        self.assertEqual(len(calls), 1)

    def test_timeout_moves_to_retry_then_dlq(self):
        ledger = JobLedger(self.root / "jobs.sqlite")
        job = ledger.submit(
            "tenant-amber", "req-1", {"claim_id": "claim-0001"}, "release-1.0.0"
        )

        def timeout(payload):
            raise TimeoutError

        self.assertEqual(
            ledger.process_delivery(job["job_id"], "tenant-amber", timeout)["state"],
            "RETRYABLE",
        )
        self.assertEqual(
            ledger.process_delivery(job["job_id"], "tenant-amber", timeout)["state"],
            "DLQ",
        )

    def test_provider_exception_is_sanitized(self):
        ledger = JobLedger(self.root / "jobs.sqlite")
        job = ledger.submit(
            "tenant-amber", "req-1", {"claim_id": "claim-0001"}, "release-1.0.0"
        )

        def fail(payload):
            raise RuntimeError("secret-token")

        result = ledger.process_delivery(job["job_id"], "tenant-amber", fail)
        self.assertEqual(result["error"], "DEPENDENCY_FAILURE")
        self.assertNotIn("secret", str(result))

    def test_cross_tenant_job_hidden(self):
        ledger = JobLedger(self.root / "jobs.sqlite")
        job = ledger.submit(
            "tenant-amber", "req-1", {"claim_id": "claim-0001"}, "release-1.0.0"
        )
        with self.assertRaises(BoundaryError):
            ledger.process_delivery(job["job_id"], "tenant-birch", lambda x: {})

    def test_invalid_provider_result_rejected(self):
        ledger = JobLedger(self.root / "jobs.sqlite")
        job = ledger.submit(
            "tenant-amber", "req-1", {"claim_id": "claim-0001"}, "release-1.0.0"
        )
        calls = []

        def malformed(payload):
            calls.append(payload)
            return {"summary": "x", "review_required": False}

        first = ledger.process_delivery(job["job_id"], "tenant-amber", malformed)
        second = JobLedger(self.root / "jobs.sqlite").process_delivery(
            job["job_id"], "tenant-amber", malformed
        )
        self.assertEqual(first["state"], "DLQ")
        self.assertEqual(first["error"], "INVALID_PROVIDER_RESULT")
        self.assertEqual(second["attempts"], 1)
        self.assertEqual(len(calls), 1)

    def test_failed_release_cannot_promote(self):
        registry = ReleaseRegistry(self.root / "release.sqlite")
        registry.register(self.manifest(status="FAIL"))
        with self.assertRaises(BoundaryError):
            registry.promote("release-1.0.0")

    def test_passed_release_promotes_and_rolls_back(self):
        registry = ReleaseRegistry(self.root / "release.sqlite")
        registry.register(self.manifest("release-1.0.0"))
        registry.register(self.manifest("release-1.1.0"))
        registry.promote("release-1.0.0")
        registry.promote("release-1.1.0")
        self.assertEqual(registry.rollback()["current"], "release-1.0.0")

    def test_rollback_requires_target(self):
        registry = ReleaseRegistry(self.root / "release.sqlite")
        registry.register(self.manifest())
        registry.promote("release-1.0.0")
        with self.assertRaises(BoundaryError):
            registry.rollback()

    def test_demonstration_duplicate_and_gate(self):
        work = self.root / "demo"
        work.mkdir()
        report = demonstration(work)
        self.assertEqual(report["provider_calls"], 1)
        self.assertEqual(report["job_first"], report["job_duplicate"])
        self.assertIn("quality gate", report["failed_release_rejected"])

    def test_stream_rejects_same_tenant_wrong_subject_or_operation(self):
        gate = LocalGateway({"bedrock/demo-v1"}, {"tenant-amber": 5})
        auth = gate.authorize(self.context, "queue_summary", "bedrock/demo-v1", 1, 1000)
        with self.assertRaises(BoundaryError):
            list(stream_fixture(self.context, auth, ["x"], "release-1.0.0"))
        auth = gate.authorize(
            self.context, "stream_summary", "bedrock/demo-v1", 1, 1000
        )
        with self.assertRaises(BoundaryError):
            list(
                stream_fixture(
                    self.context,
                    auth | {"subject": "adjuster-other"},
                    ["x"],
                    "release-1.0.0",
                )
            )

    def test_repeated_promotion_preserves_previous_release(self):
        registry = ReleaseRegistry(self.root / "repeat.sqlite")
        for name in ("release-one", "release-two"):
            registry.register(fixture_release_manifest(name))
            registry.promote(name)
        self.assertEqual(
            registry.promote("release-two"),
            {"current": "release-two", "previous": "release-one"},
        )
        self.assertEqual(registry.rollback()["current"], "release-one")
