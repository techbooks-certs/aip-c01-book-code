"""Joined read-only managed boundaries with explicit trusted dependency injection.

Default CLI executes local SDK-shaped fixtures, never AWS. Server integrations
supply authenticated context and a current authoritative source predicate. No
approval, payment or business-write capability exists in this path.
"""

import argparse
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic_ns
from uuid import uuid4

from .baseline import BoundaryError, _string
from .governance import (
    SecurityContext,
    authorize_resource,
    build_audit_record,
    _identifier,
)
from .managed import ManagedPassage, assess_text, retrieve_permitted
from .observability import assemble_trace, sanitize_event
from .rag_answer import ConverseDraftProvider, draft_answer


@dataclass(frozen=True)
class AssistanceConfig:
    knowledge_base_id: str
    guardrail_id: str
    guardrail_version: str
    model_id: str
    prompt_version: str
    policy_version: str
    max_evidence_chars: int = 6000

    def __post_init__(self):
        _string(self.knowledge_base_id, "knowledge_base_id", 10)
        _string(self.guardrail_id, "guardrail_id", 2048)
        if (
            not isinstance(self.guardrail_version, str)
            or not self.guardrail_version.isascii()
            or not self.guardrail_version.isdigit()
            or self.guardrail_version.startswith("0")
            or len(self.guardrail_version) > 8
        ):
            raise BoundaryError("Published guardrail version required")
        _string(self.model_id, "model_id", 2048)
        _identifier(self.prompt_version, "prompt_version")
        _identifier(self.policy_version, "policy_version")
        if (
            type(self.max_evidence_chars) is not int
            or not 1 <= self.max_evidence_chars <= 10000
        ):
            raise BoundaryError("Invalid evidence budget")


