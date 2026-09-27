from contextlib import closing as _closing_connection
import json
from pathlib import Path
import unittest
import tempfile
import hashlib
import sqlite3
from unittest.mock import patch
from botocore.config import Config
import boto3
from botocore.stub import Stubber
from botocore.validate import validate_parameters
from claimsassist.baseline import BoundaryError
from claimsassist.quality_monitoring import (
    validate_record,
    metric_request,
    publish,
    local_alert,
    alarm_request,
    guardrail_recipe,
    verify_manifest,
    read_bounded_json,
    guardrail_cases,
    main,
)

ROOT = Path(__file__).resolve().parents[2]


class QualityMonitoringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = Path(self.temp.name) / "publication.sqlite"
        self.manifest = ROOT / "data/synthetic/quality/case-labels.json"

    def publish(self, client, record):
        return publish(
            client,
            record,
            manifest_path=self.manifest,
            ledger_path=self.ledger,
            target_scope="fixture-local",
        )

    def record(self):
        return json.loads(
            (ROOT / "data/synthetic/quality/reviewed-window.json").read_text()
        )

    def client(self, service):
        return boto3.client(
            service,
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
            config=Config(retries={"total_max_attempts": 1}),
        )

    def test_synthetic_namespace_and_actual_local_alert(self):
        record = self.record()
        self.assertEqual(local_alert(record)["status"], "ALARM")
        self.assertEqual(
            metric_request(record)["Namespace"], "ClaimsAssist/TeachingFixture"
        )
        self.assertEqual(
            local_alert(dict(record, accepted=4, reviewed=5))["status"],
            "INSUFFICIENT_DATA",
        )

    def test_invalid_denominators_and_windows_rejected(self):
        for values in [
            dict(accepted=True),
            dict(reviewed=0),
            dict(accepted=31),
            dict(window_end="2026-02-30T09:00:00Z"),
            dict(window_end="2026-09-20T08:00:00Z"),
        ]:
            with self.subTest(values=values), self.assertRaises(BoundaryError):
                validate_record(dict(self.record(), **values))

    def test_exact_cloudwatch_publication_and_alarm_shapes(self):
        client = self.client("cloudwatch")
        record = self.record()
        with Stubber(client) as stub:
            stub.add_response("put_metric_data", {}, metric_request(record))
            self.assertTrue(self.publish(client, record)["submitted"])
            stub.assert_no_pending_responses()
        validate_parameters(
            alarm_request(record),
            client.meta.service_model.operation_model("PutMetricAlarm").input_shape,
        )

    def test_guardrail_recipe_matches_installed_sdk_contract(self):
        client = self.client("bedrock")
        validate_parameters(
            guardrail_recipe(),
            client.meta.service_model.operation_model("CreateGuardrail").input_shape,
        )

    def test_service_failure_is_not_claimed_as_published(self):
        client = self.client("cloudwatch")
        record = self.record()
        with Stubber(client) as stub:
            stub.add_client_error(
                "put_metric_data",
                service_error_code="InternalServiceError",
                expected_params=metric_request(record),
            )
            result = self.publish(client, record)
            self.assertEqual(result["state"], "UNKNOWN")
            self.assertFalse(result["submitted"])
            stub.assert_no_pending_responses()
            again = self.publish(client, record)
            self.assertFalse(again["retry_allowed"])
            self.assertEqual(again["state"], "UNKNOWN")

    def test_verified_manifest_matches_counts_and_fixture_provenance(self):
        result = verify_manifest(self.record(), self.manifest)
        self.assertEqual(result["accepted"], 24)
        self.assertEqual(result["case_count"], 30)
        self.assertFalse(result["reviewer_authenticated"])
        with self.assertRaises(BoundaryError):
            verify_manifest(dict(self.record(), accepted=25), self.manifest)

    def test_manifest_tampering_or_identity_changes_are_rejected(self):
        for updates in [
            dict(case_manifest_sha256="a" * 64),
            dict(reviewer_ref="different"),
            dict(label_provenance="independent_review"),
        ]:
            with self.subTest(updates=updates), self.assertRaises(BoundaryError):
                verify_manifest(dict(self.record(), **updates), self.manifest)

    def test_duplicate_cases_and_mixed_labels_fail_even_with_new_digest(self):
        base = json.loads(self.manifest.read_text())
        for mode in ("duplicate", "mixed"):
            manifest = json.loads(json.dumps(base))
            if mode == "duplicate":
                manifest["cases"][1]["case_id"] = manifest["cases"][0]["case_id"]
            else:
                manifest["cases"][1]["provenance"] = "independent_review"
            path = Path(self.temp.name) / (mode + ".json")
            path.write_text(json.dumps(manifest))
            record = dict(
                self.record(),
                case_manifest_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            with self.subTest(mode=mode), self.assertRaises(BoundaryError):
                verify_manifest(record, path)

    def test_bounded_reader_rejects_oversize_and_symlink(self):
        path = Path(self.temp.name) / "large.json"
        path.write_bytes(b"x" * 200)
        with self.assertRaises(BoundaryError):
            read_bounded_json(path, max_bytes=100)
        link = Path(self.temp.name) / "link.json"
        link.symlink_to(self.manifest)
        with self.assertRaises(BoundaryError):
            read_bounded_json(link)

    def test_acknowledged_publication_is_not_submitted_again(self):
        client = self.client("cloudwatch")
        record = self.record()
        with Stubber(client) as stub:
            stub.add_response("put_metric_data", {}, metric_request(record))
            self.assertTrue(self.publish(client, record)["submitted"])
            repeated = self.publish(client, record)
            self.assertFalse(repeated["submitted"])
            self.assertEqual(repeated["state"], "ACKNOWLEDGED")
            stub.assert_no_pending_responses()

    def test_crash_after_reservation_is_not_replayed(self):
        class Crash:
            def put_metric_data(self, **request):
                raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            self.publish(Crash(), self.record())

        class Never:
            def put_metric_data(self, **request):
                raise AssertionError("Must not retry")

        result = self.publish(Never(), self.record())
        self.assertEqual(result["state"], "RESERVED")
        self.assertFalse(result["retry_allowed"])

    def test_new_record_id_cannot_republish_same_window(self):
        class Successful:
            def put_metric_data(self, **request):
                return {}

        self.publish(Successful(), self.record())
        with self.assertRaises(BoundaryError):
            self.publish(Successful(), dict(self.record(), record_id="renamed"))

    def test_changed_identity_cannot_reuse_record_id(self):
        class Successful:
            def put_metric_data(self, **request):
                return {}

        self.publish(Successful(), self.record())
        # Valid manifest still required; changing ID alone is tested above. The
        # record fingerprint itself rejects changed content after verification.
        with _closing_connection(sqlite3.connect(self.ledger)) as db, db:
            db.execute("UPDATE publications SET digest='tampered'")
        with self.assertRaises(BoundaryError):
            self.publish(Successful(), self.record())

    def test_automatic_sdk_retry_configuration_is_rejected(self):
        client = boto3.client(
            "cloudwatch",
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
            config=Config(retries={"total_max_attempts": 2}),
        )
        with Stubber(client), self.assertRaises(BoundaryError):
            self.publish(client, self.record())

    def test_guardrail_expectations_are_not_detector_observations(self):
        cases = guardrail_cases()
        self.assertEqual(len(cases), 6)
        self.assertTrue(all(c["actual_action"] == "NOT_RUN" for c in cases))
        self.assertEqual(
            {c["expected_disposition"] for c in cases}, {"ALLOW", "WITHHOLD"}
        )
        client = self.client("bedrock")
        validate_parameters(
            {
                "guardrailIdentifier": "abc123",
                "description": "Reviewed synthetic candidate",
            },
            client.meta.service_model.operation_model(
                "CreateGuardrailVersion"
            ).input_shape,
        )

    def test_alarm_uses_weighted_counts_and_separates_provenance(self):
        record = self.record()
        request = alarm_request(record)
        self.assertEqual(
            request["Metrics"][-1]["Expression"],
            "IF(reviewed>=20,100*accepted/reviewed)",
        )
        self.assertFalse(request["ActionsEnabled"])
        other = dict(record, label_provenance="independent_review")
        self.assertNotEqual(request["AlarmName"], alarm_request(other)["AlarmName"])
        self.assertEqual(metric_request(record)["MetricData"][0]["Timestamp"].minute, 4)


class QualityMonitoringCliTests(unittest.TestCase):
    """Regression: main() must not leave a permanent empty --output stub when
    a live-mode AWS call raises after the file has already been reserved
    (session/client construction, the STS identity check, and publish()
    itself can all raise).
    """

    def run_main(self, argv):
        with patch("sys.argv", ["quality_monitoring.py"] + argv):
            main()

    def _record_and_manifest_paths(self):
        return (
            ROOT / "data/synthetic/quality/reviewed-window.json",
            ROOT / "data/synthetic/quality/case-labels.json",
        )

    def test_failed_live_session_does_not_leave_a_reserved_output_stub(self):
        record_path, manifest_path = self._record_and_manifest_paths()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.json"
            ledger = Path(tmp) / "publication.sqlite"
            argv = [
                "--record",
                str(record_path),
                "--manifest",
                str(manifest_path),
                "--output",
                str(out),
                "--live",
                "--profile",
                "nonexistent-profile-xyz",
                "--region",
                "us-east-1",
                "--account",
                "123456789012",
                "--ledger",
                str(ledger),
                "--acknowledge-charges",
            ]
            # boto3.Session construction itself is mocked out to fail deterministically
            # (independent of the fixture's timestamp relative to wall-clock "now").
            with patch(
                "boto3.Session", side_effect=RuntimeError("no such profile")
            ):
                with self.assertRaises(RuntimeError):
                    self.run_main(argv)
            self.assertFalse(out.exists())
            # Retrying at the same path must fail on the same underlying
            # error, not on "file already exists".
            with patch(
                "boto3.Session", side_effect=RuntimeError("no such profile")
            ):
                with self.assertRaises(RuntimeError):
                    self.run_main(argv)
            self.assertFalse(out.exists())

    def test_offline_run_still_succeeds_and_writes_a_real_report(self):
        record_path, manifest_path = self._record_and_manifest_paths()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.json"
            self.run_main(
                [
                    "--record",
                    str(record_path),
                    "--manifest",
                    str(manifest_path),
                    "--output",
                    str(out),
                ]
            )
            self.assertTrue(out.exists())
            report = json.loads(out.read_text())
            self.assertEqual(report["scope"], "local_verified_label_arithmetic")
            self.assertTrue(report["verification"]["manifest_verified"])

    def test_existing_output_path_fails_cleanly_before_any_work(self):
        # A prior successful run (or any pre-existing file) at --output must
        # be rejected with a clear parser.error before the record/manifest
        # are even read, not with a bare FileExistsError traceback from the
        # exclusive-create reservation.
        record_path, manifest_path = self._record_and_manifest_paths()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.json"
            out.write_text("{}")
            with self.assertRaises(SystemExit) as caught:
                self.run_main(
                    [
                        "--record",
                        str(record_path),
                        "--manifest",
                        str(manifest_path),
                        "--output",
                        str(out),
                    ]
                )
            self.assertEqual(caught.exception.code, 2)
