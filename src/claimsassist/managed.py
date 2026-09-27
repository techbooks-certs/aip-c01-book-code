"""Optional managed-service adapters for configured AWS clients.

Construct clients with the approved account/Region outside this module. Tenant
context and the authoritative source predicate must come from trusted application
code, never from model-generated arguments or request-controlled assertions.
"""

from dataclasses import dataclass
from typing import Callable
from botocore.exceptions import BotoCoreError, ClientError
from .baseline import BoundaryError, ProviderError, _string


def _call(client, method, **kwargs):
    try:
        return getattr(client, method)(**kwargs)
    except (ClientError, BotoCoreError):
        # Do not expose SDK exception text, which can include request details.
        raise ProviderError("provider_failure") from None


@dataclass(frozen=True)
class GuardrailDecision:
    allowed: bool
    action: str
    version: str


def assess_text(client, *, guardrail_id, version, source, text):
    """Conservative policy: every intervention withholds content.

    This intentionally does not implement a masking/reuse policy. Returned
    replacement text and detailed assessments can contain sensitive data and are
    not exposed by this boundary. An SDK failure is not an allow decision.
    """
    guardrail_id = _string(guardrail_id, "guardrail_id", 2048)
    if (
        not isinstance(version, str)
        or not 1 <= len(version) <= 8
        or version.startswith("0")
        or not version.isascii()
        or not version.isdigit()
        or not 1 <= int(version) <= 99999999
    ):
        raise BoundaryError("Use an explicit published guardrail version")
    if source not in ("INPUT", "OUTPUT"):
        raise BoundaryError("Invalid guardrail source")
    text = _string(text, "text", 20000)
    result = _call(
        client,
        "apply_guardrail",
        guardrailIdentifier=guardrail_id,
        guardrailVersion=version,
        source=source,
        content=[{"text": {"text": text}}],
    )
    action = result.get("action")
    if action not in ("NONE", "GUARDRAIL_INTERVENED"):
        raise BoundaryError("Unknown managed guardrail action")
    return GuardrailDecision(action == "NONE", action, version)


@dataclass(frozen=True)
class ManagedPassage:
    source_id: str
    source_version: str
    text: str


def retrieve_permitted(
    client,
    *,
    knowledge_base_id,
    trusted_tenant,
    query,
    source_allowed: Callable[[str, str, str], bool],
    limit=5,
):
    """Return a bounded first page of text passages after two eligibility checks.

    Required ingestion metadata: tenant_id, source_id, source_version. The
    supplied predicate must check current permission/effectivity in an
    authoritative store. Pagination is deliberately not automatic.
    """
    knowledge_base_id = _string(knowledge_base_id, "knowledge_base_id", 10)
    tenant = _string(trusted_tenant, "trusted_tenant", 80)
    query = _string(query, "query", 1000)
    if type(limit) is not int or not 1 <= limit <= 20 or not callable(source_allowed):
        raise BoundaryError("Invalid retrieval limit or permission resolver")
    response = _call(
        client,
        "retrieve",
        knowledgeBaseId=knowledge_base_id,
        retrievalQuery={"text": query},
        retrievalConfiguration={
            "vectorSearchConfiguration": {
                "numberOfResults": limit,
                "filter": {"equals": {"key": "tenant_id", "value": tenant}},
            }
        },
    )
    rows = response.get("retrievalResults")
    if not isinstance(rows, list) or len(rows) > limit:
        raise BoundaryError("Invalid bounded retrieval response")
    passages = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("metadata"), dict):
            raise BoundaryError("Missing retrieval metadata")
        metadata = row["metadata"]
        if metadata.get("tenant_id") != tenant:
            raise BoundaryError("Retrieved tenant mismatch")
        source_id = _string(metadata.get("source_id"), "source_id", 200)
        version = _string(metadata.get("source_version"), "source_version", 100)
        # Refuse truthy non-booleans and denied/stale sources; never return them.
        if source_allowed(tenant, source_id, version) is not True:
            raise BoundaryError("Source is not currently permitted")
        content = row.get("content")
        if not isinstance(content, dict) or content.get("type", "TEXT") != "TEXT":
            raise BoundaryError("Expected a text retrieval result")
        text = _string(content.get("text"), "retrieved_text", 50000)
        passages.append(ManagedPassage(source_id, version, text))
    return tuple(passages)
