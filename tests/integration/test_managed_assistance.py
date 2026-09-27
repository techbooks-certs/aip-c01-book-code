"""Joined dependency-injected path; no credentials, model or cloud execution."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from claimsassist.governance import SecurityContext
from claimsassist.managed_assistance import (
    AssistanceConfig,
    FixtureRuntime,
    FixtureRetrieval,
    assist,
    demonstrate,
    main as managed_assistance_main,
)


class JoinedAssistanceTests(unittest.TestCase):
    def setup_args(self):
        self.audit = []
        return dict(
            context=SecurityContext(
                "tenant-amber", "adjuster-demo", frozenset({"adjuster"})
            ),
            resource_tenant="tenant-amber",
            resource_id="claim-0001",
            config=AssistanceConfig(
                "ABCDEFGHIJ",
                "guardrail-demo",
                "1",
                "fixture-model",
                "draft-v1",
                "policy-v1",
            ),
            retrieval_client=FixtureRetrieval(),
            runtime_client=FixtureRuntime(),
            source_allowed=lambda *args: True,
            audit_sink=self.audit.append,
        )

    def test_joined_pipeline_keeps_review_and_provenance(self):
        report = demonstrate()
        self.assertEqual(report["status"], "draft_for_review")
        self.assertEqual(report["answer"]["semantic_support"], "NOT_EVALUATED")
        self.assertTrue(report["audit_persisted"])
        self.assertFalse(report["business_write_available"])
        self.assertEqual(report["evidence"][0]["source_version"], "v1")
        self.assertIn("model", [e["stage"] for e in report["trace"]["events"]])
        self.assertIsNone(report["trace"]["first_failure"])

    def test_cross_tenant_stops_before_any_managed_dependency(self):
        class Never:
            def __getattr__(self, name):
                raise AssertionError("Dependency accessed")

        args = self.setup_args()
        args.update(
            resource_tenant="tenant-birch",
            runtime_client=Never(),
            retrieval_client=Never(),
        )
        report = assist("Synthetic request", **args)
        self.assertEqual(report["failure_boundary"], "auth")
        self.assertIsNone(report["answer"])
        self.assertEqual(len(self.audit), 1)

    def test_input_intervention_never_retrieves(self):
        class Blocked(FixtureRuntime):
            def apply_guardrail(self, **request):
                return {"action": "GUARDRAIL_INTERVENED"}

        class Never:
            def retrieve(self, **request):
                raise AssertionError("Retrieval accessed")

        args = self.setup_args()
        args.update(runtime_client=Blocked(), retrieval_client=Never())
        report = assist("Synthetic request", **args)
        self.assertEqual(report["failure_boundary"], "prompt")
        self.assertFalse(
            any(e["stage"] == "retrieval" for e in report["trace"]["events"])
        )

    def test_empty_retrieval_abstains_without_model(self):
        class Empty:
            def retrieve(self, **request):
                return {"retrievalResults": []}

        class NoModel(FixtureRuntime):
            def converse(self, **request):
                raise AssertionError("Model accessed")

        args = self.setup_args()
        args.update(retrieval_client=Empty(), runtime_client=NoModel())
        report = assist("Synthetic request", **args)
        self.assertEqual(report["status"], "abstain")
        self.assertFalse(any(e["stage"] == "model" for e in report["trace"]["events"]))

    def test_permission_revocation_after_generation_withholds_answer(self):
        args = self.setup_args()
        checks = []

        def allowed(*values):
            checks.append(values)
            return len(checks) < 3

        args["source_allowed"] = allowed
        report = assist("Synthetic request", **args)
        self.assertEqual(len(checks), 3)
        self.assertEqual(report["failure_boundary"], "auth")
        self.assertIsNone(report["answer"])
        self.assertEqual(report["evidence"], [])

    def test_output_intervention_withholds_draft(self):
        class OutputBlocked(FixtureRuntime):
            def apply_guardrail(self, **request):
                return {
                    "action": "GUARDRAIL_INTERVENED"
                    if request["source"] == "OUTPUT"
                    else "NONE"
                }

        args = self.setup_args()
        args["runtime_client"] = OutputBlocked()
        report = assist("Synthetic request", **args)
        self.assertEqual(report["failure_boundary"], "response")
        self.assertIsNone(report["answer"])

    def test_audit_failure_withholds_answer_and_marks_trace(self):
        args = self.setup_args()

        def fail(record):
            raise RuntimeError("PRIVATE_AUDIT_SENTINEL")

        args["audit_sink"] = fail
        report = assist("Synthetic request", **args)
        self.assertEqual(report["failure_boundary"], "audit")
        self.assertFalse(report["audit_persisted"])
        self.assertIsNotNone(report["trace"]["first_failure"])
        self.assertNotIn("PRIVATE_AUDIT_SENTINEL", json.dumps(report))

    def test_oversize_complete_context_fails_before_model(self):
        args = self.setup_args()
        args["config"] = AssistanceConfig(
            "ABCDEFGHIJ",
            "guardrail-demo",
            "1",
            "fixture-model",
            "draft-v1",
            "policy-v1",
            1,
        )
        report = assist("Synthetic request", **args)
        self.assertEqual(report["failure_boundary"], "prompt")
        self.assertFalse(any(e["stage"] == "model" for e in report["trace"]["events"]))

    def test_private_content_is_not_in_audit_or_trace(self):
        args = self.setup_args()
        report = assist("PRIVATE_QUESTION_SENTINEL", **args)
        safe = json.dumps({"audit": report["audit_records"], "trace": report["trace"]})
        self.assertNotIn("PRIVATE_QUESTION_SENTINEL", safe)
        self.assertNotIn("Fictional policy:", safe)
        self.assertEqual(report["audit_records"][0]["actor_role"], "adjuster")

    def test_model_failure_is_sanitized_and_localized(self):
        class Failed(FixtureRuntime):
            def converse(self, **request):
                raise RuntimeError("PRIVATE_PROVIDER_SENTINEL")

        args = self.setup_args()
        args["runtime_client"] = Failed()
        report = assist("Synthetic request", **args)
        self.assertEqual(report["failure_boundary"], "model")
        self.assertNotIn("PRIVATE_PROVIDER_SENTINEL", json.dumps(report))

    def test_document_intervention_stops_before_model(self):
        class DocumentBlocked(FixtureRuntime):
            def __init__(self):
                self.checks = 0

            def apply_guardrail(self, **request):
                self.checks += 1
                return {
                    "action": "GUARDRAIL_INTERVENED" if self.checks == 2 else "NONE"
                }

        args = self.setup_args()
        args["runtime_client"] = DocumentBlocked()
        report = assist("Synthetic request", **args)
        self.assertEqual(report["failure_boundary"], "prompt")
        self.assertFalse(any(e["stage"] == "model" for e in report["trace"]["events"]))

    def test_fabricated_citation_is_withheld(self):
        class Invalid(FixtureRuntime):
            def converse(self, **request):
                result = {
                    "claims": [
                        {"text": "Authored invalid draft", "citations": ["invented"]}
                    ],
                    "abstained": False,
                }
                return {
                    "stopReason": "end_turn",
                    "output": {"message": {"content": [{"text": json.dumps(result)}]}},
                }

        args = self.setup_args()
        args["runtime_client"] = Invalid()
        report = assist("Synthetic request", **args)
        self.assertEqual(report["failure_boundary"], "response")
        self.assertIsNone(report["answer"])

    def test_audit_configuration_binds_guardrail_identity_and_version(self):
        from dataclasses import replace

        args = self.setup_args()
        base = assist("Synthetic request", **args)["audit_records"][0][
            "configuration_ref"
        ]
        for changed in [
            replace(args["config"], guardrail_id="different-guardrail"),
            replace(args["config"], guardrail_version="2"),
        ]:
            candidate = assist("Synthetic request", **dict(args, config=changed))
            self.assertNotEqual(
                base, candidate["audit_records"][0]["configuration_ref"]
            )
        again = assist("Synthetic request", **args)
        self.assertEqual(base, again["audit_records"][0]["configuration_ref"])


class ManagedAssistanceCliTests(unittest.TestCase):
    """Regression: main() must not leave an unhandled traceback on a rerun.

    demonstrate() runs entirely against in-process fixtures and does not
    raise for any input it is given here, but main() previously ran it
    inside the exclusive-create block with no upfront guard -- so a reader
    rerunning the documented command a second time at the same --output
    path got a bare FileExistsError traceback (wrong exit code, no
    guidance) instead of the clean parser.error() message every sibling
    CLI gives (index_demo.py, capstone.py, deployment_cli.py).
    """

    def run_main(self, argv):
        with patch("sys.argv", ["managed_assistance.py"] + argv):
            managed_assistance_main()

    def test_first_run_succeeds_and_second_run_at_same_path_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "assistance.json"
            argv = ["--output", str(out)]
            self.run_main(argv)
            self.assertTrue(out.exists())
            saved = json.loads(out.read_text())
            self.assertEqual(saved["scope"], "executed_local_joined_sdk_shaped_fixtures")
            with self.assertRaises(SystemExit) as caught:
                self.run_main(argv)
            self.assertEqual(caught.exception.code, 2)

    def test_missing_parent_directory_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "missing-dir" / "assistance.json"
            with self.assertRaises(SystemExit) as caught:
                self.run_main(["--output", str(out)])
            self.assertEqual(caught.exception.code, 2)
            self.assertFalse(out.exists())
