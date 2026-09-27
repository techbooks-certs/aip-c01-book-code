"""Deterministic security and governance fixtures; no AWS call is made."""

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import re

from .baseline import BoundaryError, _string


SENSITIVE_KEYS = frozenset(
    {
        "claim_text",
        "document_text",
        "email",
        "phone",
        "prompt",
        "response",
        "secret",
        "token",
        "authorization",
    }
)
AUDIT_KEYS = frozenset(
    {
        "event_id",
        "occurred_at",
        "correlation_id",
        "tenant_id",
        "actor_id",
        "actor_role",
        "action",
        "resource_id",
        "source_version",
        "model_ref",
        "prompt_version",
        "policy_version",
        "decision",
        "approval_id",
        "configuration_ref",
    }
)


def _identifier(value: str, name: str) -> str:
    _string(value, name, 100)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", value):
        raise BoundaryError(f"Invalid {name}")
    return value


@dataclass(frozen=True)
class SecurityContext:
    tenant_id: str
    actor_id: str
    roles: frozenset[str]

    def __post_init__(self):
        _identifier(self.tenant_id, "tenant_id")
        _identifier(self.actor_id, "actor_id")
        if (
            not isinstance(self.roles, frozenset)
            or not self.roles
            or any(role not in {"reader", "adjuster", "auditor"} for role in self.roles)
        ):
            raise BoundaryError("Invalid role set")


def authorize_resource(
    context: SecurityContext, resource_tenant: str, operation: str
) -> bool:
    """Authorize the tenant and role at the resource boundary."""
    _identifier(resource_tenant, "resource_tenant")
    if context.tenant_id != resource_tenant:
        raise BoundaryError("Cross-tenant access denied")
    required = {
        "read": {"reader", "adjuster", "auditor"},
        "update": {"adjuster"},
        "audit": {"auditor"},
    }
    if operation not in required or not context.roles.intersection(required[operation]):
        raise BoundaryError("Operation is not authorized")
    return True


def tenant_cache_key(
    tenant_id: str,
    resource_id: str,
    source_version: str,
    model_ref: str,
    prompt_version: str,
    *,
    permission_version: str,
) -> str:
    """Key a versioned resource lookup; reauthorize every hit.

    This is not a complete response-cache key. Responses additionally require
    request identity and every answer-changing dependency in their cache scope.
    """
    values = [
        _identifier(v, n)
        for v, n in (
            (tenant_id, "tenant_id"),
            (resource_id, "resource_id"),
            (source_version, "source_version"),
            (model_ref, "model_ref"),
            (prompt_version, "prompt_version"),
            (permission_version, "permission_version"),
        )
    ]
    return "claimsassist:" + sha256("\x1f".join(values).encode()).hexdigest()


def redact_record(record: dict) -> dict:
    """Return an allowlisted audit projection; sensitive and unknown fields never pass."""
    if not isinstance(record, dict):
        raise BoundaryError("Audit input must be an object")
    clean = {}
    for key in AUDIT_KEYS:
        if key not in record:
            continue
        value = record[key]
        if key == "occurred_at":
            if not isinstance(value, str) or not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value
            ):
                raise BoundaryError("Invalid occurred_at")
            try:
                datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                raise BoundaryError("Invalid occurred_at") from None
        else:
            _identifier(value, key)
        clean[key] = value
    return clean


def build_audit_record(**fields) -> dict:
    required = {
        "event_id",
        "occurred_at",
        "correlation_id",
        "tenant_id",
        "actor_id",
        "actor_role",
        "action",
        "resource_id",
        "source_version",
        "model_ref",
        "prompt_version",
        "policy_version",
        "decision",
    }
    clean = redact_record(fields)
    missing = required - clean.keys()
    if missing:
        raise BoundaryError("Missing audit fields: " + ", ".join(sorted(missing)))
    return dict(sorted(clean.items()))


def audit_fingerprint(record: dict) -> str:
    """Hash a canonical record so later changes are detectable."""
    clean = build_audit_record(**record)
    payload = json.dumps(clean, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode()).hexdigest()


def verify_deletion_inventory(inventory: list[dict], *, expected_stores) -> dict:
    """Validate supplied attestations for a predeclared inventory, not deletion.

    evidence_ref identifies retained operation/verification evidence; this function
    does not read that evidence or establish its truth. Retention requires a policy
    reference as well. Expected stores must originate from the lifecycle inventory.
    """
    if not isinstance(inventory, list) or not inventory:
        raise BoundaryError("Deletion inventory is empty or invalid")
    if (
        not isinstance(expected_stores, (set, frozenset, list, tuple))
        or not expected_stores
    ):
        raise BoundaryError("An explicit expected store inventory is required")
    expected = [_identifier(store, "expected_store") for store in expected_stores]
    if len(set(expected)) != len(expected):
        raise BoundaryError("Expected stores must be unique")
    terminal = {"DELETED", "RETAINED_BY_POLICY", "NOT_FOUND"}
    statuses = terminal | {"PENDING", "FAILED"}
    seen = set()
    incomplete = []
    for item in inventory:
        if (
            not isinstance(item, dict)
            or not {"store", "status"} <= item.keys()
            or set(item) - {"store", "status", "evidence_ref", "policy_ref"}
        ):
            raise BoundaryError("Invalid deletion attestation fields")
        store = _identifier(item["store"], "store")
        status = item["status"]
        if not isinstance(status, str) or status not in statuses:
            raise BoundaryError("Invalid deletion status")
        if store in seen or store not in expected:
            raise BoundaryError("Duplicate or unexpected store")
        seen.add(store)
        for field in ("evidence_ref", "policy_ref"):
            if field in item:
                _identifier(item[field], field)
        if status in terminal and "evidence_ref" not in item:
            raise BoundaryError(
                "Terminal status requires verification evidence reference"
            )
        if status == "RETAINED_BY_POLICY" and "policy_ref" not in item:
            raise BoundaryError("Retention requires a governing policy reference")
        if status not in terminal:
            incomplete.append(store)
    missing = sorted(set(expected) - seen)
    return {
        "complete": not incomplete and not missing,
        "checked": len(inventory),
        "incomplete_stores": sorted(incomplete + missing),
        "missing_stores": missing,
        "evidence_verified": False,
        "scope": "supplied_attestation_validation",
    }
