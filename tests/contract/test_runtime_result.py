"""Real SDK envelopes carrying invalid application JSON must fail closed."""

import io
import json
import unittest
import boto3
from botocore.response import StreamingBody
from botocore.stub import Stubber
from claimsassist.baseline import BoundaryError
from claimsassist.runtime_client import invoke
from claimsassist.cloud_integration import run_job
from tests.integration.test_cloud_integration import RESULT, ARN, RAW


class RuntimeResultContracts(unittest.TestCase):
    def setUp(self):
        args = dict(
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        self.agent = boto3.client("bedrock-agentcore", **args)
        self.table = boto3.resource("dynamodb", **args).Table("claims-results")

    def body(self, raw):
        return dict(
            statusCode=200,
            contentType="application/json",
            response=StreamingBody(io.BytesIO(raw), len(raw)),
        )

    def test_invalid_application_payloads_rejected_by_client_and_worker(self):
        cases = [
            {k: v for k, v in RESULT.items() if k != "response"},
            dict(RESULT, response=" "),
            dict(RESULT, response="x" * 16001),
            dict(RESULT, write_tools_available=True),
            dict(RESULT, synthetic_data=1),
            dict(RESULT, stop_reason="max_tokens"),
            dict(RESULT, stop_reason="tool_use"),
            dict(RESULT, successful_evidence_reads=0),
            dict(RESULT, tool_proposals=True),
            dict(RESULT, successful_evidence_reads=2),
            dict(RESULT, quality_evaluated=True),
        ]
        raw_cases = [json.dumps(c).encode() for c in cases]
        raw_cases.append(json.dumps(RESULT).encode()[:-1] + b',"response":"duplicate"}')
        for raw in raw_cases:
            with self.subTest(raw=raw[:100]), Stubber(self.agent) as a:
                a.add_response("invoke_agent_runtime", self.body(raw))
                with self.assertRaises(BoundaryError):
                    invoke(
                        self.agent, runtime_arn=ARN, prompt="test", session_id="a" * 36
                    )
                a.assert_no_pending_responses()
            with (
                self.subTest(worker=raw[:100]),
                Stubber(self.agent) as a,
                Stubber(self.table.meta.client) as d,
            ):
                d.add_response("get_item", {})
                d.add_response("put_item", {})
                # Capture the request sent through real DynamoDB serialization;
                # only RETRYABLE may be saved for malformed runtime completion.
                updates = []

                def capture(params, **kwargs):
                    updates.append(params.copy())

                self.table.meta.client.meta.events.register(
                    "before-parameter-build.dynamodb.UpdateItem", capture
                )
                d.add_response("update_item", {})
                a.add_response("invoke_agent_runtime", self.body(raw))
                try:
                    with self.assertRaises(BoundaryError):
                        run_job(
                            RAW,
                            table=self.table,
                            agent=self.agent,
                            runtime_arn=ARN,
                            qualifier="candidate",
                            release_id="release-one",
                            now=100,
                        )
                finally:
                    self.table.meta.client.meta.events.unregister(
                        "before-parameter-build.dynamodb.UpdateItem", capture
                    )
                self.assertEqual(len(updates), 1)
                self.assertIn(":retry", updates[0]["ExpressionAttributeValues"])
                self.assertNotIn(":result", updates[0]["ExpressionAttributeValues"])
                d.assert_no_pending_responses()
                a.assert_no_pending_responses()

    def test_real_dynamodb_resource_accepts_success_serialization(self):
        with Stubber(self.agent) as a, Stubber(self.table.meta.client) as d:
            for op in ("get_item", "put_item", "update_item"):
                d.add_response(op, {})
            a.add_response(
                "invoke_agent_runtime", self.body(json.dumps(RESULT).encode())
            )
            result = run_job(
                RAW,
                table=self.table,
                agent=self.agent,
                runtime_arn=ARN,
                qualifier="candidate",
                release_id="release-one",
                now=100,
            )
            self.assertEqual(result["result"], RESULT)
            d.assert_no_pending_responses()
            a.assert_no_pending_responses()
