import unittest
from unittest.mock import Mock

from claimsassist.baseline import BoundaryError, ModelConfig, OutputError, ProviderError
from claimsassist.cli import FixtureClient
from claimsassist.routing import Route, RoutePolicy, run_with_fallback


def claim():
    return {
        "schema_version": 1,
        "claim_id": "claim-0001",
        "tenant_id": "tenant-amber",
        "description": "Synthetic water damage; date unknown.",
    }


def routes():
    return [
        Route(n, ModelConfig("offline-" + n), "us-east-1", ["us-east-1"])
        for n in ("first", "second")
    ]


class Failure(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.routes = routes()
        self.policy = RoutePolicy(["us-east-1"])

    def call(self, factory, data=None):
        return run_with_fallback(
            data or claim(), "tenant-amber", self.routes, self.policy, factory
        )

    def test_success_does_not_construct_fallback(self):
        factory = Mock(return_value=FixtureClient())
        result = self.call(factory)
        self.assertEqual(len(result["attempts"]), 1)
        self.assertTrue(result["draft"]["review_required"])
        factory.assert_called_once_with(self.routes[0])

    def test_allowlisted_transient_errors_fall_back(self):
        for code in ("ThrottlingException", "ServiceUnavailableException"):
            with self.subTest(code=code):
                client = Mock()
                client.converse.side_effect = Failure(code)
                factory = Mock(side_effect=[client, FixtureClient()])
                result = self.call(factory)
                self.assertEqual(factory.call_count, 2)
                self.assertIsNone(result["attempts"][0]["usage"])
                self.assertEqual(result["attempts"][1]["route"], "second")

    def test_other_errors_do_not_fall_back(self):
        for code in (
            "AccessDeniedException",
            "ValidationException",
            "ResourceNotFoundException",
            "ModelTimeoutException",
            "Unknown",
        ):
            with self.subTest(code=code):
                client = Mock()
                client.converse.side_effect = Failure(code)
                factory = Mock(return_value=client)
                with self.assertRaises(ProviderError):
                    self.call(factory)
                self.assertEqual(factory.call_count, 1)

    def test_output_rejection_never_tries_another_model(self):
        for reason in (
            "guardrail_intervened",
            "content_filtered",
            "max_tokens",
            "tool_use",
        ):
            with self.subTest(reason=reason):
                raw = FixtureClient().converse()
                raw["stopReason"] = reason
                client = Mock()
                client.converse.return_value = raw
                factory = Mock(return_value=client)
                with self.assertRaises(OutputError):
                    self.call(factory)
                self.assertEqual(factory.call_count, 1)

    def test_all_transient_failures_return_no_draft(self):
        client = Mock()
        client.converse.side_effect = Failure("ServiceUnavailableException")
        result = self.call(Mock(return_value=client))
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["draft"])
        self.assertEqual(client.converse.call_count, 2)

    def test_cross_tenant_stops_before_clients(self):
        factory = Mock()
        with self.assertRaises(BoundaryError):
            self.call(factory, {**claim(), "tenant_id": "tenant-birch"})
        factory.assert_not_called()

    def test_disallowed_fallback_blocks_whole_configuration(self):
        self.routes[1] = Route(
            "second", ModelConfig("offline-b"), "us-east-1", ["us-east-1", "eu-west-1"]
        )
        factory = Mock()
        with self.assertRaisesRegex(BoundaryError, "Region policy"):
            self.call(factory)
        factory.assert_not_called()

    def test_disallowed_source_is_rejected(self):
        self.routes[0] = Route(
            "first", ModelConfig("offline-a"), "eu-west-1", ["us-east-1"]
        )
        with self.assertRaises(BoundaryError):
            self.call(Mock())

    def test_invalid_region_sets_and_contract(self):
        for value in ([], ["*"], ["us-east-1", "us-east-1"], "us-east-1", [True]):
            with self.subTest(value=value), self.assertRaises(BoundaryError):
                RoutePolicy(value)
        with self.assertRaises(BoundaryError):
            Route(
                "bad",
                ModelConfig("offline-a"),
                "us-east-1",
                ["us-east-1"],
                "approve-v1",
            )

    def test_route_count_and_duplicates(self):
        original = self.routes
        for value in ([], original * 2, [original[0], original[0]]):
            self.routes = value
            with self.assertRaises(BoundaryError):
                self.call(Mock())

    def test_unknown_provider_category_is_sanitized(self):
        error = ProviderError("sensitive request text")
        self.assertEqual(error.category, "provider_failure")
        self.assertNotIn("sensitive", str(error))
