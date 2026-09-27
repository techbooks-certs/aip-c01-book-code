"""Offline deployment-contract fixture: gateway, queued jobs, release and rollback."""

from contextlib import closing as _closing_connection

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from .baseline import BoundaryError, _identifier, _string


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass(frozen=True)
class DeploymentContext:
    subject: str
    tenant: str
    correlation_id: str
    operations: frozenset[str]
    deadline: int

    def __post_init__(self):
        _identifier(self.subject, "subject", r"adjuster-[a-z0-9-]{1,40}")
        _identifier(self.tenant, "tenant", r"tenant-[a-z]{1,20}")
        _identifier(self.correlation_id, "correlation_id", r"corr-[a-z0-9-]{1,50}")
        if not self.operations or not self.operations <= frozenset(
            {"stream_summary", "queue_summary"}
        ):
            raise BoundaryError("Invalid permitted operations")
        if type(self.deadline) is not int or self.deadline < 0:
            raise BoundaryError("Invalid deadline")


class LocalGateway:
    """Application gateway fixture; authentication is assumed to happen upstream."""

    def __init__(self, allowed_models, tenant_units):
        if not allowed_models or any(
            not re.fullmatch(r"[a-z0-9._:/-]{1,100}", m) for m in allowed_models
        ):
            raise BoundaryError("Invalid model allowlist")
        if any(
            not re.fullmatch(r"tenant-[a-z]{1,20}", t) or type(v) is not int or v < 1
            for t, v in tenant_units.items()
        ):
            raise BoundaryError("Invalid tenant budget")
        self.allowed = frozenset(allowed_models)
        self.remaining = dict(tenant_units)

    def authorize(self, context, operation, model_ref, units, now):
        if type(now) is not int or now < 0 or now >= context.deadline:
            raise BoundaryError("Request deadline expired")
        if operation not in context.operations:
            raise BoundaryError("Operation unavailable to caller")
        if model_ref not in self.allowed:
            raise BoundaryError("Model is not approved")
        if type(units) is not int or not 1 <= units <= 100:
            raise BoundaryError("Invalid request units")
        if self.remaining.get(context.tenant, 0) < units:
            raise BoundaryError("Tenant request budget exhausted")
        self.remaining[context.tenant] -= units
        return {
            "tenant": context.tenant,
            "subject": context.subject,
            "correlation_id": context.correlation_id,
            "operation": operation,
            "model_ref": model_ref,
            "units": units,
        }


def stream_fixture(context, authorization, chunks, release_id):
    """Yield typed local events; chunks are pre-written fixture text, not model tokens."""
    _identifier(release_id, "release_id", r"release-[a-z0-9.-]{1,40}")
    if (
        not isinstance(authorization, dict)
        or authorization.get("tenant") != context.tenant
        or authorization.get("correlation_id") != context.correlation_id
        or authorization.get("subject") != context.subject
        or authorization.get("operation") != "stream_summary"
        or "stream_summary" not in context.operations
    ):
        raise BoundaryError("Authorization context mismatch")
    if not isinstance(chunks, list) or not 1 <= len(chunks) <= 20:
        raise BoundaryError("Invalid stream fixture")
    yield {
        "event": "start",
        "correlation_id": context.correlation_id,
        "release_id": release_id,
    }
    for sequence, chunk in enumerate(chunks):
        _string(chunk, "stream chunk", 500)
        yield {"event": "delta", "sequence": sequence, "text": chunk}
    yield {"event": "complete", "sequence": len(chunks), "review_required": True}


