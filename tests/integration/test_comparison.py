import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from claimsassist.baseline import BoundaryError
from claimsassist.cli import FixtureClient
from claimsassist.compare import compare_cases, load_config, main
from claimsassist.routing import RoutePolicy
from tests.unit.test_routing import claim, routes, Failure

ROOT = Path(__file__).resolve().parents[2]


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.routes = routes()
        self.policy = RoutePolicy(["us-east-1"])

    def test_all_cases_validated_before_clients(self):
        factory = Mock()
        with self.assertRaises(BoundaryError):
            compare_cases(
                [claim(), {**claim(), "tenant_id": "tenant-birch"}],
                "tenant-amber",
                self.routes,
                self.policy,
                factory,
                "offline_fixture",
            )
        factory.assert_not_called()

    def test_duplicate_case_ids_rejected(self):
        factory = Mock()
        with self.assertRaises(BoundaryError):
            compare_cases(
                [claim(), claim()],
                "tenant-amber",
                self.routes,
                self.policy,
                factory,
                "offline_fixture",
            )
        factory.assert_not_called()

    def test_alternating_order_and_duration(self):
        clock = Mock(side_effect=[0, 0.01, 1, 1.02, 2, 2.03, 3, 3.04])
        factory = Mock(return_value=FixtureClient())
        rows = compare_cases(
            [claim(), {**claim(), "claim_id": "claim-0002"}],
            "tenant-amber",
            self.routes,
            self.policy,
            factory,
            "offline_fixture",
            clock=clock,
        )
        self.assertEqual(
            [r["route"] for r in rows], ["first", "second", "second", "first"]
        )
        self.assertEqual([r["elapsed_ms"] for r in rows], [10, 20, 30, 40])
        self.assertEqual(factory.call_count, 2)
        self.assertTrue(all(r["quality_score"] is None for r in rows))

    def test_failures_remain_rows(self):
        client = Mock()
        client.converse.side_effect = Failure("ThrottlingException")
        rows = compare_cases(
            [claim()],
            "tenant-amber",
            self.routes,
            self.policy,
            lambda r: client if r.name == "first" else FixtureClient(),
            "offline_fixture",
        )
        self.assertEqual(rows[0]["status"], "throttled")
        self.assertIsNone(rows[0]["usage"])
        self.assertEqual(rows[1]["status"], "success")

    def test_bad_output_is_not_exposed(self):
        client = Mock()
        client.converse.return_value = {"secret": "raw-content"}
        rows = compare_cases(
            [claim()],
            "tenant-amber",
            self.routes,
            self.policy,
            lambda r: client,
            "offline_fixture",
        )
        self.assertTrue(all(r["status"] == "output_rejected" for r in rows))
        self.assertNotIn("raw-content", json.dumps(rows))

    def test_invalid_config(self):
        source = json.loads((ROOT / "config/ch02-offline.json").read_text())
        for value in (
            {},
            {**source, "schema_version": True},
            {**source, "routes": []},
            {**source, "extra": 1},
        ):
            with self.subTest(value=value), self.assertRaises(BoundaryError):
                load_config(value)

    def test_offline_report_and_fallback_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.json"
            with (
                patch("socket.socket.connect", side_effect=AssertionError("network")),
                patch(
                    "claimsassist.compare.live_client",
                    side_effect=AssertionError("SDK"),
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                code = main(
                    [
                        "--config",
                        str(ROOT / "config/ch02-offline.json"),
                        "--cases",
                        str(ROOT / "data/synthetic/ch02-development.json"),
                        "--output",
                        str(out),
                        "--simulate-fallback",
                    ]
                )
            self.assertEqual(code, 0)
            report = json.loads(out.read_text())
            self.assertEqual(len(report["rows"]), 6)
            self.assertEqual(report["evidence_class"], "offline_fixture")
            self.assertEqual(len(report["fallback_exercise"]["attempts"]), 2)
            self.assertEqual(len(report["cases_sha256"]), 64)

    def test_existing_output_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "keep.json"
            out.write_text("keep")
            with (
                patch("claimsassist.compare.FixtureClient") as client,
                contextlib.redirect_stderr(io.StringIO()),
            ):
                code = main(
                    [
                        "--config",
                        str(ROOT / "config/ch02-offline.json"),
                        "--cases",
                        str(ROOT / "data/synthetic/ch02-development.json"),
                        "--output",
                        str(out),
                    ]
                )
            self.assertEqual(code, 2)
            self.assertEqual(out.read_text(), "keep")
            client.assert_not_called()

    def test_live_requires_explicit_charge_ack(self):
        with (
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as caught,
        ):
            main(
                [
                    "--config",
                    "unused",
                    "--cases",
                    "unused",
                    "--output",
                    "unused",
                    "--live",
                ]
            )
        self.assertEqual(caught.exception.code, 2)

    def test_failure_report_preserved(self):
        client = Mock()
        client.converse.side_effect = Failure("ServiceUnavailableException")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "failed.json"
            with (
                patch("claimsassist.compare.FixtureClient", return_value=client),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                code = main(
                    [
                        "--config",
                        str(ROOT / "config/ch02-offline.json"),
                        "--cases",
                        str(ROOT / "data/synthetic/ch02-development.json"),
                        "--output",
                        str(out),
                    ]
                )
            self.assertEqual(code, 3)
            self.assertEqual(len(json.loads(out.read_text())["rows"]), 6)
