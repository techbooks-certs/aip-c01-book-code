import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import boto3
from botocore.stub import Stubber, ANY
from claimsassist.optimization_benchmark import (
    benchmark,
    fixed_cases,
    LocalProvider,
    LiveProvider,
    main,
)


class OptimizationBenchmarkTests(unittest.TestCase):
    def test_actual_selection_changes_request_context(self):
        calls = []
        local = LocalProvider()

        def provider(request, model):
            calls.append((request, model))
            return local(request, model)

        report = benchmark(provider)
        self.assertEqual(len(report["rows"]), 20)
        self.assertEqual(len(calls), 19)
        base = [r for r in report["rows"] if r["candidate"] == "baseline"]
        selected = [r for r in report["rows"] if r["candidate"] == "selected_context"]
        self.assertTrue(
            all(
                b["context_characters"] > s["context_characters"]
                for b, s in zip(base, selected)
            )
        )
        self.assertEqual(selected[0]["context_sources"], ["policy", "exception"])
        self.assertTrue(all(r["reference_probe_pass"] for r in selected))

    def test_cold_warm_source_and_permission_invalidation(self):
        report = benchmark(LocalProvider())
        rows = [r for r in report["rows"] if r["candidate"] == "response_cache"]
        self.assertEqual(
            [r["cache_hit"] for r in rows], [False, False, True, False, False]
        )
        self.assertEqual(rows[2]["provider_calls"], 0)
        self.assertEqual(rows[2]["attempt_usage"]["input_units"], 0)
        self.assertEqual(rows[0]["output_sha256"], rows[2]["output_sha256"])
        self.assertNotEqual(rows[0]["output_sha256"], rows[3]["output_sha256"])

    def test_unapproved_efficient_route_never_executes(self):
        report = benchmark(LocalProvider(), eligible_models=frozenset({"primary"}))
        self.assertTrue(all(r["model_route"] == "primary" for r in report["rows"]))

    def test_complex_case_keeps_primary_route(self):
        rows = [
            r
            for r in benchmark(LocalProvider())["rows"]
            if r["candidate"] == "eligible_routing"
        ]
        self.assertEqual(
            [r["model_route"] for r in rows],
            ["efficient", "primary", "efficient", "efficient", "efficient"],
        )
        self.assertTrue(rows[1]["reference_probe_pass"])

    def test_revocation_rejects_before_cache_or_provider(self):
        cases = fixed_cases()
        cases[2]["source_allowed"] = False
        report = benchmark(LocalProvider(), cases=cases)
        rows = [r for r in report["rows"] if r["case_id"] == "simple-warm"]
        self.assertTrue(
            all(
                r["status"] == "FAILED"
                and r["provider_calls"] == 0
                and not r["cache_hit"]
                for r in rows
            )
        )

    def test_failed_attempts_preserved_and_not_cached(self):
        def fail(*args):
            raise TimeoutError("private provider error")

        report = benchmark(fail)
        self.assertEqual(len(report["rows"]), 20)
        self.assertTrue(
            all(
                r["status"] == "FAILED"
                and r["provider_calls"] == 1
                and r["attempt_usage"] is None
                for r in report["rows"]
            )
        )
        self.assertTrue(all(not s["usage_complete"] for s in report["summaries"]))
        self.assertNotIn("private provider error", json.dumps(report))

    def test_independent_labels_catch_wrong_output_without_quality_claim(self):
        def wrong(request, model):
            return {
                "text": "Coverage is approved automatically.",
                "usage": {
                    "input_units": 1,
                    "output_units": 1,
                    "unit": "whitespace_separated_units_not_tokens",
                },
                "model": model,
            }

        report = benchmark(wrong)
        self.assertTrue(all(not r["reference_probe_pass"] for r in report["rows"]))
        self.assertFalse(report["semantic_quality_evaluated"])
        self.assertEqual(report["release_decision"], "NOT_APPROVED")

    def test_live_provider_uses_actual_sdk_usage_without_calling_aws(self):
        client = boto3.client(
            "bedrock-runtime",
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        response = {
            "stopReason": "end_turn",
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"text": "Synthetic stub answer"}],
                }
            },
            "usage": {"inputTokens": 37, "outputTokens": 9, "totalTokens": 46},
            "metrics": {"latencyMs": 10},
        }
        with Stubber(client) as stub:
            stub.add_response(
                "converse",
                response,
                {
                    "modelId": "test-model",
                    "system": ANY,
                    "messages": ANY,
                    "inferenceConfig": {"maxTokens": 512, "temperature": 0},
                },
            )
            result = LiveProvider(client, {"primary": "test-model"})(
                {"documents": []}, "primary"
            )
            stub.assert_no_pending_responses()
        self.assertEqual(
            result["usage"],
            {"input_units": 37, "output_units": 9, "unit": "provider_reported_tokens"},
        )

    def test_provider_model_or_usage_contract_failure_is_recorded(self):
        for result in [
            {"text": "text", "usage": {}, "model": "primary"},
            {
                "text": "text",
                "usage": {
                    "input_units": True,
                    "output_units": 0,
                    "unit": "provider_reported_tokens",
                },
                "model": "primary",
            },
            {
                "text": "text",
                "usage": {
                    "input_units": 0,
                    "output_units": 0,
                    "unit": "provider_reported_tokens",
                },
                "model": "unapproved",
            },
        ]:
            with self.subTest(result=result):
                report = benchmark(lambda *args: result, cases=fixed_cases()[:1])
                self.assertTrue(all(r["status"] == "FAILED" for r in report["rows"]))


class OptimizationBenchmarkCliTests(unittest.TestCase):
    def run_main(self, argv):
        with patch("sys.argv", ["optimization_benchmark.py"] + argv):
            main()

    def test_failed_live_client_construction_does_not_leave_a_reserved_output_stub(
        self,
    ):
        # main() reserves --output before constructing the live boto3 client.
        # A bad --profile makes client construction itself raise (before
        # benchmark() ever runs); that must not leave a permanent empty file
        # blocking a retry at the same path.
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bench.json"
            argv = [
                "--output",
                str(out),
                "--live",
                "--acknowledge-charges",
                "--profile",
                "nonexistent-profile-xyz",
                "--region",
                "us-east-1",
                "--primary-model",
                "m1",
                "--efficient-model",
                "m2",
            ]
            with self.assertRaises(Exception):
                self.run_main(argv)
            self.assertFalse(out.exists())
            # Retrying at the same path must fail on the same underlying
            # error, not on "file already exists".
            with self.assertRaises(Exception):
                self.run_main(argv)
            self.assertFalse(out.exists())

    def test_offline_run_still_succeeds_and_writes_a_real_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bench.json"
            self.run_main(["--output", str(out)])
            self.assertTrue(out.exists())
            report = json.loads(out.read_text())
            self.assertEqual(report["scope"], "executed_local_provider")

    def test_existing_output_path_fails_cleanly_before_any_work(self):
        # A prior successful run (or any pre-existing file) at --output must
        # be rejected with a clear parser.error before the offline benchmark
        # ever runs, not with a bare FileExistsError traceback from the
        # exclusive-create reservation.
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bench.json"
            out.write_text("{}")
            with self.assertRaises(SystemExit) as caught:
                self.run_main(["--output", str(out)])
            self.assertEqual(caught.exception.code, 2)
