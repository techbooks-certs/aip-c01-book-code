"""Local integrated ClaimsAssist exercise: exact extracts and pending approval only.

Caller identity is a trusted application input in this simulation. There is no
authentication server, LLM, AWS service, email delivery or payment integration.
"""

import argparse
import json
from pathlib import Path
import tempfile

from .approval import ApprovalLedger
from .baseline import BoundaryError
from .governance import SecurityContext, authorize_resource
from .guardrails import GuardrailConfig, OfflineGuardrail, authorize_tool_call
from .rag_demo import build_index
from .retrieval import retrieve, validate_extracts


def stage_assistance(
    *,
    context,
    resource_tenant,
    claim,
    request_id,
    loss_date,
    query,
    index,
    guardrail,
    ledger,
    now,
    policy_version="synthetic-policy-v1",
    permission_epoch="synthetic-permissions-v1",
):
    """Connect scope, input safety, retrieval, document safety and approval staging.

    A safety MASK on a document is withheld rather than silently altering a quote.
    Only one complete extract is staged. Multiple extracts require an explicit
    selection in a larger application. This function never approves its proposal.
    """
    authorize_resource(context, resource_tenant, "update")
    checked = guardrail.apply(query, "INPUT")
    if checked["action"] == "BLOCK":
        return {"status": "blocked", "boundary": "input", "simulation_only": True}
    result = retrieve(
        index,
        context.tenant_id,
        loss_date,
        checked["output"],
        mode="hybrid",
        rerank=True,
    )
    if result["status"] != "evidence_available":
        return {
            "status": result["status"],
            "reason": result["reason"],
            "simulation_only": True,
        }
    eligible = []
    for item in result["evidence"]:
        inspected = guardrail.apply(item["quote"], "DOCUMENT")
        if inspected["action"] == "NONE":
            eligible.append(item)
    if not eligible:
        return {
            "status": "abstain",
            "reason": "no_safe_complete_extract",
            "simulation_only": True,
        }
    selected = eligible[0]
    authorize_tool_call(checked, "lookup_policy", {"source_id": selected["source_id"]})
    extracts = validate_extracts(
        {
            "extracts": [
                {"citation_id": selected["citation_id"], "quote": selected["quote"]}
            ]
        },
        eligible,
    )
    message = f"Review extract {selected['citation_id']}: {selected['quote']}"
    if len(message) > 500:
        return {
            "status": "review_required",
            "reason": "complete_extract_too_long",
            "simulation_only": True,
        }
    if guardrail.apply(message, "OUTPUT")["action"] != "NONE":
        return {"status": "blocked", "boundary": "output", "simulation_only": True}
    proposal = ledger.propose(
        context.tenant_id,
        claim,
        request_id,
        selected["source_id"],
        message,
        now,
        source_version=selected["version"],
        policy_version=policy_version,
        permission_epoch=permission_epoch,
    )
    status = {
        "PENDING": "awaiting_review",
        "APPROVED": "approved",
        "EXECUTED": "recorded_locally",
    }[proposal["state"]]
    return {
        "status": status,
        "proposal": proposal,
        "extracts": extracts["extracts"],
        "simulation_only": True,
    }


def demonstrate():
    context = SecurityContext("tenant-amber", "adjuster-one", frozenset({"adjuster"}))
    guardrail = OfflineGuardrail(GuardrailConfig("gr-1", frozenset(), frozenset()))
    with tempfile.TemporaryDirectory(prefix="claimsassist-capstone-") as directory:
        path = Path(directory) / "approval.sqlite"
        ledger = ApprovalLedger(path)
        index = build_index()
        staged = stage_assistance(
            context=context,
            resource_tenant="tenant-amber",
            claim="claim-0001",
            request_id="capstone-1",
            loss_date="2026-09-20",
            query="water damage from gradual pipe corrosion",
            index=index,
            guardrail=guardrail,
            ledger=ledger,
            now=1000,
        )
        proposal = staged["proposal"]
        execution = dict(
            pid=proposal["proposal_id"],
            tenant=context.tenant_id,
            current_claim="claim-0001",
            current_status="open",
            actor_role="adjuster",
            now=1002,
            current_source_allowed=True,
            current_source_version="v1",
            current_policy_version="synthetic-policy-v1",
            current_permission_epoch="synthetic-permissions-v1",
        )
        try:
            ledger.execute_simulation(**execution)
        except BoundaryError:
            pending_rejected = True
        else:
            raise AssertionError("A pending proposal executed without approval")
        # Simulates an authenticated reviewer outside the automated staging path.
        ledger.approve(
            proposal["proposal_id"],
            context.tenant_id,
            proposal["fingerprint"],
            context.actor_id,
            "adjuster",
            1001,
        )
        ledger = ApprovalLedger(path)  # Resume from durable local state.
        receipt = ledger.execute_simulation(**execution)
        replay = ledger.execute_simulation(**execution)
    return {
        "scope": "local_synthetic_integration",
        "staged": staged,
        "pending_execution_rejected": pending_rejected,
        "receipt": receipt,
        "replay_identical": receipt == replay,
        "temporary_ledger_removed": not path.exists(),
        "limitations": [
            "Identity, clock and source authorization are trusted test inputs",
            "Reviewer action is simulated, not a real human approval",
            "Exact extracts only; no generated answer or semantic support judge",
            "No AWS, AgentCore deployment, model inference or external action",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    # Same existing-output/missing-directory guard used by the sibling CLIs
    # (index_demo.py, deployment_cli.py): fail with a clear message before
    # doing any work, rather than letting a bare open("x", ...) raise an
    # unhandled traceback at a reader who reruns this exact command.
    if args.output.exists() or not args.output.parent.is_dir():
        parser.error("Choose a new output path in an existing directory")
    report = demonstrate()
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")
    print("Saved local capstone evidence; no external action occurred")


if __name__ == "__main__":
    main()
