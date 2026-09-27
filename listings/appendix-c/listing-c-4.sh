#!/usr/bin/env bash
# Listing C.4 [R] — Inspect a sanitized telemetry event
# AWS Generative AI Developer in Practice

PYTHONPATH=src python - <<'PY'
from claimsassist.observability import sanitize_event
clean = sanitize_event({
    "correlation_id": "corr-lab-1",
    "stage": "retrieval",
    "status": "OK",
    "duration_ms": 120,
    "document_ids": ["pol-4821"],
    "document_text": "Synthetic private payload"
})
assert "document_text" not in clean
assert clean["document_ids"] == ["pol-4821"]
print(clean)
PY
