import unittest
from unittest.mock import patch
from starlette.testclient import TestClient
from claimsassist.baseline import BoundaryError
from claimsassist.runtime_app import RuntimeSettings, create_app, run_agent
from claimsassist.agent_lab import ScriptedModel


class RuntimeAppTests(unittest.TestCase):
    def test_ping_and_scripted_strands_invocation(self):
        with TestClient(create_app(RuntimeSettings())) as client:
            self.assertEqual(client.get("/ping").json(), {"status": "Healthy"})
            response = client.post(
                "/invocations", json={"prompt": "Explain the fictional exclusion"}
            )
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()["write_tools_available"])
            self.assertEqual(response.json()["tool_proposals"], 1)
            self.assertEqual(response.json()["mode"], "fixture")

    def test_tenant_and_model_cannot_be_selected_by_request(self):
        calls = []

        def runner(*args):
            calls.append(args)
            return {}

        with TestClient(create_app(RuntimeSettings(), runner)) as client:
            for field in ["tenant_id", "model_id", "approval", "session_id"]:
                self.assertEqual(
                    client.post(
                        "/invocations", json={"prompt": "test", field: "untrusted"}
                    ).status_code,
                    400,
                )
        self.assertEqual(calls, [])

    def test_duplicates_invalid_unicode_and_oversize_fail_before_agent(self):
        with TestClient(
            create_app(RuntimeSettings(), lambda *args: self.fail("agent called"))
        ) as client:
            for raw in [
                b'{"prompt":"a","prompt":"b"}',
                b"\xff",
                b'{"prompt":true}',
                b"NaN",
                b"[]",
            ]:
                self.assertEqual(
                    client.post(
                        "/invocations",
                        content=raw,
                        headers={"content-type": "application/json"},
                    ).status_code,
                    400,
                )
            self.assertEqual(
                client.post(
                    "/invocations",
                    content=b"a" * 8193,
                    headers={"content-type": "application/json"},
                ).status_code,
                413,
            )

    def test_wrong_content_type_does_not_call_agent(self):
        with TestClient(
            create_app(RuntimeSettings(), lambda *args: self.fail("agent called"))
        ) as client:
            self.assertEqual(client.post("/invocations", content="hi").status_code, 415)

    def test_provider_error_is_sanitized(self):
        def broken(*args):
            raise RuntimeError("private payload and secret")

        with TestClient(create_app(RuntimeSettings(), broken)) as client:
            response = client.post("/invocations", json={"prompt": "sensitive"})
            self.assertEqual(response.status_code, 502)
            self.assertEqual(response.json(), {"error": "agent_execution_failed"})

    def test_invalid_startup_configuration_fails(self):
        for args in [
            ("unknown", "", ""),
            ("bedrock", "", "us-east-1"),
            ("fixture", "model", ""),
        ]:
            with self.subTest(args=args), self.assertRaises(BoundaryError):
                RuntimeSettings(*args)

    def test_repeated_invocations_do_not_share_scripted_state(self):
        with TestClient(create_app(RuntimeSettings())) as client:
            first = client.post("/invocations", json={"prompt": "first"}).json()
            second = client.post("/invocations", json={"prompt": "second"}).json()
            self.assertEqual(first["tool_proposals"], 1)
            self.assertEqual(second["tool_proposals"], 1)

    def test_model_cannot_add_arguments_to_fixed_policy_tool(self):
        model = ScriptedModel(
            [
                {"tool": "read_fictional_policy", "input": {"tenant": "tenant-violet"}},
                {"text": "done"},
            ]
        )
        with patch(
            "claimsassist.runtime_app.read_policy",
            side_effect=AssertionError("Unauthorized tool executed"),
        ):
            with self.assertRaises(BoundaryError):
                run_agent("test", RuntimeSettings(), model)

    def test_answer_without_successful_evidence_read_is_withheld(self):
        with self.assertRaises(BoundaryError):
            run_agent(
                "test",
                RuntimeSettings(),
                ScriptedModel([{"text": "Unsupported answer"}]),
            )
        with patch("claimsassist.runtime_app.read_policy", return_value=[]):
            with self.assertRaises(BoundaryError):
                run_agent(
                    "test",
                    RuntimeSettings(),
                    ScriptedModel(
                        [
                            {"tool": "read_fictional_policy", "input": {}},
                            {"text": "Unsupported answer"},
                        ]
                    ),
                )
