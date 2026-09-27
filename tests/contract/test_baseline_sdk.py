"""Exercise the injected baseline through actual Botocore operation contracts."""

import unittest
import boto3
from botocore.stub import Stubber
from claimsassist.baseline import (
    Claim,
    ModelConfig,
    build_request,
    run_baseline,
    OutputError,
    ProviderError,
)
from tests.unit.test_boundaries import valid_claim
from tests.contract.test_converse_contract import response


class BaselineSDKContracts(unittest.TestCase):
    def test_real_sdk_complete_truncated_tool_and_service_error(self):
        client = boto3.client(
            "bedrock-runtime",
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        config = ModelConfig("synthetic-model")
        expected = build_request(Claim.from_mapping(valid_claim()), config)
        for stop in ("end_turn", "max_tokens", "tool_use", "guardrail_intervened"):
            with self.subTest(stop=stop), Stubber(client) as stub:
                payload = response()
                payload.update(stopReason=stop, metrics={"latencyMs": 0})
                stub.add_response("converse", payload, expected)
                if stop == "end_turn":
                    result = run_baseline(valid_claim(), "tenant-amber", client, config)
                    self.assertTrue(result["review_required"])
                else:
                    with self.assertRaises(OutputError):
                        run_baseline(valid_claim(), "tenant-amber", client, config)
                stub.assert_no_pending_responses()
        with Stubber(client) as stub:
            stub.add_client_error(
                "converse",
                service_error_code="AccessDeniedException",
                service_message="private",
                expected_params=expected,
            )
            with self.assertRaisesRegex(ProviderError, "access_denied"):
                run_baseline(valid_claim(), "tenant-amber", client, config)
            stub.assert_no_pending_responses()
