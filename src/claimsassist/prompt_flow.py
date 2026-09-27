"""Local versioned prompt workflow; not an Amazon Bedrock Flows deployment."""

import argparse
from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from string import Template

from .baseline import (
    BoundaryError,
    Claim,
    ModelConfig,
    OutputError,
    ProviderError,
    _identifier,
    _provider_category,
    _string,
    build_request,
    parse_response,
    strict_json,
)
from .cli import FixtureClient

FIELDS = {
    "prompt_id",
    "version",
    "owner",
    "model_id",
    "system",
    "template",
    "output_fields",
}
VARIABLES = {"claim_json", "evidence_json"}


def validate_asset(asset):
    if not isinstance(asset, dict) or set(asset) != FIELDS:
        raise BoundaryError("Prompt asset fields do not match the contract")
    _identifier(asset["prompt_id"], "prompt_id", r"[a-z][a-z0-9-]{0,49}")
    if type(asset["version"]) is not int or asset["version"] < 1:
        raise BoundaryError("A positive integer prompt version is required")
    _string(asset["owner"], "owner", 100)
    _string(asset["system"], "system", 4000)
    _string(asset["template"], "template", 4000)
    ModelConfig(asset["model_id"])
    if asset["output_fields"] != ["summary", "missing_information"]:
        raise BoundaryError("Prompt output contract is regressive or incompatible")
    template = Template(asset["template"])
    if not template.is_valid() or set(template.get_identifiers()) != VARIABLES:
        raise BoundaryError(
            "Prompt variables must bind claim_json and evidence_json exactly"
        )
    return hashlib.sha256(
        json.dumps(asset, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def prepare(asset, data, evidence_bundle, trusted_tenant):
    _identifier(trusted_tenant, "trusted_tenant", r"tenant-[a-z]{1,20}")
    claim = Claim.from_mapping(data)
    if claim.tenant_id != trusted_tenant:
        raise BoundaryError("Claim is outside the trusted tenant")
    asset_hash = validate_asset(asset)
    if not isinstance(evidence_bundle, dict) or set(evidence_bundle) != {
        "tenant_id",
        "items",
    }:
        raise BoundaryError("A tenant-scoped evidence envelope is required")
    if evidence_bundle["tenant_id"] != trusted_tenant:
        raise BoundaryError("Evidence is outside the trusted tenant")
    items = evidence_bundle["items"]
    if not isinstance(items, list) or len(items) > 10:
        raise BoundaryError("Evidence must contain at most ten excerpts")
    for item in items:
        if not isinstance(item, dict) or set(item) != {"source_id", "quote"}:
            raise BoundaryError("Each excerpt needs source_id and quote")
        _identifier(item["source_id"], "source_id", r"[a-z0-9][a-z0-9-]{0,49}")
        _string(item["quote"], "quote", 4000)
    if not items:
        return None, asset_hash
    rendered = Template(asset["template"]).substitute(
        claim_json=json.dumps(
            {"claim_id": claim.claim_id, "description": claim.description},
            ensure_ascii=True,
        ),
        evidence_json=json.dumps(items, ensure_ascii=True),
    )
    if len(rendered) > 12000:
        raise BoundaryError("Rendered prompt exceeds the local character budget")
    request = build_request(claim, ModelConfig(asset["model_id"]))
    request["system"] = [{"text": asset["system"]}]
    request["messages"] = [{"role": "user", "content": [{"text": rendered}]}]
    return request, asset_hash


def run_flow(asset, data, evidence_bundle, trusted_tenant, client, max_attempts=2):
    if type(max_attempts) is not int or not 1 <= max_attempts <= 2:
        raise BoundaryError("One or two application attempts are permitted")
    asset = deepcopy(asset)
    request, asset_hash = prepare(asset, data, evidence_bundle, trusted_tenant)
    if request is None:
        return dict(
            status="clarification_required",
            reason="evidence_required",
            attempts=0,
            prompt_sha256=asset_hash,
            review_required=True,
        )
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.converse(**request)
        except Exception as exc:
            raise ProviderError(_provider_category(exc)) from None
        # An intervention or incomplete completion is not a schema-repair opportunity.
        if (
            not isinstance(response, Mapping)
            or response.get("stopReason") != "end_turn"
        ):
            raise OutputError("Non-complete response; no repair retry permitted")
        try:
            draft = parse_response(response)
            return dict(
                status="draft",
                draft=draft,
                attempts=attempt,
                prompt_id=asset["prompt_id"],
                prompt_version=asset["version"],
                prompt_sha256=asset_hash,
                review_required=True,
            )
        except OutputError:
            if attempt == max_attempts:
                raise
            # Bounded generic repair instruction: do not echo invalid raw output.
            request["messages"].append(
                {
                    "role": "assistant",
                    "content": [
                        {
                            "text": "The previous draft failed the application output contract."
                        }
                    ],
                }
            )
            request["messages"].append(
                {
                    "role": "user",
                    "content": [
                        {
                            "text": "Return a complete JSON object containing only summary and missing_information. Do not invent missing facts."
                        }
                    ],
                }
            )
    raise AssertionError("Unreachable flow state")


def demonstrate(asset, data):
    evidence = dict(
        tenant_id=data["tenant_id"],
        items=[
            dict(
                source_id="synthetic-policy",
                quote="Fictional evidence checklist: record the loss date.",
            )
        ],
    )
    result = run_flow(asset, data, evidence, data["tenant_id"], FixtureClient())
    return dict(
        evidence_class="offline_prompt_fixture",
        result=result,
        limits=[
            "Fixture output does not measure prompt or model quality",
            "Local workflow only; no Prompt management or Bedrock Flows resource was created",
            "Template delimiters and scoped data do not establish prompt-injection immunity",
        ],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", type=Path, required=True)
    parser.add_argument("--claim", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or not args.output.parent.is_dir():
        parser.error("Choose a new output path in an existing directory")
    try:
        if args.asset.stat().st_size > 20000 or args.claim.stat().st_size > 20000:
            raise BoundaryError("Local input exceeds file-size limit")
        asset = strict_json(args.asset.read_text(encoding="utf-8"))
        data = strict_json(args.claim.read_text(encoding="utf-8"))
        result = demonstrate(asset, data)
    except (BoundaryError, ProviderError, OSError, UnicodeError):
        print("Prompt workflow rejected the input or output contract")
        return 2
    with args.output.open("x", encoding="utf-8") as out:
        json.dump(result, out, indent=2)
        out.write("\n")
    print("Saved local prompt workflow observation; no model-quality claim")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
