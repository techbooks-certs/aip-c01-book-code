from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from claimsassist.baseline import BoundaryError
from claimsassist.deployment import ReleaseRegistry, fixture_release_manifest
from claimsassist.rag_answer import observed_answer, fixture_provider
from claimsassist.rag_demo import build_index


class ReleaseIdentityTests(unittest.TestCase):
    def test_changed_material_dependency_rejects_stale_evaluation(self):
        with TemporaryDirectory() as d:
            registry = ReleaseRegistry(Path(d) / "release.db")
            for field, value in [
                ("dataset_sha256", "d" * 64),
                ("index_version", "new-index"),
                ("evidence_sha256", "e" * 64),
                ("guardrail_version", "new-guardrail"),
                ("release_id", "release-new"),
            ]:
                m = fixture_release_manifest("release-base")
                m[field] = value
                with self.subTest(field=field), self.assertRaises(BoundaryError):
                    registry.register(m)

    def test_missing_dependency_and_modified_report_rejected(self):
        with TemporaryDirectory() as d:
            registry = ReleaseRegistry(Path(d) / "release.db")
            m = fixture_release_manifest("release-base")
            del m["guardrail_version"]
            with self.assertRaises(BoundaryError):
                registry.register(m)
            m = fixture_release_manifest("release-base")
            m["evaluation_report"]["cases"] += 1
            with self.assertRaises(BoundaryError):
                registry.register(m)

    def test_fixture_hash_binding_does_not_claim_real_review(self):
        with TemporaryDirectory() as d:
            registry = ReleaseRegistry(Path(d) / "release.db")
            a = fixture_release_manifest("release-one")
            b = fixture_release_manifest("release-two")
            self.assertNotEqual(
                a["evaluation_report"]["subject_sha256"],
                b["evaluation_report"]["subject_sha256"],
            )
            registry.register(a)
            registry.register(b)
            registry.promote("release-one")
            registry.promote("release-two")
            self.assertEqual(registry.rollback()["current"], "release-one")


class ObservedAnswerTests(unittest.TestCase):
    def run_answer(self, provider, question="Explain gradual corrosion"):
        return observed_answer(
            question,
            index=build_index(),
            tenant="tenant-amber",
            loss_date="2026-09-20",
            provider=provider,
            source_id="pol-4821",
        )

    def test_connected_trace_times_actual_boundaries_without_payload(self):
        report = self.run_answer(fixture_provider)
        self.assertEqual(report["status"], "completed")
        trace = report["trace"]
        self.assertEqual(
            [e["stage"] for e in trace["events"]], ["retrieval", "model", "response"]
        )
        self.assertTrue(
            all(
                type(e["duration_ms"]) is int and e["duration_ms"] >= 0
                for e in trace["events"]
            )
        )
        serialized = json.dumps(trace)
        for private in [
            "tenant-amber",
            "pol-4821",
            "gradual corrosion",
            "authored candidate",
        ]:
            self.assertNotIn(private, serialized)
        self.assertFalse(trace["semantic_quality_evaluated"])
        self.assertFalse(trace["exported_to_managed_telemetry"])

    def test_provider_failure_has_first_boundary_without_private_exception(self):
        def fail(request):
            raise RuntimeError("secret claim payload")

        report = self.run_answer(fail)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["trace"]["first_failure"]["stage"], "model")
        self.assertNotIn("secret claim payload", json.dumps(report))

    def test_invalid_generated_answer_fails_response_boundary(self):
        report = self.run_answer(lambda request: {"claims": [], "abstained": False})
        self.assertEqual(report["trace"]["first_failure"]["stage"], "response")

    def test_request_correlation_not_reused(self):
        self.assertNotEqual(
            self.run_answer(fixture_provider)["trace"]["correlation_id"],
            self.run_answer(fixture_provider)["trace"]["correlation_id"],
        )
