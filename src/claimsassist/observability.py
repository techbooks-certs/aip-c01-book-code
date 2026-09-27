"""Sanitized observability and diagnosis fixtures; no telemetry is exported."""

from dataclasses import dataclass
import re
from .baseline import BoundaryError

ALLOWED_STAGES = (
    "auth",
    "retrieval",
    "rerank",
    "prompt",
    "model",
    "tool",
    "approval",
    "response",
)
SAFE_FIELDS = frozenset(
    {
        "correlation_id",
        "stage",
        "status",
        "duration_ms",
        "tenant_hash",
        "model_ref",
        "prompt_version",
        "source_version",
        "document_ids",
        "tool_name",
        "error_class",
        "input_tokens",
        "output_tokens",
        "release_id",
    }
)
FORBIDDEN = frozenset(
    {"prompt", "response", "document_text", "authorization", "secret", "email"}
)


def sanitize_event(event):
    if not isinstance(event, dict):
        raise BoundaryError("Event must be an object")
    clean = {k: v for k, v in event.items() if k in SAFE_FIELDS and k not in FORBIDDEN}
    required = {"correlation_id", "stage", "status", "duration_ms"}
    if required - clean.keys():
        raise BoundaryError("Missing telemetry fields")
    if not isinstance(clean["correlation_id"], str) or not re.fullmatch(
        r"corr-[a-z0-9-]{1,50}", clean["correlation_id"]
    ):
        raise BoundaryError("Invalid correlation ID")
    if (
        not isinstance(clean["stage"], str)
        or not isinstance(clean["status"], str)
        or clean["stage"] not in ALLOWED_STAGES
        or clean["status"] not in {"OK", "ERROR", "DENIED", "TIMEOUT"}
    ):
        raise BoundaryError("Invalid event state")
    if type(clean["duration_ms"]) is not int or clean["duration_ms"] < 0:
        raise BoundaryError("Invalid duration")
    for key, value in clean.items():
        if key in required:
            continue
        if key in {"input_tokens", "output_tokens"}:
            if type(value) is not int or value < 0:
                raise BoundaryError("Invalid token count")
        elif key == "document_ids":
            if (
                not isinstance(value, list)
                or len(value) > 20
                or any(
                    not isinstance(v, str)
                    or not re.fullmatch(r"[A-Za-z0-9._:/-]{1,150}", v)
                    for v in value
                )
            ):
                raise BoundaryError("Invalid document identifiers")
        elif not isinstance(value, str) or not re.fullmatch(
            r"[A-Za-z0-9._:/-]{1,150}", value
        ):
            raise BoundaryError("Telemetry metadata must be a bounded identifier")
    return clean


def assemble_trace(events):
    if not events:
        raise BoundaryError("Trace cannot be empty")
    clean = [sanitize_event(e) for e in events]
    correlation = clean[0]["correlation_id"]
    if any(e["correlation_id"] != correlation for e in clean):
        raise BoundaryError("Mixed correlation IDs")
    return {
        "correlation_id": correlation,
        "events": clean,
        "total_observed_ms": sum(e["duration_ms"] for e in clean),
        "first_failure": next((e for e in clean if e["status"] != "OK"), None),
    }


def classify_api_failure(status_code, error_class):
    table = {
        (403, "AccessDeniedException"): "IDENTITY_OR_POLICY",
        (400, "ValidationException"): "REQUEST_CONTRACT",
        (404, "ResourceNotFoundException"): "MODEL_OR_RESOURCE",
        (429, "ThrottlingException"): "QUOTA_OR_CAPACITY",
        (408, "ModelTimeoutException"): "TIMEOUT",
    }
    return table.get((status_code, error_class), "UNCLASSIFIED")


def diagnose_answer(record):
    """Identify the first observable broken boundary in a synthetic request record."""
    required = {
        "source_current",
        "retrieval_contains_required",
        "prompt_contains_retrieval",
        "output_supported",
        "tool_authorized",
    }
    if not isinstance(record, dict) or required - record.keys():
        raise BoundaryError("Incomplete diagnosis record")
    if any(type(record[k]) is not bool for k in required):
        raise BoundaryError("Diagnosis flags must be booleans")
    if not record["source_current"]:
        return "STALE_SOURCE"
    if not record["retrieval_contains_required"]:
        return "RETRIEVAL_MISS"
    if not record["prompt_contains_retrieval"]:
        return "CONTEXT_ASSEMBLY"
    if not record["output_supported"]:
        return "GENERATION_OR_VALIDATION"
    if not record["tool_authorized"]:
        return "TOOL_AUTHORIZATION"
    return "NO_OBSERVED_FAILURE"


@dataclass(frozen=True)
class Runbook:
    symptom: str
    evidence: tuple
    safe_intervention: str
    verification: str
    escalation: str

    def __post_init__(self):
        if not all(
            (
                self.symptom,
                self.evidence,
                self.safe_intervention,
                self.verification,
                self.escalation,
            )
        ):
            raise BoundaryError("Incomplete runbook")


def incident_recovery(
    *, capability_disabled, rollback_verified, impact_recorded, regression_added
):
    checks = {
        "capability_disabled": capability_disabled,
        "rollback_verified": rollback_verified,
        "impact_recorded": impact_recorded,
        "regression_added": regression_added,
    }
    if any(type(value) is not bool for value in checks.values()):
        raise BoundaryError("Recovery flags must be booleans")
    return {
        "ready_to_close": all(checks.values()),
        "checks": checks,
        "missing": [k for k, v in checks.items() if not v],
    }
