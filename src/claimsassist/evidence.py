"""Verify declared evidence files and hashes; never infer their substantive quality."""

import hashlib
from pathlib import Path, PurePosixPath
import re

from .baseline import BoundaryError

EVIDENCE_ROLES = frozenset(
    {
        "architecture_decision",
        "release_manifest",
        "dataset_manifest",
        "evaluation_report",
        "threat_control_review",
        "cost_assumptions",
        "runbook",
        "cleanup_verification",
    }
)


def verify_evidence_files(root, manifest):
    """Require each role to name a nonempty file under root with its expected digest.

    The caller obtains the expected manifest from a trusted review/snapshot. Hashes
    do not authenticate a manifest that an attacker can rewrite alongside files.
    This is a local integrity check, not a signed provenance or release approval.
    """
    root = Path(root).resolve(strict=True)
    if not root.is_dir() or not isinstance(manifest, dict):
        raise BoundaryError("Evidence root and manifest are invalid")
    if set(manifest) != EVIDENCE_ROLES:
        raise BoundaryError(
            "Evidence manifest must declare exactly the eight required roles"
        )
    verified = {}
    for role in sorted(EVIDENCE_ROLES):
        entry = manifest[role]
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256"}:
            raise BoundaryError("Each evidence entry needs a relative path and SHA-256")
        relative, expected = entry["path"], entry["sha256"]
        if (
            not isinstance(relative, str)
            or not relative
            or "\\" in relative
            or "\x00" in relative
            or PurePosixPath(relative).is_absolute()
            or ".." in PurePosixPath(relative).parts
        ):
            raise BoundaryError("Evidence path must remain inside its package")
        if not isinstance(expected, str) or not re.fullmatch("[a-f0-9]{64}", expected):
            raise BoundaryError("Invalid expected SHA-256")
        try:
            path = (root / relative).resolve(strict=True)
            if not path.is_relative_to(root) or not path.is_file():
                raise BoundaryError("Evidence path escapes package or is not a file")
            data = path.read_bytes()
        except (OSError, RuntimeError, ValueError) as exc:
            raise BoundaryError("Evidence file unavailable") from exc
        if not data or hashlib.sha256(data).hexdigest() != expected:
            raise BoundaryError("Evidence is empty or its digest does not match")
        verified[role] = {"path": relative, "bytes": len(data), "sha256": expected}
    return {"integrity_verified": True, "content_approved": False, "files": verified}
