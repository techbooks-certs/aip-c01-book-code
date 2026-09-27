import io
import json
import unittest
from unittest.mock import Mock
import boto3
from botocore.response import StreamingBody
from botocore.stub import Stubber, ANY
from botocore.exceptions import ClientError
from starlette.testclient import TestClient
from claimsassist.cloud_integration import parse_job, run_job, handle_batch
from claimsassist.runtime_app import create_app, RuntimeSettings

ARN = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/claimsassist-abcdefghij"
RAW = json.dumps({"request_id": "request-1", "prompt": "Explain the fictional policy"})
RESULT = {
    "status": "completed",
    "mode": "fixture",
    "stop_reason": "end_turn",
    "tool_proposals": 1,
    "successful_evidence_reads": 1,
    "quality_evaluated": False,
    "response": "Synthetic policy",
    "synthetic_data": True,
    "write_tools_available": False,
}


class ConditionalTable:
    """Behavioral fixture enforcing ownership/lease conditions; not DynamoDB."""

    def __init__(self):
        self.item = None

    def get_item(self, **kwargs):
        assert kwargs["ConsistentRead"] is True
        return {"Item": dict(self.item)} if self.item else {}

    def put_item(self, **kwargs):
        values = kwargs.get("ExpressionAttributeValues", {})
        if self.item:
            if (
                not values
                or self.item["fingerprint"] != values[":fp"]
                or self.item["state"] == "SUCCEEDED"
                or self.item["lease_until"] >= values[":now"]
            ):
                raise ClientError(
                    {"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem"
                )
        self.item = dict(kwargs["Item"])

    def update_item(self, **kwargs):
        values = kwargs["ExpressionAttributeValues"]
        if (
            self.item["owner"] != values[":owner"]
            or self.item["fingerprint"] != values[":fp"]
        ):
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem"
            )
        self.item["state"] = values.get(":done", values.get(":retry"))
        self.item["lease_until"] = 0
        if ":result" in values:
            self.item["result"] = values[":result"]


class CloudIntegrationTests(unittest.TestCase):
    def config(self, table, agent):
        return dict(
            table=table,
            agent=agent,
            runtime_arn=ARN,
            qualifier="releaseone",
            release_id="release-one",
            now=1000,
        )

    def agent(self):
        raw = json.dumps(RESULT).encode()
        a = Mock()
        a.invoke_agent_runtime.return_value = {
            "statusCode": 200,
            "contentType": "application/json",
            "response": StreamingBody(io.BytesIO(raw), len(raw)),
        }
        return a

    def test_sdk_request_contract_and_durable_duplicate(self):
        agent = boto3.client(
            "bedrock-agentcore",
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        table = ConditionalTable()
        raw = json.dumps(RESULT).encode()
        with Stubber(agent) as stub:
            stub.add_response(
                "invoke_agent_runtime",
                {
                    "statusCode": 200,
                    "contentType": "application/json",
                    "response": StreamingBody(io.BytesIO(raw), len(raw)),
                },
                {
                    "agentRuntimeArn": ARN,
                    "qualifier": "releaseone",
                    "runtimeSessionId": ANY,
                    "contentType": "application/json",
                    "accept": "application/json",
                    "payload": json.dumps(
                        {"prompt": "Explain the fictional policy"}
                    ).encode(),
                },
            )
            first = run_job(RAW, **self.config(table, agent))
            second = run_job(RAW, **self.config(table, agent))
            self.assertEqual(first, second)
            stub.assert_no_pending_responses()
        self.assertEqual(table.item["state"], "SUCCEEDED")

    def test_job_cannot_select_authority_or_duplicate_fields(self):
        for raw in [
            RAW[:-1] + ',"tenant":"other"}',
            '{"request_id":"a","request_id":"b","prompt":"x"}',
            json.dumps({"request_id": "a", "prompt": True}),
        ]:
            with self.assertRaises(ValueError):
                parse_job(raw)

    def test_conflicting_id_does_not_invoke(self):
        table = ConditionalTable()
        agent = self.agent()
        run_job(RAW, **self.config(table, agent))
        with self.assertRaises(ValueError):
            run_job(
                json.dumps({"request_id": "request-1", "prompt": "different"}),
                **self.config(table, agent),
            )
        self.assertEqual(agent.invoke_agent_runtime.call_count, 1)

    def test_active_lease_cannot_invoke_again(self):
        table = ConditionalTable()
        agent = self.agent()
        run_job(RAW, **self.config(table, agent))
        table.item["state"] = "RUNNING"
        table.item["lease_until"] = 2000
        with self.assertRaises(ClientError):
            run_job(RAW, **self.config(table, agent))
        self.assertEqual(agent.invoke_agent_runtime.call_count, 1)

    def test_runtime_failure_is_retryable_and_batch_failure_is_scoped(self):
        table = ConditionalTable()
        agent = Mock()
        agent.invoke_agent_runtime.side_effect = RuntimeError("secret")
        process = lambda raw: run_job(raw, **self.config(table, agent))
        result = handle_batch(
            {"Records": [{"messageId": "m1", "body": RAW}]}, process=process
        )
        self.assertEqual(result, {"batchItemFailures": [{"itemIdentifier": "m1"}]})
        self.assertEqual(table.item["state"], "RETRYABLE")
        self.assertNotIn("secret", json.dumps(table.item))

    def test_malformed_response_never_becomes_success(self):
        table = ConditionalTable()
        agent = self.agent()
        raw = json.dumps(dict(RESULT, write_tools_available=True)).encode()
        agent.invoke_agent_runtime.return_value["response"] = StreamingBody(
            io.BytesIO(raw), len(raw)
        )
        with self.assertRaises(ValueError):
            run_job(RAW, **self.config(table, agent))
        self.assertEqual(table.item["state"], "RETRYABLE")

    def test_sse_validated_completion_and_error_terminal(self):
        with TestClient(create_app(RuntimeSettings())) as client:
            response = client.post(
                "/invocations",
                json={"prompt": "Explain policy"},
                headers={"Accept": "text/event-stream"},
            )
            self.assertTrue(
                response.headers["content-type"].startswith("text/event-stream")
            )
            self.assertIn("event: start\n", response.text)
            self.assertIn("event: delta\n", response.text)
            self.assertIn("event: complete\n", response.text)

        def fail(*args):
            raise RuntimeError("private")

        with TestClient(create_app(RuntimeSettings(), fail)) as client:
            text = client.post(
                "/invocations",
                json={"prompt": "test"},
                headers={"Accept": "text/event-stream"},
            ).text
            self.assertIn("event: error\n", text)
            self.assertNotIn("event: complete", text)
            self.assertNotIn("private", text)

    def test_sse_accept_header_matching_is_case_insensitive(self):
        # HTTP media-type values are case-insensitive; a caller sending a
        # differently-cased Accept header must still get the streaming path,
        # matching the content-type check elsewhere in the same handler.
        with TestClient(create_app(RuntimeSettings())) as client:
            response = client.post(
                "/invocations",
                json={"prompt": "Explain policy"},
                headers={"Accept": "Text/Event-Stream"},
            )
            self.assertTrue(
                response.headers["content-type"].startswith("text/event-stream")
            )
            self.assertIn("event: complete\n", response.text)