class JobLedger:
    """SQLite queue-state simulation with duplicate-delivery protection."""

    def __init__(self, path: Path):
        self.path = Path(path)
        with _closing_connection(self._connect()) as con, con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, tenant TEXT, payload TEXT, digest TEXT, release TEXT, state TEXT, attempts INTEGER, result TEXT, error TEXT)"
            )

    def _connect(self):
        return sqlite3.connect(self.path, timeout=2)

    def submit(self, tenant, request_id, payload, release_id):
        _identifier(tenant, "tenant", r"tenant-[a-z]{1,20}")
        _identifier(request_id, "request_id", r"[a-z0-9][a-z0-9-]{0,49}")
        _identifier(release_id, "release_id", r"release-[a-z0-9.-]{1,40}")
        if not isinstance(payload, dict) or set(payload) != {"claim_id"}:
            raise BoundaryError("Invalid queued payload")
        _identifier(payload["claim_id"], "claim_id", r"claim-[0-9]{4,12}")
        canonical = {
            "tenant": tenant,
            "request_id": request_id,
            "payload": payload,
            "release_id": release_id,
        }
        job_id = _digest({"tenant": tenant, "request_id": request_id})
        fingerprint = _digest(canonical)
        with _closing_connection(self._connect()) as con, con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT digest,state,attempts FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            if row:
                if row[0] != fingerprint:
                    raise BoundaryError("Idempotency key reused for different job")
                return {"job_id": job_id, "state": row[1], "attempts": row[2]}
            con.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    tenant,
                    json.dumps(payload, sort_keys=True),
                    fingerprint,
                    release_id,
                    "QUEUED",
                    0,
                    None,
                    None,
                ),
            )
        return {"job_id": job_id, "state": "QUEUED", "attempts": 0}

    def process_delivery(self, job_id, tenant, provider, *, max_attempts=2):
        """Deliver one job to `provider` at most once per call, recording the outcome.

        Teaching note: `provider` runs while this method's SQLite write transaction is
        still open, so the whole ledger is locked for the duration of that call. That is
        fine for this single-process fixture, whose point is duplicate-delivery
        idempotency, not concurrent throughput. Do not carry this shape into a real queue
        worker: holding a database write lock across an external/network call serializes
        every other worker behind it. A production version should record a leased
        "IN_PROGRESS" state in one short transaction, call the provider with no lock held,
        then write the final state in a second transaction.
        """
        _identifier(job_id, "job_id", r"[a-f0-9]{64}")
        _identifier(tenant, "tenant", r"tenant-[a-z]{1,20}")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 5:
            raise BoundaryError("Invalid attempt limit")
        with _closing_connection(self._connect()) as con, con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT tenant,payload,state,attempts,result FROM jobs WHERE id=?",
                (job_id,),
            ).fetchone()
            if row is None or row[0] != tenant:
                raise BoundaryError("Job unavailable in this scope")
            if row[2] == "SUCCEEDED":
                return json.loads(row[4])
            if row[2] == "DLQ":
                return {"job_id": job_id, "state": "DLQ", "attempts": row[3]}
            attempts = row[3] + 1
            try:
                result = provider(json.loads(row[1]))
            except TimeoutError:
                state = "DLQ" if attempts >= max_attempts else "RETRYABLE"
                con.execute(
                    "UPDATE jobs SET state=?,attempts=?,error=? WHERE id=?",
                    (state, attempts, "DEPENDENCY_TIMEOUT", job_id),
                )
                return {
                    "job_id": job_id,
                    "state": state,
                    "attempts": attempts,
                    "error": "DEPENDENCY_TIMEOUT",
                }
            except Exception:
                con.execute(
                    "UPDATE jobs SET state=?,attempts=?,error=? WHERE id=?",
                    ("DLQ", attempts, "DEPENDENCY_FAILURE", job_id),
                )
                return {
                    "job_id": job_id,
                    "state": "DLQ",
                    "attempts": attempts,
                    "error": "DEPENDENCY_FAILURE",
                }
            try:
                if (
                    not isinstance(result, dict)
                    or set(result) != {"summary", "review_required"}
                    or result["review_required"] is not True
                ):
                    raise BoundaryError("Provider result contract rejected")
                _string(result["summary"], "summary", 2000)
            except BoundaryError:
                # Persist the poison response before returning; raising here would roll back the attempt.
                con.execute(
                    "UPDATE jobs SET state=?,attempts=?,error=? WHERE id=?",
                    ("DLQ", attempts, "INVALID_PROVIDER_RESULT", job_id),
                )
                return {
                    "job_id": job_id,
                    "state": "DLQ",
                    "attempts": attempts,
                    "error": "INVALID_PROVIDER_RESULT",
                }
            receipt = {
                "job_id": job_id,
                "state": "SUCCEEDED",
                "attempts": attempts,
                "result": result,
            }
            con.execute(
                "UPDATE jobs SET state=?,attempts=?,result=?,error=NULL WHERE id=?",
                ("SUCCEEDED", attempts, json.dumps(receipt, sort_keys=True), job_id),
            )
            return receipt


