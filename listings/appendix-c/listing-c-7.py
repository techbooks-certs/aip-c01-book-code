# Listing C.7 [I] — Wire the managed adapters
# AWS Generative AI Developer in Practice

from claimsassist.managed import assess_text, retrieve_permitted
from claimsassist.baseline import BoundaryError

# runtime and agent_runtime are configured SDK clients.
# trusted_tenant comes from authenticated application context.
# Supply permission_store from your application.
decision = assess_text(
    runtime,
    guardrail_id=configured_guardrail_id,
    version=configured_guardrail_version,
    source="INPUT",
    text=validated_question,
)
if not decision.allowed:
    raise BoundaryError("Input assessment withheld the request")
passages = retrieve_permitted(
    agent_runtime,
    knowledge_base_id=configured_kb_id,
    trusted_tenant=trusted_tenant,
    query=validated_question,
    source_allowed=permission_store.is_currently_allowed,
)
