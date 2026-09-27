import io
import json
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
import boto3
from botocore.response import StreamingBody
from botocore.stub import Stubber
from claimsassist.baseline import BoundaryError, ProviderError
from claimsassist.runtime_client import invoke, main as runtime_client_main
from claimsassist.stack_cleanup import cleanup, main as stack_cleanup_main
from tests.integration.test_cloud_integration import RESULT

ARN = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/ClaimsAssist-abcdefghij"
STACK = "arn:aws:cloudformation:us-east-1:123456789012:stack/claimsassist-runtime-test/abcdef12-1234"
SESSION = "12345678-1234-1234-1234-123456789012"


class RuntimeOperations(unittest.TestCase):
    def setUp(self):
        args = dict(
            region_name="us-east-1",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
        self.runtime = boto3.client("bedrock-agentcore", **args)
        self.cfn = boto3.client("cloudformation", **args)
        self.params = dict(
            agentRuntimeArn=ARN,
            runtimeSessionId=SESSION,
            qualifier="candidate",
            contentType="application/json",
            accept="application/json",
            payload=json.dumps({"prompt": "test"}).encode(),
        )

    def stack(self, **changes):
        return (
            dict(
                StackId=STACK,
                StackName="claimsassist-runtime-test",
                CreationTime=datetime(2026, 1, 1, tzinfo=timezone.utc),
                StackStatus="CREATE_COMPLETE",
                Tags=[
                    {"Key": "Project", "Value": "AIP-C01"},
                    {"Key": "Lab", "Value": "ch08-agentcore"},
                ],
            )
            | changes
        )

    def resources(self):
        return {
            "StackResources": [
                {
                    "LogicalResourceId": key,
                    "ResourceType": kind,
                    "ResourceStatus": "CREATE_COMPLETE",
                    "Timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc),
                }
                for key, kind in [
                    ("ExecutionRole", "AWS::IAM::Role"),
                    ("Runtime", "AWS::BedrockAgentCore::Runtime"),
                    ("CandidateEndpoint", "AWS::BedrockAgentCore::RuntimeEndpoint"),
                ]
            ]
        }

    def run_cleanup(self, **changes):
        return cleanup(
            self.cfn,
            **(
                dict(
                    stack_id=STACK,
                    expected_account="123456789012",
                    expected_region="us-east-1",
                )
                | changes
            ),
        )

    def test_sdk_invocation_reads_and_closes_stream(self):
        raw = io.BytesIO(json.dumps(RESULT).encode())
        body = StreamingBody(raw, len(raw.getvalue()))
        with Stubber(self.runtime) as stub:
            stub.add_response(
                "invoke_agent_runtime",
                {
                    "response": body,
                    "contentType": "application/json",
                    "statusCode": 200,
                },
                self.params,
            )
            result = invoke(
                self.runtime, runtime_arn=ARN, prompt="test", session_id=SESSION
            )
            self.assertTrue(result["synthetic_data"])
            self.assertTrue(raw.closed)
            stub.assert_no_pending_responses()

    def test_oversize_response_is_bounded_and_closed(self):
        raw = io.BytesIO(b"x" * 65537)
        with Stubber(self.runtime) as stub:
            stub.add_response(
                "invoke_agent_runtime",
                {
                    "response": StreamingBody(raw, 65537),
                    "contentType": "application/json",
                    "statusCode": 200,
                },
                self.params,
            )
            with self.assertRaises(BoundaryError):
                invoke(self.runtime, runtime_arn=ARN, prompt="test", session_id=SESSION)
            self.assertTrue(raw.closed)

    def test_failed_invocation_does_not_expose_provider_details(self):
        with Stubber(self.runtime) as stub:
            stub.add_client_error(
                "invoke_agent_runtime",
                service_error_code="AccessDeniedException",
                service_message="private prompt",
                expected_params=self.params,
            )
            with self.assertRaises(ProviderError) as caught:
                invoke(self.runtime, runtime_arn=ARN, prompt="test", session_id=SESSION)
            self.assertNotIn("private", str(caught.exception))

    def test_short_session_is_rejected_before_request(self):
        with Stubber(self.runtime), self.assertRaises(BoundaryError):
            invoke(self.runtime, runtime_arn=ARN, prompt="test", session_id="short")

    def test_cleanup_default_reads_but_never_deletes(self):
        with Stubber(self.cfn) as stub:
            stub.add_response(
                "describe_stacks", {"Stacks": [self.stack()]}, {"StackName": STACK}
            )
            stub.add_response(
                "describe_stack_resources", self.resources(), {"StackName": STACK}
            )
            result = self.run_cleanup()
            self.assertEqual(result["action"], "PLAN_ONLY")
            self.assertFalse(result["stack_deletion_verified"])
            stub.assert_no_pending_responses()

    def test_cleanup_targets_exact_owned_stack_without_claiming_completion(self):
        with Stubber(self.cfn) as stub:
            stub.add_response(
                "describe_stacks", {"Stacks": [self.stack()]}, {"StackName": STACK}
            )
            stub.add_response(
                "describe_stack_resources", self.resources(), {"StackName": STACK}
            )
            stub.add_response("delete_stack", {}, {"StackName": STACK})
            result = self.run_cleanup(execute=True, confirmation=STACK)
            self.assertEqual(result["action"], "DELETE_REQUESTED")
            self.assertFalse(result["stack_deletion_verified"])
            stub.assert_no_pending_responses()

    def test_cleanup_wrong_account_region_or_confirmation_fails_before_sdk(self):
        with Stubber(self.cfn):
            for changes in [
                {"expected_account": "000000000000"},
                {"expected_region": "eu-west-1"},
                {"execute": True, "confirmation": "wrong"},
                {"execute": "true"},
            ]:
                with self.subTest(changes=changes), self.assertRaises(BoundaryError):
                    self.run_cleanup(**changes)

    def test_cleanup_ownership_protection_and_inflight_gates(self):
        for changes in [
            {"Tags": []},
            {"EnableTerminationProtection": True},
            {"StackStatus": "UPDATE_IN_PROGRESS"},
        ]:
            with self.subTest(changes=changes), Stubber(self.cfn) as stub:
                stub.add_response(
                    "describe_stacks",
                    {"Stacks": [self.stack(**changes)]},
                    {"StackName": STACK},
                )
                with self.assertRaises(BoundaryError):
                    self.run_cleanup(execute=True, confirmation=STACK)
                stub.assert_no_pending_responses()

    def test_repository_or_extra_resource_stack_cannot_be_deleted(self):
        with Stubber(self.cfn) as stub:
            stub.add_response(
                "describe_stacks", {"Stacks": [self.stack()]}, {"StackName": STACK}
            )
            rows = self.resources()
            rows["StackResources"][0]["ResourceType"] = "AWS::ECR::Repository"
            stub.add_response("describe_stack_resources", rows, {"StackName": STACK})
            with self.assertRaises(BoundaryError):
                self.run_cleanup(execute=True, confirmation=STACK)
            stub.assert_no_pending_responses()


class RuntimeClientCliTests(unittest.TestCase):
    def test_failed_invocation_does_not_leave_a_reserved_output_stub(self):
        # main() reserves --output before the billable call. A rejected
        # request (invalid ARN, validated before any AWS call) must not
        # leave a permanent empty file blocking a later retry at the path.
        # boto3.Session/client construction is mocked out entirely so the
        # test exercises main()'s file-handling, not real AWS SDK config
        # resolution.
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "evidence.json"
            argv = [
                "runtime_client.py",
                "--profile",
                "default",
                "--region",
                "us-east-1",
                "--runtime-arn",
                "not-a-valid-arn",
                "--output",
                str(out),
                "--acknowledge-charges",
            ]
            with (
                patch("sys.argv", argv),
                patch("boto3.Session") as session_cls,
            ):
                with self.assertRaises(BoundaryError):
                    runtime_client_main()
            self.assertFalse(out.exists())
            # Retrying at the same path must fail on the same validation
            # error, not on "file already exists".
            with (
                patch("sys.argv", argv),
                patch("boto3.Session") as session_cls,
            ):
                with self.assertRaises(BoundaryError):
                    runtime_client_main()
            self.assertFalse(out.exists())

    def test_existing_output_path_fails_cleanly_before_any_aws_work(self):
        # A prior successful run (or any pre-existing file) at --output must
        # be rejected with a clear parser.error before boto3 is ever touched,
        # not with a bare FileExistsError traceback from the exclusive-create
        # reservation.
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "evidence.json"
            out.write_text("{}")
            argv = [
                "runtime_client.py",
                "--profile",
                "default",
                "--region",
                "us-east-1",
                "--runtime-arn",
                ARN,
                "--output",
                str(out),
                "--acknowledge-charges",
            ]
            with (
                patch("sys.argv", argv),
                patch("boto3.Session") as session_cls,
            ):
                with self.assertRaises(SystemExit) as caught:
                    runtime_client_main()
            self.assertEqual(caught.exception.code, 2)
            session_cls.assert_not_called()


class StackCleanupCliTests(unittest.TestCase):
    """Regression: main() must not leave a permanent empty --output stub.

    cleanup() raises BoundaryError for many ordinary conditions a reader is
    likely to hit on a first attempt (mistyped --confirm-stack-id, a stack
    still under termination protection, etc.), and client construction
    itself can raise for a bad --profile -- both happen after --output is
    reserved, so both must not leave a stub blocking a retry.
    """

    def run_main(self, argv):
        with patch("sys.argv", ["stack_cleanup.py"] + argv):
            stack_cleanup_main()

    def test_failed_client_construction_does_not_leave_a_reserved_output_stub(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "cleanup.json"
            argv = [
                "--profile",
                "nonexistent-profile-xyz",
                "--region",
                "us-east-1",
                "--account",
                "123456789012",
                "--stack-id",
                STACK,
                "--output",
                str(out),
            ]
            with self.assertRaises(Exception):
                self.run_main(argv)
            self.assertFalse(out.exists())
            # Retrying at the same path must fail on the same real error,
            # not on "file already exists".
            with self.assertRaises(Exception):
                self.run_main(argv)
            self.assertFalse(out.exists())

    def test_cleanup_validation_failure_does_not_leave_a_reserved_output_stub(self):
        # A working client but a rejected confirmation (BoundaryError from
        # cleanup() itself, not from AWS/network) must behave the same way.
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "cleanup.json"
            argv = [
                "--profile",
                "default",
                "--region",
                "us-east-1",
                "--account",
                "123456789012",
                "--stack-id",
                STACK,
                "--execute",
                "--confirm-stack-id",
                "wrong-confirmation",
                "--output",
                str(out),
            ]
            with patch("boto3.Session") as session_cls:
                with self.assertRaises(BoundaryError):
                    self.run_main(argv)
            self.assertFalse(out.exists())

    def test_existing_output_path_fails_cleanly_before_any_aws_work(self):
        # A prior successful run (or any pre-existing file) at --output must
        # be rejected with a clear parser.error before boto3 is ever touched,
        # not with a bare FileExistsError traceback from the exclusive-create
        # reservation.
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "cleanup.json"
            out.write_text("{}")
            argv = [
                "--profile",
                "default",
                "--region",
                "us-east-1",
                "--account",
                "123456789012",
                "--stack-id",
                STACK,
                "--output",
                str(out),
            ]
            with patch("boto3.Session") as session_cls:
                with self.assertRaises(SystemExit) as caught:
                    self.run_main(argv)
            self.assertEqual(caught.exception.code, 2)
            session_cls.assert_not_called()