def fixture_release_manifest(release_id, status="PASS", cases=25):
    """Authored local fixture only; hashes bind identity, not review truth."""
    manifest = dict(
        release_id=release_id,
        code_sha256="a" * 64,
        prompt_version="claims-summary-v1",
        tool_schema_version="tools-v1",
        model_ref="bedrock/demo-model-v1",
        policy_version="policy-v1",
        guardrail_version="guardrail-v1",
        dataset_sha256="b" * 64,
        index_version="index-v1",
        evidence_sha256="c" * 64,
    )
    report = dict(subject_sha256=_digest(manifest), status=status, cases=cases)
    return dict(
        manifest,
        quality_gate=dict(status=status, cases=cases),
        evaluation_report=report,
        evaluation_sha256=_digest(report),
    )


class ReleaseRegistry:
    """Local release-pointer simulation. It does not deploy infrastructure."""

    REQUIRED = {
        "release_id",
        "code_sha256",
        "prompt_version",
        "tool_schema_version",
        "model_ref",
        "policy_version",
        "guardrail_version",
        "dataset_sha256",
        "index_version",
        "evidence_sha256",
        "quality_gate",
        "evaluation_report",
        "evaluation_sha256",
    }

    def __init__(self, path: Path):
        self.path = Path(path)
        with _closing_connection(self._connect()) as con, con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS releases (id TEXT PRIMARY KEY, manifest TEXT, eligible INTEGER)"
            )
            con.execute(
                "CREATE TABLE IF NOT EXISTS pointer (slot INTEGER PRIMARY KEY CHECK(slot=1), current TEXT, previous TEXT)"
            )
            con.execute("INSERT OR IGNORE INTO pointer VALUES (1,NULL,NULL)")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=2)

    def register(self, manifest):
        if not isinstance(manifest, dict) or set(manifest) != self.REQUIRED:
            raise BoundaryError("Invalid release manifest fields")
        _identifier(manifest["release_id"], "release_id", r"release-[a-z0-9.-]{1,40}")
        for field in (
            "code_sha256",
            "dataset_sha256",
            "evidence_sha256",
            "evaluation_sha256",
        ):
            if not isinstance(manifest[field], str) or not re.fullmatch(
                r"[a-f0-9]{64}", manifest[field]
            ):
                raise BoundaryError("Invalid release artifact digest")
        for field in (
            "prompt_version",
            "tool_schema_version",
            "model_ref",
            "policy_version",
            "guardrail_version",
            "index_version",
        ):
            _string(manifest[field], field, 100)
        gate = manifest["quality_gate"]
        if (
            not isinstance(gate, dict)
            or set(gate) != {"status", "cases"}
            or gate["status"] not in ("PASS", "FAIL")
            or type(gate["cases"]) is not int
            or gate["cases"] < 1
        ):
            raise BoundaryError("Invalid quality gate")
        report = manifest["evaluation_report"]
        subject = {
            k: v
            for k, v in manifest.items()
            if k not in {"quality_gate", "evaluation_report", "evaluation_sha256"}
        }
        if (
            not isinstance(report, dict)
            or set(report) != {"subject_sha256", "status", "cases"}
            or report.get("subject_sha256") != _digest(subject)
            or report.get("status") != gate["status"]
            or type(report.get("cases")) is not int
            or report.get("cases") != gate["cases"]
            or _digest(report) != manifest["evaluation_sha256"]
        ):
            raise BoundaryError("Evaluation artifact does not bind this release")
        with _closing_connection(self._connect()) as con, con:
            con.execute(
                "INSERT INTO releases VALUES (?,?,?)",
                (
                    manifest["release_id"],
                    json.dumps(manifest, sort_keys=True),
                    int(gate["status"] == "PASS"),
                ),
            )
        return {
            "release_id": manifest["release_id"],
            "eligible": gate["status"] == "PASS",
        }

    def promote(self, release_id):
        _identifier(release_id, "release_id", r"release-[a-z0-9.-]{1,40}")
        with _closing_connection(self._connect()) as con, con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT eligible FROM releases WHERE id=?", (release_id,)
            ).fetchone()
            if row is None or row[0] != 1:
                raise BoundaryError("Release did not pass its quality gate")
            current, previous = con.execute(
                "SELECT current,previous FROM pointer WHERE slot=1"
            ).fetchone()
            if current == release_id:
                return {"current": current, "previous": previous}
            con.execute(
                "UPDATE pointer SET current=?,previous=? WHERE slot=1",
                (release_id, current),
            )
        return {"current": release_id, "previous": current}

    def rollback(self):
        with _closing_connection(self._connect()) as con, con:
            con.execute("BEGIN IMMEDIATE")
            current, previous = con.execute(
                "SELECT current,previous FROM pointer WHERE slot=1"
            ).fetchone()
            if previous is None:
                raise BoundaryError("No evaluated rollback target")
            eligible = con.execute(
                "SELECT eligible FROM releases WHERE id=?", (previous,)
            ).fetchone()
            if eligible is None or eligible[0] != 1:
                raise BoundaryError("Rollback target is not eligible")
            con.execute(
                "UPDATE pointer SET current=?,previous=? WHERE slot=1",
                (previous, current),
            )
        return {"current": previous, "previous": current}


