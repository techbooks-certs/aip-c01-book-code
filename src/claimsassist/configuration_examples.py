"""Offline teaching worksheets; no provisioning, media decoding or authorization."""

from copy import deepcopy
from .baseline import BoundaryError


def image_request(model_id, image_bytes):
    if not isinstance(model_id, str) or not model_id.strip():
        raise BoundaryError("Model reference required")
    if not isinstance(image_bytes, bytes) or not image_bytes.startswith(
        b"\x89PNG\r\n\x1a\n"
    ):
        raise BoundaryError("PNG bytes required; a path is not image content")
    if len(image_bytes) > 100_000:
        raise BoundaryError("Teaching image budget exceeded")
    return {
        "modelId": model_id,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"text": "Describe visible damage only; identify uncertainty."},
                    {"image": {"format": "png", "source": {"bytes": image_bytes}}},
                ],
            }
        ],
        "inferenceConfig": {"maxTokens": 128},
    }


def backend_contract():
    """Application-owned worksheet, not an OpenSearch create-index request."""
    return {
        "embedding_space": "synthetic-e1-1024",
        "dimensions": 1024,
        "engine": "faiss",
        "vectorIndexName": "claims-policy-e1",
        "fieldMapping": {
            "vectorField": "policy_vector",
            "textField": "policy_text",
            "metadataField": "kb_metadata",
        },
        "fields": {
            "policy_vector": {"kind": "vector", "dimensions": 1024},
            "policy_text": {"kind": "string", "filterable": True},
            "kb_metadata": {"kind": "string", "filterable": False},
        },
        "required_source_metadata": ["tenant_id", "source_id", "source_version"],
    }


def validate_backend_contract(contract, *, embedding_space, dimensions):
    """Check one worksheet's consistency, not AWS service support or IAM."""
    expected = backend_contract()
    if not isinstance(contract, dict) or set(contract) != set(expected):
        raise BoundaryError("Unexpected worksheet fields")
    if type(dimensions) is not int or dimensions <= 0:
        raise BoundaryError("Positive embedding dimension required")
    if (
        type(contract["dimensions"]) is not int
        or contract["embedding_space"] != embedding_space
        or contract["dimensions"] != dimensions
    ):
        raise BoundaryError("Embedding identity or dimension mismatch")
    if (
        contract["engine"] != "faiss"
        or not isinstance(contract["vectorIndexName"], str)
        or not contract["vectorIndexName"]
    ):
        raise BoundaryError("Invalid index configuration")
    fields, mapping = contract["fields"], contract["fieldMapping"]
    if (
        not isinstance(fields, dict)
        or not isinstance(mapping, dict)
        or set(mapping) != set(expected["fieldMapping"])
    ):
        raise BoundaryError("Invalid field map")
    names = list(mapping.values())
    if (
        any(not isinstance(n, str) for n in names)
        or len(set(names)) != 3
        or any(n not in fields for n in names)
    ):
        raise BoundaryError("Missing or overlapping mapped fields")
    if fields[mapping["vectorField"]] != {"kind": "vector", "dimensions": dimensions}:
        raise BoundaryError("Vector field mismatch")
    if fields[mapping["textField"]] != {"kind": "string", "filterable": True}:
        raise BoundaryError("Searchable text field required")
    if fields[mapping["metadataField"]] != {"kind": "string", "filterable": False}:
        raise BoundaryError("Bedrock metadata field mismatch")
    if contract["required_source_metadata"] != expected["required_source_metadata"]:
        raise BoundaryError("Source eligibility metadata contract changed")
    return deepcopy(mapping)
