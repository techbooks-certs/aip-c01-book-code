from tests.network_support import portable_socketpairs
import asyncio
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from claimsassist.approval import ApprovalLedger

try:
    from claimsassist.agent_lab import (
        RequestContext,
        ScriptedModel,
        create_agent,
        demonstration,
    )

    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False


@unittest.skipUnless(
    SDK_AVAILABLE, "Install the pinned agent SDK lock to run these checks"
)
class AgentSDKTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "ledger.sqlite"
        self.ledger = ApprovalLedger(self.path)
        self.context = RequestContext("tenant-amber", "claim-0001", "2026-09-20")

    def invoke(self, steps, limit=5, **kwargs):
        model = ScriptedModel(steps)
        agent, audit = create_agent(
            self.context, self.ledger, model, clock=lambda: 1000, **kwargs
        )
        # Block network connections while the actual SDK runs the scripted provider.
        with portable_socketpairs(), patch.object(
            socket.socket,
            "connect",
            side_effect=AssertionError("Network forbidden in SDK fixture"),
        ):
            result = agent("Execute the fixture sequence.", limits={"turns": limit})
        results = [
            c["toolResult"]
            for m in agent.messages
            for c in m["content"]
            if "toolResult" in c
        ]
        return result, model, audit, results, agent

    def test_full_sdk_demo_without_network(self):
        with portable_socketpairs(), patch.object(
            socket.socket, "connect", side_effect=AssertionError("Network forbidden")
        ):
            report = demonstration(self.path)
        self.assertEqual(report["model_calls"], 4)
        self.assertEqual(report["stop_reason"], "end_turn")
        self.assertEqual(
            sum(x["event"] == "proposal_recorded" for x in report["audit"]), 1
        )

    def test_malformed_argument_blocked_before_calculation(self):
        _, _, audit, results, _ = self.invoke(
            [dict(tool="sum_amounts_cents", input={"amounts_cents": [True]})]
        )
        self.assertFalse(audit[0]["allowed"])
        self.assertEqual(results[0]["status"], "error")

    def test_extra_tenant_argument_blocked(self):
        _, _, audit, results, _ = self.invoke(
            [
                dict(
                    tool="lookup_policy",
                    input={"source_id": "pol-4821", "tenant": "tenant-birch"},
                )
            ]
        )
        self.assertFalse(audit[0]["allowed"])
        self.assertNotIn("Private Birch", json.dumps(results))

    def test_cross_tenant_text_never_returned(self):
        _, _, _, results, _ = self.invoke(
            [dict(tool="lookup_policy", input={"source_id": "pol-4821"})]
        )
        text = json.dumps(results)
        self.assertIn("corrosion", text)
        self.assertNotIn("Private Birch", text)

    def test_revoked_source_not_found(self):
        _, _, _, results, _ = self.invoke(
            [dict(tool="lookup_policy", input={"source_id": "revoked"})]
        )
        self.assertIn("not_found", json.dumps(results))
        self.assertNotIn("Revoked private", json.dumps(results))

    def test_cooperative_timeout_has_typed_result(self):
        async def waiting(source):
            await asyncio.Event().wait()

        _, _, audit, results, _ = self.invoke(
            [dict(tool="lookup_policy", input={"source_id": "pol-4821"})],
            lookup_backend=waiting,
            timeout=0.01,
        )
        self.assertTrue(any(x["event"] == "lookup_timeout" for x in audit))
        self.assertIn("timeout", json.dumps(results))

    def test_tool_budget_blocks_later_call(self):
        step = dict(tool="sum_amounts_cents", input={"amounts_cents": [100, 200]})
        _, _, audit, results, _ = self.invoke([step, step], tool_budget=1)
        self.assertEqual([a["allowed"] for a in audit], [True, False])
        self.assertEqual([r["status"] for r in results], ["success", "error"])

    def test_turn_limit_stops_loop(self):
        step = dict(tool="sum_amounts_cents", input={"amounts_cents": [100]})
        result, model, _, _, _ = self.invoke([step] * 10, limit=2)
        self.assertEqual(model.calls, 2)
        self.assertNotEqual(result.stop_reason, "end_turn")

    def test_repeated_action_is_one_pending_proposal(self):
        step = dict(
            tool="propose_evidence_request",
            input={
                "request_id": "request-1",
                "source_id": "pol-4821",
                "message": "Provide photographs.",
            },
        )
        _, _, _, results, _ = self.invoke([step, step])
        data = [json.loads(r["content"][0]["text"]) for r in results]
        self.assertEqual(data[0], data[1])
        self.assertEqual(data[0]["state"], "PENDING")

    def test_agent_cannot_access_approval_or_execution_tools(self):
        _, _, _, results, agent = self.invoke([dict(tool="approve", input={})])
        self.assertEqual(results[0]["status"], "error")
        self.assertEqual(
            set(agent.tool_names),
            {"lookup_policy", "sum_amounts_cents", "propose_evidence_request"},
        )

    def test_integer_cent_total(self):
        _, _, _, results, _ = self.invoke(
            [dict(tool="sum_amounts_cents", input={"amounts_cents": [1250, 2750]})]
        )
        self.assertEqual(
            json.loads(results[0]["content"][0]["text"])["total_cents"], 4000
        )

    def test_wrong_argument_type_and_negative_values(self):
        for values in [["100"], [-1], [1.5]]:
            with self.subTest(values=values):
                _, _, audit, results, _ = self.invoke(
                    [dict(tool="sum_amounts_cents", input={"amounts_cents": values})]
                )
                self.assertFalse(audit[0]["allowed"])
                self.assertEqual(results[0]["status"], "error")
