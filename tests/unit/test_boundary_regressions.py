"""Regression checks for approval, runtime, evaluation and cache boundaries."""

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from starlette.testclient import TestClient
from claimsassist.approval import ApprovalLedger
from claimsassist.baseline import BoundaryError
from claimsassist.evaluation import release_decision
from claimsassist.optimization import safe_cache_key
from claimsassist.runtime_app import RuntimeSettings, run_agent, create_app
from claimsassist.agent_lab import ScriptedModel


class DeveloperRegressions(unittest.TestCase):
    def test_approval_rejects_each_changed_authority_version(self):
        with TemporaryDirectory() as directory:
            ledger = ApprovalLedger(Path(directory) / "ledger.db")
            binding = dict(
                source_version="v1", policy_version="p1", permission_epoch="e1"
            )
            p = ledger.propose(
                "tenant-amber",
                "claim-0001",
                "request-1",
                "pol-4821",
                "Review",
                1000,
                **binding,
            )
            ledger.approve(
                p["proposal_id"],
                "tenant-amber",
                p["fingerprint"],
                "adjuster-one",
                "adjuster",
                1001,
            )
            current = {"current_" + k: v for k, v in binding.items()}
            for key in current:
                changed = dict(current)
                changed[key] = "changed"
                with self.subTest(key=key), self.assertRaises(BoundaryError):
                    ledger.execute_simulation(
                        p["proposal_id"],
                        "tenant-amber",
                        "claim-0001",
                        "open",
                        "adjuster",
                        1002,
                        current_source_allowed=True,
                        **changed,
                    )
            for key in binding:
                changed = dict(binding)
                changed[key] = "changed"
                with self.subTest(reuse=key), self.assertRaises(BoundaryError):
                    ledger.propose(
                        "tenant-amber",
                        "claim-0001",
                        "request-1",
                        "pol-4821",
                        "Review",
                        1000,
                        **changed,
                    )
            receipt = ledger.execute_simulation(
                p["proposal_id"],
                "tenant-amber",
                "claim-0001",
                "open",
                "adjuster",
                1002,
                current_source_allowed=True,
                **current,
            )
            self.assertTrue(receipt["simulation_only"])

    def test_exhausted_runtime_is_http_failure(self):
        def runner(prompt, settings):
            return run_agent(
                prompt,
                settings,
                ScriptedModel([{"tool": "read_fictional_policy", "input": {}}] * 6),
            )

        with TestClient(create_app(RuntimeSettings(), runner)) as client:
            response = client.post("/invocations", json={"prompt": "test"})
            self.assertEqual(response.status_code, 502)
            self.assertEqual(response.json(), {"error": "agent_execution_failed"})

    def test_other_incomplete_stops_and_empty_answer_rejected(self):
        class Result:
            def __init__(self, reason, text):
                self.stop_reason = reason
                self.text = text

            def __str__(self):
                return self.text

        for reason, text in [
            ("max_tokens", "partial"),
            ("guardrail_intervened", "blocked"),
            ("limit_turns", "partial"),
            ("end_turn", ""),
        ]:
            with (
                self.subTest(reason=reason),
                patch("claimsassist.runtime_app.Agent") as cls,
            ):
                cls.return_value.return_value = Result(reason, text)
                with self.assertRaises(BoundaryError):
                    run_agent("test", RuntimeSettings())

    def test_invalid_scorecard_is_rejected_before_release(self):
        good = dict(
            cases=1,
            critical_failures=0,
            acceptance_rate=1,
            evidence_coverage=1,
            mean_cost=0,
            max_latency_ms=1,
            subgroup_counts={"all": 1},
            subgroup_acceptance={"all": 1},
        )
        thresholds = dict(
            min_acceptance=1, min_evidence=1, max_cost=0, max_latency=1, min_subgroup=1
        )
        bads = [
            ("cases", True),
            ("critical_failures", False),
            ("critical_failures", 2),
            ("acceptance_rate", 2),
            ("acceptance_rate", "NaN"),
            ("evidence_coverage", -1),
            ("mean_cost", -1),
            ("mean_cost", "Infinity"),
            ("max_latency_ms", -1),
            ("subgroup_acceptance", {"all": 2}),
        ]
        for key, value in bads:
            card = dict(good)
            card[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(BoundaryError):
                release_decision(card, **thresholds)
        serialized = dict(
            good,
            acceptance_rate="1",
            evidence_coverage="1",
            mean_cost="0",
            subgroup_acceptance={"all": "1"},
        )
        self.assertEqual(
            release_decision(serialized, **thresholds)["decision"], "APPROVE"
        )
        self.assertEqual(serialized["mean_cost"], "0")

    def test_cache_permission_epoch_and_scope_are_distinct(self):
        args = ("tenant-amber", "v1", "p1", "model1", "queryhash")
        base = safe_cache_key(
            *args, authorization_scope="scope1", permission_epoch="epoch1"
        )
        self.assertNotEqual(
            base,
            safe_cache_key(
                *args, authorization_scope="scope2", permission_epoch="epoch1"
            ),
        )
        self.assertNotEqual(
            base,
            safe_cache_key(
                *args, authorization_scope="scope1", permission_epoch="epoch2"
            ),
        )
        with self.assertRaises(BoundaryError):
            safe_cache_key(*args, authorization_scope="", permission_epoch="epoch1")
