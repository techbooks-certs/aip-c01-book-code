import json
import unittest
import boto3
from botocore.stub import Stubber, ANY
from claimsassist.baseline import BoundaryError, ProviderError
from claimsassist.rag_answer import draft_answer, ConverseDraftProvider


class RagAnswerTests(unittest.TestCase):
    def evidence(self):
        return [
            {
                "citation_id": "policy-v1",
                "quote": "General exclusion with review exception.",
            }
        ]

    def valid(self):
        return {
            "claims": [{"text": "Review is conditional.", "citations": ["policy-v1"]}],
            "abstained": False,
        }

    def test_no_evidence_abstains_without_model_call(self):
        self.assertEqual(
            draft_answer("question", [], lambda _: self.fail("called model"))["status"],
            "abstain",
        )

    def test_valid_citations_still_require_semantic_review(self):
        result = draft_answer("question", self.evidence(), lambda _: self.valid())
        self.assertEqual(result["semantic_support"], "NOT_EVALUATED")
        self.assertTrue(result["review_required"])

    def test_fabricated_citation_fails(self):
        bad = self.valid()
        bad["claims"][0]["citations"] = ["invented"]
        with self.assertRaises(BoundaryError):
            draft_answer("question", self.evidence(), lambda _: bad)

    def test_empty_claims_or_truthy_abstention_cannot_pass(self):
        for output in [
            {"claims": [], "abstained": False},
            {"claims": [], "abstained": "false"},
            self.valid() | {"abstained": True},
        ]:
            with self.subTest(output=output), self.assertRaises(BoundaryError):
                draft_answer("question", self.evidence(), lambda _: output)

    def test_converse_request_and_generated_json_contract(self):
        client = boto3.client(
            "bedrock-runtime",
            region_name="us-east-1",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
        expected = {
            "modelId": "model-id",
            "system": ANY,
            "messages": ANY,
            "inferenceConfig": {"maxTokens": 1024},
        }
        response = {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"text": json.dumps(self.valid())}],
                }
            },
            "stopReason": "end_turn",
            "usage": {"inputTokens": 20, "outputTokens": 30, "totalTokens": 50},
            "metrics": {"latencyMs": 5},
        }
        with Stubber(client) as stub:
            stub.add_response("converse", response, expected)
            result = draft_answer(
                "question", self.evidence(), ConverseDraftProvider(client, "model-id")
            )
            self.assertEqual(result["status"], "draft_for_review")
            stub.assert_no_pending_responses()

    def test_truncation_is_not_a_complete_answer(self):
        class Truncated:
            def converse(self, **kwargs):
                return {"stopReason": "max_tokens"}

        with self.assertRaises(ProviderError):
            ConverseDraftProvider(Truncated(), "model")({})
