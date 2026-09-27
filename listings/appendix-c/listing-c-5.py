# Listing C.5 [I] — ApplyGuardrail request fragment
# AWS Generative AI Developer in Practice

response = runtime.apply_guardrail(
    # Replace with your deployed guardrail.
    guardrailIdentifier="abc123",
    # Use a published version rather than DRAFT.
    guardrailVersion="1",
    source="INPUT",
    content=[{"text": {"text": "Synthetic request"}}],
)
