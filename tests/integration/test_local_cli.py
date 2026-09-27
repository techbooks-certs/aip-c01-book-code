from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from claimsassist.cli import main
from tests.unit.test_boundaries import valid_claim


class LocalIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "claim.json"
        self.source.write_text(json.dumps(valid_claim()), encoding="utf-8")

    def invoke(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = main(["--input", str(self.source), *args])
        return result, stdout.getvalue(), stderr.getvalue()

    def test_offline_pipeline_outputs_labeled_fixture_and_never_initializes_aws(self):
        with (
            patch(
                "claimsassist.cli.live_client",
                side_effect=AssertionError("AWS forbidden"),
            ),
            patch(
                "socket.socket.connect", side_effect=AssertionError("Network forbidden")
            ),
        ):
            status, out, err = self.invoke()
        self.assertEqual(status, 0)
        self.assertEqual(err, "")
        data = json.loads(out)
        self.assertEqual(data["evidence_class"], "offline_fixture")
        self.assertEqual(data["usage"]["totalTokens"], 0)
        self.assertTrue(data["review_required"])

    def test_cross_tenant_failure_does_not_create_evidence(self):
        dest = self.root / "evidence.json"
        status, out, err = self.invoke(
            "--tenant", "tenant-birch", "--output", str(dest)
        )
        self.assertEqual(status, 2)
        self.assertFalse(dest.exists())
        self.assertEqual(out, "")
        self.assertIn("tenant scope", err)

    def test_new_evidence_file_roundtrip_and_existing_file_preserved(self):
        dest = self.root / "evidence.json"
        self.assertEqual(self.invoke("--output", str(dest))[0], 0)
        first = dest.read_bytes()
        self.assertEqual(json.loads(first)["claim_id"], "claim-0001")
        self.assertEqual(self.invoke("--output", str(dest))[0], 2)
        self.assertEqual(dest.read_bytes(), first)

    def test_live_mode_requires_explicit_charge_and_configuration_flags(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            main(["--input", str(self.source), "--live"])
        self.assertEqual(caught.exception.code, 2)

    def test_invalid_live_input_rejected_before_sdk_initialization(self):
        self.source.write_text("{}")
        with patch(
            "claimsassist.cli.live_client",
            side_effect=AssertionError("must not initialize"),
        ):
            result, _, _ = self.invoke(
                "--live",
                "--acknowledge-charges",
                "--profile",
                "sandbox",
                "--region",
                "us-east-1",
                "--model-id",
                "configured-model",
            )
        self.assertEqual(result, 2)

    def test_oversized_file_rejected_before_parse(self):
        self.source.write_bytes(b" " * 65537)
        result, _, err = self.invoke()
        self.assertEqual(result, 2)
        self.assertIn("64 KiB", err)

    def test_malformed_input_does_not_echo_sensitive_payload(self):
        self.source.write_text('{"description":"SECRET_SENTINEL"')
        result, out, err = self.invoke()
        self.assertEqual(result, 2)
        self.assertNotIn("SECRET_SENTINEL", out + err)

    def test_invalid_utf8_is_sanitized(self):
        self.source.write_bytes(b"\xff")
        result, _, err = self.invoke()
        self.assertEqual(result, 2)
        self.assertIn("UTF-8", err)


if __name__ == "__main__":
    unittest.main()