def demonstration(directory: Path):
    directory = Path(directory)
    directory.mkdir(parents=False, exist_ok=True)
    context = DeploymentContext(
        "adjuster-ava",
        "tenant-amber",
        "corr-demo-1",
        frozenset({"stream_summary", "queue_summary"}),
        2000,
    )
    gateway = LocalGateway({"bedrock/demo-model-v1"}, {"tenant-amber": 10})
    auth = gateway.authorize(
        context, "stream_summary", "bedrock/demo-model-v1", 2, 1000
    )
    stream = list(
        stream_fixture(
            context,
            auth,
            ["Synthetic claim summary.", " Human review required."],
            "release-1.0.0",
        )
    )
    jobs = JobLedger(directory / "jobs.sqlite")
    job = jobs.submit(
        context.tenant, "request-0001", {"claim_id": "claim-0001"}, "release-1.0.0"
    )
    calls = {"count": 0}

    def provider(payload):
        calls["count"] += 1
        return {
            "summary": f"Fixture for {payload['claim_id']}",
            "review_required": True,
        }

    first = jobs.process_delivery(job["job_id"], context.tenant, provider)
    duplicate = jobs.process_delivery(job["job_id"], context.tenant, provider)
    releases = ReleaseRegistry(directory / "releases.sqlite")
    releases.register(fixture_release_manifest("release-1.0.0"))
    promoted = releases.promote("release-1.0.0")
    rejected = None
    try:
        releases.register(fixture_release_manifest("release-1.1.0", "FAIL"))
        releases.promote("release-1.1.0")
    except BoundaryError as exc:
        rejected = str(exc)
    return {
        "evidence_class": "offline_deployment_contract_fixture",
        "stream": stream,
        "job_first": first,
        "job_duplicate": duplicate,
        "provider_calls": calls["count"],
        "promoted": promoted,
        "failed_release_rejected": rejected,
        "limits": [
            "No AWS resource was created",
            "No model or external API was invoked",
            "Streaming and queue behavior are local fixtures",
        ],
    }
