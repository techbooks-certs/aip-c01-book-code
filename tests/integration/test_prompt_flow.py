from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from claimsassist.baseline import BoundaryError, OutputError, ProviderError
from claimsassist.cli import FixtureClient
from claimsassist.prompt_flow import prepare, run_flow, validate_asset

ROOT = Path(__file__).resolve().parents[2]


class SequenceClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def converse(self, **request):
        self.calls.append(deepcopy(request))
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result


class PromptFlowTests(unittest.TestCase):
    def setUp(self):
        self.asset = json.loads(
            (ROOT / "prompts/ch06/claims-summary-v1.json").read_text()
        )
        self.claim = json.loads((ROOT / "data/synthetic/claim-0001.json").read_text())
        self.tenant = self.claim["tenant_id"]
        self.evidence = dict(
            tenant_id=self.tenant,
            items=[dict(source_id="policy", quote="Record the loss date.")],
        )
        self.good = FixtureClient().converse()

    def run_case(self, client=None, **kwargs):
        return run_flow(
            self.asset,
            self.claim,
            self.evidence,
            self.tenant,
            client or FixtureClient(),
            **kwargs,
        )

    def test_valid_release_trace(self):
        result = self.run_case()
        self.assertEqual(result["status"], "draft")
        self.assertEqual(result["prompt_version"], 1)
        self.assertEqual(result["prompt_sha256"], validate_asset(self.asset))
        self.assertTrue(result["review_required"])

    def test_schema_regression_rejected_before_provider(self):
        self.asset["output_fields"] = ["summary"]
        client = SequenceClient([])
        with self.assertRaises(BoundaryError):
            self.run_case(client)
        self.assertEqual(client.calls, [])

    def test_unbound_template_variable_rejected(self):
        self.asset["template"] += " ${unknown}"
        with self.assertRaises(BoundaryError):
            self.run_case()

    def test_missing_evidence_variable_rejected(self):
        self.asset["template"] = "${claim_json}"
        with self.assertRaises(BoundaryError):
            self.run_case()

    def test_malformed_template_rejected(self):
        self.asset["template"] += " ${broken"
        with self.assertRaises(BoundaryError):
            self.run_case()

    def test_literal_variable_text_in_data_is_not_rebound(self):
        self.claim["description"] = (
            "Literal ${unknown} and ${claim_json} remain evidence text."
        )
        request, _ = prepare(self.asset, self.claim, self.evidence, self.tenant)
        body = request["messages"][0]["content"][0]["text"]
        self.assertIn("${unknown}", body)
        self.assertEqual(request["system"][0]["text"], self.asset["system"])
        self.assertNotIn("${unknown}", request["system"][0]["text"])

    def test_cross_tenant_claim_and_evidence_stop_before_provider(self):
        for target in ("claim", "evidence"):
            with self.subTest(target=target):
                obj = self.claim if target == "claim" else self.evidence
                old = obj["tenant_id"]
                obj["tenant_id"] = "tenant-other"
                client = SequenceClient([])
                with self.assertRaises(BoundaryError):
                    self.run_case(client)
                self.assertEqual(client.calls, [])
                obj["tenant_id"] = old

    def test_empty_evidence_clarifies_without_provider(self):
        self.evidence["items"] = []
        client = SequenceClient([])
        result = self.run_case(client)
        self.assertEqual(result["status"], "clarification_required")
        self.assertEqual(result["attempts"], 0)
        self.assertEqual(client.calls, [])

    def test_bounded_repair_then_success(self):
        bad = deepcopy(self.good)
        bad["output"]["message"]["content"] = [{"text": "sensitive invalid raw output"}]
        client = SequenceClient([bad, self.good])
        result = self.run_case(client)
        self.assertEqual(result["attempts"], 2)
        self.assertNotIn("sensitive invalid raw output", json.dumps(client.calls[1]))

    def test_repair_exhaustion_stops_at_two(self):
        bad = deepcopy(self.good)
        bad["output"]["message"]["content"] = [{"text": "bad"}]
        client = SequenceClient([bad, bad, self.good])
        with self.assertRaises(OutputError):
            self.run_case(client)
        self.assertEqual(len(client.calls), 2)

    def test_intervention_never_repaired(self):
        bad = deepcopy(self.good)
        bad["stopReason"] = "guardrail_intervened"
        client = SequenceClient([bad, self.good])
        with self.assertRaises(OutputError):
            self.run_case(client)
        self.assertEqual(len(client.calls), 1)

    def test_provider_exception_is_sanitized_and_not_repaired(self):
        client = SequenceClient([RuntimeError("secret provider payload"), self.good])
        with self.assertRaises(ProviderError) as caught:
            self.run_case(client)
        self.assertNotIn("secret", str(caught.exception))
        self.assertEqual(len(client.calls), 1)

    def test_invalid_attempt_and_metadata_values(self):
        for value in (0, 3, True):
            with self.subTest(value=value), self.assertRaises(BoundaryError):
                self.run_case(max_attempts=value)
        self.asset["version"] = True
        with self.assertRaises(BoundaryError):
            self.run_case()

    def test_evidence_and_rendered_size_bounds(self):
        self.evidence["items"] = [dict(source_id="policy", quote="x" * 4000)] * 4
        with self.assertRaises(BoundaryError):
            self.run_case()
        self.evidence["items"] = [
            dict(source_id="policy", quote="x", extra="unsupported")
        ]
        with self.assertRaises(BoundaryError):
            self.run_case()

    def test_prompt_metadata_snapshot_not_mutated_by_client(self):
        owner = self

        class MutatingClient:
            def converse(self, **request):
                owner.asset["version"] = 99
                return owner.good

        result = self.run_case(MutatingClient())
        self.assertEqual(result["prompt_version"], 1)

    def test_cli_regression_rejection_and_output_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            base = [
                sys.executable,
                "-m",
                "claimsassist.prompt_flow",
                "--claim",
                str(ROOT / "data/synthetic/claim-0001.json"),
                "--output",
                str(path),
            ]
            bad = base + [
                "--asset",
                str(ROOT / "prompts/ch06/claims-summary-v2-regressive.json"),
            ]
            self.assertEqual(subprocess.run(bad, capture_output=True).returncode, 2)
            self.assertFalse(path.exists())
            good = base + ["--asset", str(ROOT / "prompts/ch06/claims-summary-v1.json")]
            result = subprocess.run(good, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            original = path.read_bytes()
            self.assertEqual(subprocess.run(good, capture_output=True).returncode, 2)
            self.assertEqual(path.read_bytes(), original)