def assist(
    question,
    *,
    context,
    resource_tenant,
    resource_id,
    config,
    retrieval_client,
    runtime_client,
    source_allowed,
    audit_sink,
    correlation_id=None,
):
    """One bounded request: authorize, assess, retrieve, draft, reassess, audit.

    source_allowed(tenant, source, version) must close over current claim/date and
    permission policy, not request-supplied assertions. It is called by retrieval,
    immediately before model invocation, and before releasing the draft. These
    checks do not form a distributed transaction; use stronger leases/versioned
    reads where the real application's consistency requirements demand them.
    audit_sink must persist successfully before any draft is returned. Audit
    failure withholds the answer. Dependencies are trusted application objects.
    """
    if not isinstance(context, SecurityContext) or not isinstance(
        config, AssistanceConfig
    ):
        raise BoundaryError("Validated trusted context/configuration required")
    if not callable(source_allowed) or not callable(audit_sink):
        raise BoundaryError("Trusted authorization and audit dependencies required")
    question = _string(question, "question", 1000)
    _identifier(resource_id, "resource_id")
    correlation = correlation_id or "corr-" + uuid4().hex
    # Validate trace identifier before any service call.
    sanitize_event(
        dict(correlation_id=correlation, stage="auth", status="OK", duration_ms=0)
    )
    events = []
    audit = []
    answer = None
    evidence = []
    passages: tuple[ManagedPassage, ...] = ()
    status = "failed"
    failure_boundary = None

    def execute(stage, operation):
        nonlocal failure_boundary
        started = monotonic_ns()
        state = "OK"
        try:
            return operation()
        except Exception:
            state = "ERROR"
            if failure_boundary is None:
                failure_boundary = stage
            raise
        finally:
            events.append(
                sanitize_event(
                    dict(
                        correlation_id=correlation,
                        stage=stage,
                        status=state,
                        duration_ms=max(0, monotonic_ns() - started) // 1_000_000,
                    )
                )
            )

    def check_text(text, source):
        decision = assess_text(
            runtime_client,
            guardrail_id=config.guardrail_id,
            version=config.guardrail_version,
            source=source,
            text=text,
        )
        if decision.allowed is not True:
            raise BoundaryError("Content withheld by safety boundary")

    def reauthorize():
        authorize_resource(context, resource_tenant, "read")
        for passage in passages:
            if (
                source_allowed(
                    context.tenant_id, passage.source_id, passage.source_version
                )
                is not True
            ):
                raise BoundaryError("Current source authorization denied")

    try:
        execute("auth", lambda: authorize_resource(context, resource_tenant, "read"))
        execute("prompt", lambda: check_text(question, "INPUT"))
        passages = execute(
            "retrieval",
            lambda: retrieve_permitted(
                retrieval_client,
                knowledge_base_id=config.knowledge_base_id,
                trusted_tenant=context.tenant_id,
                query=question,
                source_allowed=source_allowed,
                limit=5,
            ),
        )

        def assemble():
            for index, passage in enumerate(passages):
                citation = "evidence-" + str(index + 1)
                evidence.append(
                    {
                        "citation_id": citation,
                        "quote": passage.text,
                        "source_id": passage.source_id,
                        "source_version": passage.source_version,
                    }
                )
            if (
                len(json.dumps(evidence, ensure_ascii=False))
                > config.max_evidence_chars
            ):
                raise BoundaryError("Complete evidence exceeds context budget")
            for passage in passages:
                check_text(passage.text, "INPUT")
            reauthorize()

        execute("prompt", assemble)
        # Empty evidence abstains before constructing a model invocation.
        provider = ConverseDraftProvider(runtime_client, config.model_id)

        def measured_provider(request):
            return execute("model", lambda: provider(request))

        model_before = sum(e["duration_ms"] for e in events if e["stage"] == "model")
        try:
            answer = execute(
                "response", lambda: draft_answer(question, evidence, measured_provider)
            )
        finally:
            # The enclosing response validation duration excludes nested model time.
            model_elapsed = (
                sum(e["duration_ms"] for e in events if e["stage"] == "model")
                - model_before
            )
            if events and events[-1]["stage"] == "response":
                events[-1]["duration_ms"] = max(
                    0, events[-1]["duration_ms"] - model_elapsed
                )
        if answer["claims"]:
            execute(
                "response",
                lambda: check_text(
                    json.dumps(answer["claims"], ensure_ascii=False), "OUTPUT"
                ),
            )
        execute("auth", reauthorize)
        status = answer["status"]
    except Exception:
        # Preserve typed boundary and timings, never raw provider payload/errors.
        answer = None
        status = "failed"

    evidence_identity = sha256(
        json.dumps(
            [
                (p.source_id, p.source_version, sha256(p.text.encode()).hexdigest())
                for p in passages
            ],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    record = build_audit_record(
        event_id="event-" + uuid4().hex,
        occurred_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        correlation_id=correlation,
        tenant_id=context.tenant_id,
        actor_id=context.actor_id,
        actor_role=next(
            role for role in ("adjuster", "reader", "auditor") if role in context.roles
        ),
        action="assistance.draft",
        resource_id=resource_id,
        source_version="sha256-" + evidence_identity,
        model_ref="sha256-" + sha256(config.model_id.encode()).hexdigest(),
        prompt_version=config.prompt_version,
        policy_version=config.policy_version,
        configuration_ref="sha256-"
        + sha256(
            json.dumps(asdict(config), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        decision="WITHHELD" if status == "failed" else "REVIEW_REQUIRED",
    )
    try:
        audit_sink(record)
        audit.append(record)
    except Exception:
        answer = None
        status = "failed"
        failure_boundary = "audit"
        events.append(
            sanitize_event(
                dict(
                    correlation_id=correlation,
                    stage="response",
                    status="ERROR",
                    duration_ms=0,
                    error_class="AUDIT_PERSISTENCE",
                )
            )
        )
    return {
        "status": status,
        "answer": answer,
        "failure_boundary": failure_boundary,
        "correlation_id": correlation,
        "trace": assemble_trace(events),
        "evidence": evidence if answer is not None else [],
        "audit_records": audit,
        "audit_persisted": bool(audit),
        "semantic_support": "NOT_EVALUATED",
        "review_required": True,
        "business_write_available": False,
        "guardrail_version": config.guardrail_version,
    }


class FixtureRuntime:
    """SDK-shaped authored responses; not a safety classifier or live model."""

    def apply_guardrail(self, **request):
        return {"action": "NONE"}

    def converse(self, **request):
        prompt = json.loads(request["messages"][0]["content"][0]["text"])
        result = {
            "claims": [
                {
                    "text": "Authored fixture draft for independent policy review.",
                    "citations": [prompt["evidence"][0]["citation_id"]],
                }
            ],
            "abstained": False,
        }
        return {
            "stopReason": "end_turn",
            "output": {"message": {"content": [{"text": json.dumps(result)}]}},
        }


class FixtureRetrieval:
    def retrieve(self, **request):
        return {
            "retrievalResults": [
                {
                    "content": {
                        "type": "TEXT",
                        "text": "Fictional policy: gradual corrosion is excluded; documented maintenance exceptions require review.",
                    },
                    "metadata": {
                        "tenant_id": "tenant-amber",
                        "source_id": "pol-4821",
                        "source_version": "v1",
                    },
                }
            ]
        }


def demonstrate():
    records: list[dict[str, str]] = []
    report = assist(
        "Explain the fictional corrosion exception.",
        context=SecurityContext(
            "tenant-amber", "adjuster-demo", frozenset({"adjuster"})
        ),
        resource_tenant="tenant-amber",
        resource_id="claim-0001",
        config=AssistanceConfig(
            "ABCDEFGHIJ",
            "guardrail-demo",
            "1",
            "fixture-model",
            "draft-v1",
            "policy-v1",
        ),
        retrieval_client=FixtureRetrieval(),
        runtime_client=FixtureRuntime(),
        source_allowed=lambda tenant, source, version: (tenant, source, version)
        == ("tenant-amber", "pol-4821", "v1"),
        audit_sink=records.append,
    )
    report.update(
        scope="executed_local_joined_sdk_shaped_fixtures",
        live_aws="NOT_RUN",
        audit_storage="in_memory_fixture_then_output_report",
        quality_evaluated=False,
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # Same existing-output/missing-directory guard used by the sibling CLIs
    # (index_demo.py, deployment_cli.py): fail with a clear message before
    # doing any work, rather than letting a bare open("x", ...) raise an
    # unhandled traceback at a reader who reruns this exact command. The
    # reservation is also released on any failure from demonstrate() itself
    # (matching runtime_client.py/stack_cleanup.py/etc.), since here the
    # fixture run happens inside the reservation rather than before it.
    if args.output.exists() or not args.output.parent.is_dir():
        parser.error("Choose a new output path in an existing directory")
    with args.output.open("x") as handle:
        pass
    try:
        report = demonstrate()
        with args.output.open("w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
            handle.write("\n")
    except BaseException:
        args.output.unlink(missing_ok=True)
        raise
    if report["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
