"""Pinned Strands SDK with a scripted model; local tools and proposal-only authority."""

import argparse
import asyncio
from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
import time

from jsonschema import ValidationError, validate
from strands import Agent, tool
from strands.hooks import BeforeToolCallEvent
from strands.models import Model
from strands.tools.executors import SequentialToolExecutor

from .approval import ApprovalLedger
from .baseline import BoundaryError, _identifier
from .rag_demo import build_index
from .vector_index import iso_date

SCHEMAS = {
    "lookup_policy": dict(
        type="object",
        properties={
            "source_id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]{0,49}$"}
        },
        required=["source_id"],
        additionalProperties=False,
    ),
    "sum_amounts_cents": dict(
        type="object",
        properties={
            "amounts_cents": {
                "type": "array",
                "minItems": 1,
                "maxItems": 20,
                "items": {"type": "integer", "minimum": 0, "maximum": 100000000},
            }
        },
        required=["amounts_cents"],
        additionalProperties=False,
    ),
    "propose_evidence_request": dict(
        type="object",
        properties={
            "request_id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]{0,49}$"},
            "source_id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]{0,49}$"},
            "message": {"type": "string", "minLength": 1, "maxLength": 500},
        },
        required=["request_id", "source_id", "message"],
        additionalProperties=False,
    ),
}


@dataclass(frozen=True)
class RequestContext:
    tenant: str
    claim_id: str
    loss_date: str
    policy_version: str = "synthetic-policy-v1"
    permission_epoch: str = "synthetic-permissions-v1"

    def __post_init__(self):
        _identifier(self.tenant, "tenant", r"tenant-[a-z]{1,20}")
        _identifier(self.claim_id, "claim", r"claim-[0-9]{4,12}")
        iso_date(self.loss_date)


class ScriptedModel(Model):
    """SDK event fixture, not a learned model or a simulated quality benchmark."""

    def __init__(self, steps):
        self.steps = deepcopy(steps)
        self.calls = 0

    def update_config(self, **config):
        if config:
            raise ValueError("The scripted fixture has no tunable model parameters")

    def get_config(self):
        return {"model_id": "claimsassist-scripted-fixture"}

    async def structured_output(self, *args, **kwargs):
        raise NotImplementedError("Structured output is not part of this fixture")
        yield {}  # pragma: no cover -- establishes the async-generator interface

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        position = self.calls
        self.calls += 1
        step = (
            self.steps[position]
            if position < len(self.steps)
            else {"text": "Fixture completed; proposals still require external review."}
        )
        yield {"messageStart": {"role": "assistant"}}
        if "tool" in step:
            yield {
                "contentBlockStart": {
                    "contentBlockIndex": 0,
                    "start": {
                        "toolUse": {
                            "toolUseId": f"fixture-{position}",
                            "name": step["tool"],
                        }
                    },
                }
            }
            yield {
                "contentBlockDelta": {
                    "contentBlockIndex": 0,
                    "delta": {"toolUse": {"input": json.dumps(step["input"])}},
                }
            }
            reason = "tool_use"
        else:
            yield {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}}
            yield {
                "contentBlockDelta": {
                    "contentBlockIndex": 0,
                    "delta": {"text": step["text"]},
                }
            }
            reason = "end_turn"
        yield {"contentBlockStop": {"contentBlockIndex": 0}}
        yield {"messageStop": {"stopReason": reason}}
        yield {
            "metadata": {
                "usage": {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0},
                "metrics": {"latencyMs": 0},
            }
        }


