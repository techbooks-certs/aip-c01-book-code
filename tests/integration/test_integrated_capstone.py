"""Composed journey tests; no cloud or semantic-quality assertions."""

from contextlib import closing as _closing_connection

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from claimsassist.approval import ApprovalLedger
from claimsassist.baseline import BoundaryError
from claimsassist.evidence import verify_evidence_files
from claimsassist.governance import SecurityContext
from claimsassist.intake import process_batch
from claimsassist.integrated_capstone import (
    AuthoredDraftRuntime,
    PolicyRetrieval,
    SyntheticAuthority,
    demonstrate,
    draft_prepared,
    execution_context,
    main as integrated_capstone_main,
    stage_proposal,
)
from claimsassist.managed_assistance import AssistanceConfig

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ROOT / "data/synthetic/ch03"
MANIFEST = SOURCES / "manifest.json"


class IntegratedCapstoneTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.context = SecurityContext(
            "tenant-amber", "adjuster-local", frozenset({"adjuster"})
        )
        self.authority = SyntheticAuthority()
        self.prepared = process_batch(
            json.loads(MANIFEST.read_text()), SOURCES, self.context.tenant_id
        )["accepted"][0]
        self.retrieval = PolicyRetrieval(self.authority)
        self.audit = []
        self.ledger = ApprovalLedger(Path(self.temp.name) / "approval.sqlite")
        self.config = AssistanceConfig(
            "ABCDEFGHIJ",
            "guardrail-fixture",
            "1",
            "authored-baseline",
            "draft-v1",
            "policy-v1",
        )

    def draft(self, **overrides):
        args = dict(
            context=self.context,
            authority=self.authority,
            config=self.config,
            retrieval_client=self.retrieval,
            runtime_client=AuthoredDraftRuntime(),
            audit_sink=self.audit.append,
            correlation_id="corr-composed",
            release_id="release-baseline",
        )
        return draft_prepared(self.prepared, **(args | overrides))

    def stage(self, draft):
        return stage_proposal(
            draft,
            self.prepared,
            context=self.context,
            authority=self.authority,
            ledger=self.ledger,
            request_id="review-one",
            now=1000,
        )

    def proposal_count(self):
        with _closing_connection(sqlite3.connect(self.ledger.path)) as con, con:
            return con.execute("SELECT COUNT(*) FROM proposals").fetchone()[0]

    def test_intake_content_reaches_retrieval_and_review_packet(self):
        draft = self.draft()
        request = json.loads(self.retrieval.queries[0])
        self.assertEqual(request["description"], self.prepared["claim"]["description"])
        self.assertEqual(request["loss_date"], self.prepared["fields"]["loss_date"])
        self.assertEqual(
            request["policy_number"], self.prepared["fields"]["policy_number"]
        )
        self.assertIn("[CONTACT REDACTED]", request["description"])
        self.assertNotIn("synthetic@example.invalid", request["description"])
        staged = self.stage(draft)
        self.assertEqual(staged["proposal"]["state"], "PENDING")
        self.assertEqual(
            staged["review_packet"]["intake_sha256"], draft["intake_sha256"]
        )
        self.assertEqual(
            staged["review_packet"]["correlation_id"], self.audit[0]["correlation_id"]
        )
        self.assertNotIn(self.prepared["claim"]["description"], json.dumps(self.audit))

    def test_changed_intake_rejected_before_review(self):
        draft = self.draft()
        self.prepared["claim"]["description"] = "Changed after drafting"
        with self.assertRaises(BoundaryError):
            self.stage(draft)
        self.assertEqual(self.proposal_count(), 0)

    def test_authority_and_missing_date_fail_before_dependencies(self):
        for field, value in [("loss_date", None), ("policy_number", "PL-999999")]:
            old = self.prepared["fields"][field]
            self.prepared["fields"][field] = value
            with self.assertRaises(BoundaryError):
                self.draft()
            self.prepared["fields"][field] = old
        with self.assertRaises(BoundaryError):
            self.draft(context=replace(self.context, tenant_id="tenant-birch"))
        with self.assertRaises(BoundaryError):
            self.draft(context=replace(self.context, roles=frozenset({"reader"})))
        self.assertEqual(self.retrieval.queries, [])
        self.assertEqual(self.audit, [])

    def test_provider_failure_cannot_stage(self):
        draft = self.draft(runtime_client=AuthoredDraftRuntime(fail=True))
        self.assertEqual(draft["assistance"]["failure_boundary"], "response")
        with self.assertRaises(BoundaryError):
            self.stage(draft)
        self.assertEqual(self.proposal_count(), 0)
        self.assertEqual(self.audit[0]["decision"], "WITHHELD")

    def test_audit_failure_cannot_stage(self):
        def fail(record):
            raise RuntimeError("PRIVATE_SENTINEL")

        draft = self.draft(audit_sink=fail)
        self.assertEqual(draft["assistance"]["failure_boundary"], "audit")
        with self.assertRaises(BoundaryError):
            self.stage(draft)
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(draft))
        self.assertEqual(self.proposal_count(), 0)

    def test_permission_change_before_staging_requires_fresh_draft(self):
        draft = self.draft()
        self.authority.permission_epoch = "epoch-v2"
        with self.assertRaises(BoundaryError):
            self.stage(draft)
        self.assertEqual(self.stage(self.draft())["proposal"]["state"], "PENDING")

    def test_revoked_source_after_approval_denies_execution(self):
        staged = self.stage(self.draft())
        self.ledger.approve(
            staged["proposal"]["proposal_id"],
            self.context.tenant_id,
            staged["proposal"]["fingerprint"],
            "adjuster-reviewer",
            "adjuster",
            1001,
        )
        self.authority.allowed = False
        with self.assertRaises(BoundaryError):
            self.ledger.execute_simulation(
                **execution_context(
                    staged, self.prepared, self.context, self.authority, 1002
                )
            )

    def test_journey_verifies_rollback_behavior_and_evidence(self):
        output = Path(self.temp.name) / "journey"
        report = demonstrate(output, sources=SOURCES, manifest_path=MANIFEST)
        for field in (
            "failed_staging_rejected",
            "rollback_behavior_verified",
            "pending_execution_rejected",
            "changed_authority_rejected",
            "replay_identical",
        ):
            self.assertIs(report[field], True, field)
        self.assertEqual(report["rollback"]["release_id"], "release-baseline")
        self.assertEqual(
            report["controlled_failure"]["release_id"], "release-candidate"
        )
        self.assertEqual(report["controlled_failure"]["assistance"]["status"], "failed")
        self.assertTrue(report["receipt"]["simulation_only"])
        self.assertEqual(report["intake_claim_id"], "claim-0101")
        audits = [
            json.loads(line)
            for line in (output / "audit.jsonl").read_text().splitlines()
        ]
        self.assertEqual(len(audits), 5)
        self.assertEqual(
            {r["record"]["correlation_id"] for r in audits}, {report["correlation_id"]}
        )
        pack = json.loads((output / "manifest.json").read_text())
        self.assertTrue(verify_evidence_files(output, pack)["integrity_verified"])
        self.assertIsNone(
            json.loads((output / "review-labels.json").read_text())["support_verified"]
        )
        self.assertEqual(report["live_aws"], "NOT_RUN")
        with self.assertRaises(FileExistsError):
            demonstrate(output, sources=SOURCES, manifest_path=MANIFEST)
        (output / "evaluation_report.json").write_text("{}")
        with self.assertRaises(BoundaryError):
            verify_evidence_files(output, pack)

    def test_quarantined_required_source_cannot_start_journey(self):
        invalid = deepcopy(json.loads(MANIFEST.read_text()))
        for row in invalid:
            if row["claim_id"] == "claim-0101":
                row["sha256"] = "0" * 64
        manifest = Path(self.temp.name) / "manifest.json"
        manifest.write_text(json.dumps(invalid))
        with self.assertRaises(BoundaryError):
            demonstrate(
                Path(self.temp.name) / "rejected",
                sources=SOURCES,
                manifest_path=manifest,
            )
        self.assertFalse((Path(self.temp.name) / "rejected/audit.jsonl").exists())


