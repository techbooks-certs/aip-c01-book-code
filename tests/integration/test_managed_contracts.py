"""Botocore Stubber tests: no account, inference or managed deployment."""

import unittest
import boto3
from botocore.stub import Stubber
from claimsassist.baseline import BoundaryError, ProviderError
from claimsassist.managed import assess_text, retrieve_permitted


class ManagedContracts(unittest.TestCase):
    def setUp(self):
        args = dict(
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        self.runtime = boto3.client("bedrock-runtime", **args)
        self.retrieval = boto3.client("bedrock-agent-runtime", **args)
        self.guard_args = dict(
            guardrail_id="abc123", version="1", source="INPUT", text="Synthetic request"
        )
        self.guard_request = dict(
            guardrailIdentifier="abc123",
            guardrailVersion="1",
            source="INPUT",
            content=[{"text": {"text": "Synthetic request"}}],
        )
        self.retrieve_args = dict(
            knowledge_base_id="ABCDEFGHIJ",
            trusted_tenant="tenant-amber",
            query="Corrosion exclusion",
            source_allowed=lambda *args: True,
        )
        self.retrieve_request = dict(
            knowledgeBaseId="ABCDEFGHIJ",
            retrievalQuery={"text": "Corrosion exclusion"},
            retrievalConfiguration={
                "vectorSearchConfiguration": {
                    "numberOfResults": 5,
                    "filter": {"equals": {"key": "tenant_id", "value": "tenant-amber"}},
                }
            },
        )

    def response(self, tenant="tenant-amber"):
        return {
            "retrievalResults": [
                {
                    "content": {"type": "TEXT", "text": "Synthetic policy extract"},
                    "metadata": {
                        "tenant_id": tenant,
                        "source_id": "pol-4821",
                        "source_version": "v1",
                    },
                }
            ]
        }

    def test_guardrail_contract_and_conservative_intervention(self):
        for action in ["NONE", "GUARDRAIL_INTERVENED"]:
            with self.subTest(action=action), Stubber(self.runtime) as stub:
                stub.add_response(
                    "apply_guardrail",
                    {
                        "action": action,
                        "outputs": [],
                        "assessments": [],
                        "usage": {
                            "topicPolicyUnits": 0,
                            "contentPolicyUnits": 0,
                            "wordPolicyUnits": 0,
                            "sensitiveInformationPolicyUnits": 0,
                            "sensitiveInformationPolicyFreeUnits": 0,
                            "contextualGroundingPolicyUnits": 0,
                        },
                    },
                    self.guard_request,
                )
                decision = assess_text(self.runtime, **self.guard_args)
                self.assertEqual(decision.allowed, action == "NONE")
                stub.assert_no_pending_responses()

    def test_service_failure_never_becomes_permission(self):
        with Stubber(self.runtime) as stub:
            stub.add_client_error(
                "apply_guardrail",
                service_error_code="AccessDeniedException",
                service_message="private payload",
                expected_params=self.guard_request,
            )
            with self.assertRaises(ProviderError) as caught:
                assess_text(self.runtime, **self.guard_args)
            self.assertNotIn("private", str(caught.exception))
            stub.assert_no_pending_responses()

    def test_unknown_action_fails_closed(self):
        class Unexpected:
            def apply_guardrail(self, **kwargs):
                return {"action": "NEW_ACTION"}

        with self.assertRaises(BoundaryError):
            assess_text(Unexpected(), **self.guard_args)

    def test_draft_version_is_rejected_before_sdk_call(self):
        with Stubber(self.runtime):
            with self.assertRaises(BoundaryError):
                assess_text(self.runtime, **(self.guard_args | {"version": "DRAFT"}))

    def test_retrieval_filter_and_authoritative_recheck(self):
        seen = []

        def allowed(*args):
            seen.append(args)
            return True

        with Stubber(self.retrieval) as stub:
            stub.add_response("retrieve", self.response(), self.retrieve_request)
            result = retrieve_permitted(
                self.retrieval, **(self.retrieve_args | {"source_allowed": allowed})
            )
            self.assertEqual(result[0].source_id, "pol-4821")
            self.assertEqual(seen, [("tenant-amber", "pol-4821", "v1")])
            stub.assert_no_pending_responses()

    def test_cross_tenant_result_is_rejected(self):
        with Stubber(self.retrieval) as stub:
            stub.add_response(
                "retrieve", self.response("tenant-violet"), self.retrieve_request
            )
            with self.assertRaises(BoundaryError):
                retrieve_permitted(self.retrieval, **self.retrieve_args)

    def test_revoked_and_truthy_permission_results_are_rejected(self):
        for decision in [False, "true", 1]:
            with self.subTest(decision=decision), Stubber(self.retrieval) as stub:
                stub.add_response("retrieve", self.response(), self.retrieve_request)
                with self.assertRaises(BoundaryError):
                    retrieve_permitted(
                        self.retrieval,
                        **(
                            self.retrieve_args
                            | {"source_allowed": lambda *args: decision}
                        ),
                    )

    def test_empty_retrieval_is_an_empty_result(self):
        with Stubber(self.retrieval) as stub:
            stub.add_response(
                "retrieve", {"retrievalResults": []}, self.retrieve_request
            )
            self.assertEqual(
                retrieve_permitted(self.retrieval, **self.retrieve_args), ()
            )
