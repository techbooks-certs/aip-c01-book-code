import hashlib
from pathlib import Path
import tempfile
import unittest

from claimsassist.baseline import BoundaryError
from claimsassist.evidence import EVIDENCE_ROLES, verify_evidence_files


class EvidenceFilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "pack"
        self.root.mkdir()
        self.manifest = {}
        for role in EVIDENCE_ROLES:
            data = ("Synthetic test artifact: " + role).encode()
            (self.root / (role + ".txt")).write_bytes(data)
            self.manifest[role] = {
                "path": role + ".txt",
                "sha256": hashlib.sha256(data).hexdigest(),
            }

    def test_complete_files_establish_integrity_not_content_approval(self):
        report = verify_evidence_files(self.root, self.manifest)
        self.assertTrue(report["integrity_verified"])
        self.assertFalse(report["content_approved"])
        self.assertEqual(len(report["files"]), 8)

    def test_tampering_rejected(self):
        (self.root / "runbook.txt").write_text("Changed after review")
        with self.assertRaises(BoundaryError):
            verify_evidence_files(self.root, self.manifest)

    def test_missing_file_and_missing_role_rejected(self):
        (self.root / "runbook.txt").unlink()
        with self.assertRaises(BoundaryError):
            verify_evidence_files(self.root, self.manifest)
        del self.manifest["runbook"]
        with self.assertRaises(BoundaryError):
            verify_evidence_files(self.root, self.manifest)

    def test_traversal_absolute_path_and_symlink_escape_rejected(self):
        outside = Path(self.temp.name) / "outside.txt"
        outside.write_text("outside package")
        (self.root / "escape").symlink_to(outside)
        for path in ("../outside.txt", str(outside), "escape"):
            self.manifest["runbook"]["path"] = path
            self.manifest["runbook"]["sha256"] = hashlib.sha256(
                outside.read_bytes()
            ).hexdigest()
            with self.assertRaises(BoundaryError):
                verify_evidence_files(self.root, self.manifest)

    def test_empty_file_even_with_matching_digest_rejected(self):
        (self.root / "runbook.txt").write_bytes(b"")
        self.manifest["runbook"]["sha256"] = hashlib.sha256(b"").hexdigest()
        with self.assertRaises(BoundaryError):
            verify_evidence_files(self.root, self.manifest)
