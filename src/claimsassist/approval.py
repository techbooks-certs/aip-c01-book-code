"""Durable local approval simulation. No email, payment or cloud action is executed."""

from contextlib import closing as _closing_connection

import hashlib
import json
from pathlib import Path
import sqlite3

from .baseline import BoundaryError, _identifier, _string


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class ApprovalLedger:
    def __init__(self, path: Path):
        self.path = Path(path)
        with _closing_connection(self._connect()) as con, con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, tenant TEXT, claim TEXT, payload TEXT, fingerprint TEXT, state TEXT, expires INTEGER, approver TEXT, receipt TEXT)"
            )

    def _connect(self):
        return sqlite3.connect(self.path, timeout=2)

    def propose(
        self,
        tenant,
        claim,
        request_id,
        source_id,
        message,
        now,
        *,
        source_version,
        policy_version,
        permission_epoch,
    ):
        _identifier(tenant, "tenant", r"tenant-[a-z]{1,20}")
        _identifier(claim, "claim", r"claim-[0-9]{4,12}")
        for label, value in [("request_id", request_id), ("source_id", source_id)]:
            _identifier(value, label, r"[a-z0-9][a-z0-9-]{0,49}")
        _string(message, "message", 500)
        if type(now) is not int or now < 0:
            raise BoundaryError("Invalid clock value")
        for label, value in [
            ("source_version", source_version),
            ("policy_version", policy_version),
            ("permission_epoch", permission_epoch),
        ]:
            _string(value, label, 100)
        payload = dict(
            tenant=tenant,
            claim=claim,
            request_id=request_id,
            source_id=source_id,
            message=message,
            source_version=source_version,
            policy_version=policy_version,
            permission_epoch=permission_epoch,
        )
        pid = digest({"tenant": tenant, "request_id": request_id})
        fingerprint = digest(payload)
        with _closing_connection(self._connect()) as con, con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT fingerprint,state FROM proposals WHERE id=?", (pid,)
            ).fetchone()
            if row:
                if row[0] != fingerprint:
                    raise BoundaryError("Idempotency key reused for a different action")
                return dict(proposal_id=pid, state=row[1], fingerprint=fingerprint)
            con.execute(
                "INSERT INTO proposals VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    pid,
                    tenant,
                    claim,
                    json.dumps(payload, sort_keys=True),
                    fingerprint,
                    "PENDING",
                    now + 300,
                    None,
                    None,
                ),
            )
        return dict(proposal_id=pid, state="PENDING", fingerprint=fingerprint)

    def _read(self, con, pid, tenant):
        _identifier(pid, "proposal_id", r"[a-f0-9]{64}")
        _identifier(tenant, "tenant", r"tenant-[a-z]{1,20}")
        row = con.execute(
            "SELECT tenant,claim,payload,fingerprint,state,expires,approver,receipt FROM proposals WHERE id=?",
            (pid,),
        ).fetchone()
        if row is None or row[0] != tenant:
            raise BoundaryError("Proposal unavailable in this scope")
        payload = json.loads(row[2])
        if (
            digest(payload) != row[3]
            or payload["tenant"] != row[0]
            or payload["claim"] != row[1]
        ):
            raise BoundaryError("Stored action integrity mismatch")
        return row

    def approve(self, pid, tenant, reviewed_fingerprint, actor_id, actor_role, now):
        # actor identity/role are supplied by the trusted application, never by the agent.
        _identifier(actor_id, "actor", r"adjuster-[a-z0-9-]{1,40}")
        if actor_role != "adjuster" or type(now) is not int or now < 0:
            raise BoundaryError(
                "Approval requires trusted adjuster context and a valid clock"
            )
        with _closing_connection(self._connect()) as con, con:
            con.execute("BEGIN IMMEDIATE")
            row = self._read(con, pid, tenant)
            if row[3] != reviewed_fingerprint or now >= row[5]:
                raise BoundaryError("Stale or expired approval")
            if row[4] != "PENDING":
                raise BoundaryError("Proposal is not awaiting approval")
            con.execute(
                "UPDATE proposals SET state=?,approver=? WHERE id=?",
                ("APPROVED", actor_id, pid),
            )

    def execute_simulation(
        self,
        pid,
        tenant,
        current_claim,
        current_status,
        actor_role,
        now,
        *,
        current_source_allowed,
        current_source_version,
        current_policy_version,
        current_permission_epoch,
    ):
        if actor_role != "adjuster" or type(now) is not int or now < 0:
            raise BoundaryError("Execution requires current trusted adjuster context")
        with _closing_connection(self._connect()) as con, con:
            con.execute("BEGIN IMMEDIATE")
            row = self._read(con, pid, tenant)
            payload = json.loads(row[2])
            current = dict(
                source_version=current_source_version,
                policy_version=current_policy_version,
                permission_epoch=current_permission_epoch,
            )
            for key, value in current.items():
                _string(value, key, 100)
                if payload.get(key) != value:
                    raise BoundaryError("Reviewed authority context changed")
            if row[1] != current_claim or current_source_allowed is not True:
                raise BoundaryError("Claim or source authorization context changed")
            if row[4] == "EXECUTED":
                return json.loads(row[7])
            if row[4] != "APPROVED" or now >= row[5] or current_status != "open":
                raise BoundaryError("Action is not currently executable")
            receipt = dict(
                simulation_only=True,
                proposal_id=pid,
                action_sha256=row[3],
                status="RECORDED_LOCALLY",
            )
            con.execute(
                "UPDATE proposals SET state=?,receipt=? WHERE id=?",
                ("EXECUTED", json.dumps(receipt, sort_keys=True), pid),
            )
            return receipt