def create_agent(
    context,
    ledger,
    model,
    *,
    clock=lambda: int(time.time()),
    lookup_backend=None,
    tool_budget=5,
    timeout=0.05,
):
    if type(tool_budget) is not int or not 1 <= tool_budget <= 10:
        raise BoundaryError("Invalid tool budget")
    if type(timeout) not in (int, float) or not 0 < timeout <= 5:
        raise BoundaryError("Invalid timeout")
    index = build_index()
    audit: list[dict[str, str | bool]] = []
    count = 0

    async def default_lookup(source_id):
        return [
            dict(
                source_id=p.source_id,
                version=p.version,
                location=p.location,
                quote=p.text,
            )
            for p in index.eligible_passages(context.tenant, context.loss_date)
            if p.source_id == source_id
        ]

    backend = lookup_backend or default_lookup

    @tool(inputSchema=SCHEMAS["lookup_policy"])
    async def lookup_policy(source_id: str) -> dict:
        """Read an eligible fictional policy by source ID within the fixed caller context."""
        _identifier(source_id, "source", r"[a-z0-9][a-z0-9-]{0,49}")
        try:
            found = await asyncio.wait_for(backend(source_id), timeout=timeout)
        except TimeoutError:
            audit.append(dict(event="lookup_timeout", source_id=source_id))
            return dict(status="timeout", evidence=[])
        return dict(status="found" if found else "not_found", evidence=found)

    @tool(inputSchema=SCHEMAS["sum_amounts_cents"])
    def sum_amounts_cents(amounts_cents: list[int]) -> dict:
        """Add one to twenty bounded integer-cent values; never evaluate expressions."""
        if not 1 <= len(amounts_cents) <= 20 or any(
            type(v) is not int or not 0 <= v <= 100000000 for v in amounts_cents
        ):
            raise BoundaryError("Invalid integer-cent values")
        return dict(total_cents=sum(amounts_cents), status="calculated")

    @tool(inputSchema=SCHEMAS["propose_evidence_request"])
    def propose_evidence_request(request_id: str, source_id: str, message: str) -> dict:
        """Record a local request proposal requiring external approval; never send a message."""
        versions = {
            p.version
            for p in index.eligible_passages(context.tenant, context.loss_date)
            if p.source_id == source_id
        }
        if not versions:
            raise BoundaryError("Source is unavailable in the current scope")
        if len(versions) != 1:
            raise BoundaryError("One authoritative source version is required")
        proposal = ledger.propose(
            context.tenant,
            context.claim_id,
            request_id,
            source_id,
            message,
            clock(),
            source_version=next(iter(versions)),
            policy_version=context.policy_version,
            permission_epoch=context.permission_epoch,
        )
        audit.append(
            dict(event="proposal_recorded", proposal_id=proposal["proposal_id"])
        )
        return proposal

    def enforce(event: BeforeToolCallEvent):
        nonlocal count
        count += 1
        name = event.tool_use["name"]
        if count > tool_budget:
            event.cancel_tool = "Application tool-call budget exhausted"
        elif name not in SCHEMAS:
            event.cancel_tool = "Tool not in application allowlist"
        else:
            try:
                validate(event.tool_use["input"], SCHEMAS[name])
            except ValidationError:
                event.cancel_tool = "Tool argument contract rejected"
        audit.append(
            dict(event="tool_boundary", name=name, allowed=not bool(event.cancel_tool))
        )

    agent = Agent(
        model=model,
        tools=[lookup_policy, sum_amounts_cents, propose_evidence_request],
        system_prompt="Research fictional claim evidence. Use read-only tools and propose requests only. Never claim a proposal was approved or sent.",
        hooks=[enforce],
        tool_executor=SequentialToolExecutor(),
        callback_handler=None,
        context_manager=False,
        retry_strategy=None,
    )
    return agent, audit


def demonstration(path):
    context = RequestContext("tenant-amber", "claim-0001", "2026-09-20")
    ledger = ApprovalLedger(path)
    model = ScriptedModel(
        [
            {"tool": "lookup_policy", "input": {"source_id": "pol-4821"}},
            {"tool": "sum_amounts_cents", "input": {"amounts_cents": [1250, 2750]}},
            {
                "tool": "propose_evidence_request",
                "input": {
                    "request_id": "request-0001",
                    "source_id": "pol-4821",
                    "message": "Please provide the loss date and supporting photographs.",
                },
            },
        ]
    )
    agent, audit = create_agent(context, ledger, model, clock=lambda: 1000)
    result = agent(
        "Research the supplied fictional policy and prepare a request proposal.",
        limits={"turns": 5},
    )
    return dict(
        evidence_class="offline_strands_sdk_fixture",
        sdk_version="1.56.0",
        model_calls=model.calls,
        stop_reason=result.stop_reason,
        audit=audit,
        messages=agent.messages,
        limits=[
            "Actual Strands SDK executed with scripted model events, not live inference",
            "No approval/execution tool is available to the agent",
            "SQLite proposal state is local; no real request was sent",
        ],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        args.output.exists()
        or not args.output.parent.is_dir()
        or not args.ledger.parent.is_dir()
    ):
        parser.error("Choose a new report and existing parent directories")
    report = demonstration(args.ledger)
    with args.output.open("x", encoding="utf-8") as out:
        json.dump(report, out, indent=2)
        out.write("\n")
    print("Saved real SDK / scripted-model evidence; approval remains pending")


if __name__ == "__main__":
    main()
