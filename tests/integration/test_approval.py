from contextlib import closing as _closing_connection
from pathlib import Path
import sqlite3
import tempfile
import unittest

from claimsassist.approval import ApprovalLedger
from claimsassist.baseline import BoundaryError


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "ledger.sqlite"
        self.ledger = ApprovalLedger(self.path)
        self.proposal = self.ledger.propose(
            "tenant-amber",
            "claim-0001",
            "request-1",
            "pol-4821",
            "Provide photographs.",
            1000,
            source_version="v1",
            policy_version="policy-v1",
            permission_epoch="epoch-1",
        )

    def approve(self, **changes):
        args = dict(
            pid=self.proposal["proposal_id"],
            tenant="tenant-amber",
            reviewed_fingerprint=self.proposal["fingerprint"],
            actor_id="adjuster-one",
            actor_role="adjuster",
            now=1001,
        )
        args.update(changes)
        return self.ledger.approve(**args)

    def execute(self, **changes):
        args = dict(
            pid=self.proposal["proposal_id"],
            tenant="tenant-amber",
            current_claim="claim-0001",
            current_status="open",
            actor_role="adjuster",
            now=1002,
            current_source_allowed=True,
            current_source_version="v1",
            current_policy_version="policy-v1",
            current_permission_epoch="epoch-1",
        )
        args.update(changes)
        return self.ledger.execute_simulation(**args)

    def test_pending_cannot_execute(self):
        with self.assertRaises(BoundaryError):
            self.execute()

    def test_approval_persists_and_resumes(self):
        self.approve()
        self.ledger = ApprovalLedger(self.path)
        receipt = self.execute()
        self.assertTrue(receipt["simulation_only"])
        self.assertEqual(receipt["status"], "RECORDED_LOCALLY")

    def test_replay_returns_one_receipt(self):
        self.approve()
        a = self.execute()
        b = self.execute(now=2000)
        self.assertEqual(a, b)
        with _closing_connection(sqlite3.connect(self.path)) as c, c:
            self.assertEqual(
                c.execute("SELECT COUNT(*) FROM proposals").fetchone()[0], 1
            )

    def test_repeated_proposal_does_not_duplicate(self):
        again = self.ledger.propose(
            "tenant-amber",
            "claim-0001",
            "request-1",
            "pol-4821",
            "Provide photographs.",
            1100,
            source_version="v1",
            policy_version="policy-v1",
            permission_epoch="epoch-1",
        )
        self.assertEqual(again, self.proposal)

    def test_changed_action_cannot_reuse_key(self):
        with self.assertRaises(BoundaryError):
            self.ledger.propose(
                "tenant-amber",
                "claim-0001",
                "request-1",
                "pol-4821",
                "Different message.",
                1001,
                source_version="v1",
                policy_version="policy-v1",
                permission_epoch="epoch-1",
            )

    def test_cross_tenant_approval_and_execution_rejected(self):
        with self.assertRaises(BoundaryError):
            self.approve(tenant="tenant-birch")
        self.approve()
        with self.assertRaises(BoundaryError):
            self.execute(tenant="tenant-birch")

    def test_wrong_role_rejected(self):
        with self.assertRaises(BoundaryError):
            self.approve(actor_role="agent")
        self.approve()
        with self.assertRaises(BoundaryError):
            self.execute(actor_role="agent")

    def test_reviewed_fingerprint_must_match(self):
        with self.assertRaises(BoundaryError):
            self.approve(reviewed_fingerprint="0" * 64)

    def test_expired_approval_rejected(self):
        with self.assertRaises(BoundaryError):
            self.approve(now=1300)

    def test_expired_execution_rejected(self):
        self.approve()
        with self.assertRaises(BoundaryError):
            self.execute(now=1300)

    def test_changed_claim_status_rejected(self):
        self.approve()
        with self.assertRaises(BoundaryError):
            self.execute(current_status="closed")
        with self.assertRaises(BoundaryError):
            self.execute(current_claim="claim-0002")

    def test_revoked_source_rejected_after_approval(self):
        self.approve()
        with self.assertRaises(BoundaryError):
            self.execute(current_source_allowed=False)

    def test_corrupted_payload_rejected(self):
        with _closing_connection(sqlite3.connect(self.path)) as c, c:
            c.execute("UPDATE proposals SET payload=?", ('{"changed":true}',))
        with self.assertRaises(BoundaryError):
            self.approve()

    def test_invalid_clock_and_identifiers(self):
        with self.assertRaises(BoundaryError):
            self.approve(now=True)
        with self.assertRaises(BoundaryError):
            self.execute(pid="not-an-id")