class IntegratedCapstoneCliTests(unittest.TestCase):
    """Regression: main() must not leave an unhandled traceback on a rerun.

    demonstrate() creates --output-dir with mkdir(exist_ok=False); main() had
    no upfront guard, unlike the sibling CLIs (index_demo.py, capstone.py),
    so a reader rerunning this exact lab command a second time at the same
    --output-dir got a bare FileExistsError traceback instead of a clean
    parser.error() message.
    """

    def run_main(self, argv):
        with patch("sys.argv", ["integrated_capstone.py"] + argv):
            integrated_capstone_main()

    def test_first_run_succeeds_and_second_run_at_same_path_fails_cleanly(self):
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "journey"
            argv = [
                "--output-dir",
                str(out),
                "--sources",
                str(SOURCES),
                "--manifest",
                str(MANIFEST),
            ]
            self.run_main(argv)
            self.assertTrue((out / "journey.json").exists())
            with self.assertRaises(SystemExit) as caught:
                self.run_main(argv)
            self.assertEqual(caught.exception.code, 2)

    def test_missing_parent_directory_fails_cleanly(self):
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "missing-dir" / "journey"
            argv = [
                "--output-dir",
                str(out),
                "--sources",
                str(SOURCES),
                "--manifest",
                str(MANIFEST),
            ]
            with self.assertRaises(SystemExit) as caught:
                self.run_main(argv)
            self.assertEqual(caught.exception.code, 2)
            self.assertFalse(out.exists())
