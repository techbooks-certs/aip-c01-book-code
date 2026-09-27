"""Executed local intake-to-review journey; no AWS or external business action.

Identity, policy catalog, reviewer and model outputs are synthetic fixtures.
The dependency-injected assistance boundary remains the managed-adapter seam.
"""

from contextlib import closing as _closing_connection

import argparse
from dataclasses import asdict, dataclass
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

from .approval import ApprovalLedger
from .baseline import BoundaryError, Claim, strict_json, _string
from .deployment import ReleaseRegistry, _digest
from .evidence import EVIDENCE_ROLES, verify_evidence_files
from .governance import SecurityContext, authorize_resource, _identifier
from .intake import process_batch
from .managed_assistance import AssistanceConfig, FixtureRuntime, assist

POLICY_TEXT = (
    "Fictional policy PL-000042: retain photographs and a repair estimate "
    "for a roof leak. An adjuster must review the evidence. This passage "
    "does not establish coverage or authorize payment."
)


@dataclass
class SyntheticAuthority:
    """Mutable local authoritative fixture; never a request-supplied permission."""

    tenant: str = "tenant-amber"
    claim_id: str = "claim-0101"
    policy_number: str = "PL-000042"
    source_id: str = "pol-000042"
    source_version: str = "v1"
    policy_version: str = "policy-v1"
    permission_epoch: str = "epoch-v1"
    allowed: bool = True
    claim_status: str = "open"

    def permits(self, prepared, tenant, source, version):
        fields = prepared["fields"]
        try:
            effective = (
                date(2026, 1, 1)
                <= date.fromisoformat(fields["loss_date"])
                < date(2027, 1, 1)
            )
        except (TypeError, ValueError):
            return False
        return (
            self.allowed is True
            and effective
            and (
                tenant,
                prepared["claim"]["claim_id"],
                fields["policy_number"],
                source,
                version,
            )
            == (
                self.tenant,
                self.claim_id,
                self.policy_number,
                self.source_id,
                self.source_version,
            )
        )


class PolicyRetrieval:
    """SDK-shaped read-only fixture; the authority predicate remains independent."""

    def __init__(self, authority):
        self.authority = authority
        self.queries = []

    def retrieve(self, **request):
        self.queries.append(request["retrievalQuery"]["text"])
        a = self.authority
        return {
            "retrievalResults": [
                {
                    "content": {"type": "TEXT", "text": POLICY_TEXT},
                    "metadata": {
                        "tenant_id": a.tenant,
                        "source_id": a.source_id,
                        "source_version": a.source_version,
                    },
                }
            ]
        }


class AuthoredDraftRuntime(FixtureRuntime):
    """Authored exact-extract response; not a real model or semantic evaluator."""

    def __init__(self, fail=False):
        self.fail = fail

    def converse(self, **request):
        packet = json.loads(request["messages"][0]["content"][0]["text"])
        evidence = packet["evidence"][0]
        answer = {
            "claims": [
                {
                    "text": evidence["text"],
                    "citations": [
                        "unavailable" if self.fail else evidence["citation_id"]
                    ],
                }
            ],
            "abstained": False,
        }
        return {
            "stopReason": "end_turn",
            "output": {"message": {"content": [{"text": json.dumps(answer)}]}},
        }


def draft_prepared(
    prepared,
    *,
    context,
    authority,
    config,
    retrieval_client,
    runtime_client,
    audit_sink,
    correlation_id,
    release_id,
):
    """Use actual accepted intake content; reject missing/applicability facts first."""
    _identifier(release_id, "release_id")
    _identifier(correlation_id, "correlation_id")
    claim = Claim.from_mapping(prepared["claim"])
    authorize_resource(context, claim.tenant_id, "update")
    fields = prepared["fields"]
    if fields["claim_id"] != claim.claim_id or not authority.permits(
        prepared, context.tenant_id, authority.source_id, authority.source_version
    ):
        raise BoundaryError("Prepared claim lacks current applicable policy authority")
    if config.policy_version != authority.policy_version:
        raise BoundaryError("Configuration policy revision differs from authority")
    # Description, policy identifier and actual loss date all reach the draft request.
    question = _string(
        json.dumps(
            {
                "task": "Draft an evidence checklist for adjuster review",
                "claim_id": claim.claim_id,
                "description": claim.description,
                "policy_number": fields["policy_number"],
                "loss_date": fields["loss_date"],
            }
        ),
        "question",
        1000,
    )
    report = assist(
        question,
        context=context,
        resource_tenant=claim.tenant_id,
        resource_id=claim.claim_id,
        config=config,
        retrieval_client=retrieval_client,
        runtime_client=runtime_client,
        source_allowed=lambda tenant, source, version: authority.permits(
            prepared, tenant, source, version
        ),
        audit_sink=audit_sink,
        correlation_id=correlation_id,
    )
    return {
        "release_id": release_id,
        "correlation_id": correlation_id,
        "intake_sha256": _digest(prepared),
        "assistance": report,
        "authority_context": {
            "policy_version": authority.policy_version,
            "permission_epoch": authority.permission_epoch,
        },
    }


def stage_proposal(draft, prepared, *, context, authority, ledger, request_id, now):
    """Prepare only; approval/execution cannot be reached through this function."""
    authorize_resource(context, prepared["claim"]["tenant_id"], "update")
    report = draft["assistance"]
    if draft["authority_context"] != {
        "policy_version": authority.policy_version,
        "permission_epoch": authority.permission_epoch,
    }:
        raise BoundaryError("Draft authority changed; obtain a fresh draft and review")
    if draft["intake_sha256"] != _digest(prepared):
        raise BoundaryError("Prepared intake changed after drafting")
    if report["status"] != "draft_for_review" or not report["audit_persisted"]:
        raise BoundaryError("Only an audited draft can become a proposal")
    if len(report["evidence"]) != 1:
        raise BoundaryError("This bounded review proposal requires exactly one source")
    source = report["evidence"][0]
    if not authority.permits(
        prepared, context.tenant_id, source["source_id"], source["source_version"]
    ):
        raise BoundaryError("Source permission changed before staging")
    packet = {
        "correlation_id": draft["correlation_id"],
        "release_id": draft["release_id"],
        "intake_sha256": draft["intake_sha256"],
        "answer": report["answer"],
        "evidence": report["evidence"],
    }
    packet_hash = _digest(packet)
    message = f"Review local draft packet sha256-{packet_hash}; release {draft['release_id']}; correlation {draft['correlation_id']}. No business action."
    proposal = ledger.propose(
        context.tenant_id,
        prepared["claim"]["claim_id"],
        request_id,
        source["source_id"],
        message,
        now,
        source_version=source["source_version"],
        policy_version=authority.policy_version,
        permission_epoch=authority.permission_epoch,
    )
    return {"proposal": proposal, "review_packet": packet, "packet_sha256": packet_hash}


def execution_context(staged, prepared, context, authority, now):
    """Resolve current authority again, including after a human wait/restart."""
    source = staged["review_packet"]["evidence"][0]
    return dict(
        pid=staged["proposal"]["proposal_id"],
        tenant=context.tenant_id,
        current_claim=prepared["claim"]["claim_id"],
        current_status=authority.claim_status,
        actor_role="adjuster" if "adjuster" in context.roles else "reader",
        now=now,
        current_source_allowed=authority.permits(
            prepared, context.tenant_id, source["source_id"], source["source_version"]
        ),
        current_source_version=authority.source_version,
        current_policy_version=authority.policy_version,
        current_permission_epoch=authority.permission_epoch,
    )


def _write(path, value):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2)
        handle.write("\n")


def _active(registry):
    with _closing_connection(sqlite3.connect(registry.path)) as connection, connection:
        return connection.execute(
            "SELECT current FROM pointer WHERE slot=1"
        ).fetchone()[0]


def _manifest(release, config, prepared, observed):
    # The gate is one actually executed local contract case, never semantic approval.
    code_identity = {
        p.name: sha256(p.read_bytes()).hexdigest()
        for p in sorted(Path(__file__).parent.glob("*.py"))
    }
    subject = dict(
        release_id=release,
        code_sha256=_digest(code_identity),
        prompt_version=config.prompt_version,
        tool_schema_version="review-packet-v1",
        model_ref=config.model_id,
        policy_version=config.policy_version,
        guardrail_version=f"{config.guardrail_id}/{config.guardrail_version}",
        dataset_sha256=_digest(prepared),
        index_version="sha256-" + sha256(POLICY_TEXT.encode()).hexdigest(),
        evidence_sha256=_digest(observed),
    )
    status = (
        "PASS" if observed["assistance"]["status"] == "draft_for_review" else "FAIL"
    )
    evaluation = dict(subject_sha256=_digest(subject), status=status, cases=1)
    return dict(
        subject,
        quality_gate=dict(status=status, cases=1),
        evaluation_report=evaluation,
        evaluation_sha256=_digest(evaluation),
    )


def demonstrate(output, *, sources, manifest_path):
    """Create a new durable local evidence directory; no overwrite or AWS calls."""
    output = Path(output)
    output.mkdir(parents=False, exist_ok=False)
    raw = Path(manifest_path).read_bytes()
    if len(raw) > 65536:
        raise BoundaryError("Manifest exceeds capstone bound")
    context = SecurityContext("tenant-amber", "adjuster-local", frozenset({"adjuster"}))
    authority = SyntheticAuthority()
    intake = process_batch(strict_json(raw.decode()), Path(sources), context.tenant_id)
    prepared = next(
        (
            row
            for row in intake["accepted"]
            if row["claim"]["claim_id"] == authority.claim_id
        ),
        None,
    )
    if prepared is None:
        raise BoundaryError("Required synthetic claim did not pass intake")
    _write(output / "intake.json", intake)
    correlation = "corr-capstone-" + _digest(prepared)[:16]
    configs = {
        release: AssistanceConfig(
            "ABCDEFGHIJ",
            "guardrail-fixture",
            "1",
            model,
            "draft-v1",
            authority.policy_version,
        )
        for release, model in [
            ("release-baseline", "authored-baseline"),
            ("release-candidate", "authored-candidate"),
        ]
    }
    retrieval = PolicyRetrieval(authority)
    audit_path = output / "audit.jsonl"

    def run(release, fail=False):
        def persist(record):
            with audit_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {"release_id": release, "record": record}, sort_keys=True
                    )
                    + "\n"
                )

        return draft_prepared(
            prepared,
            context=context,
            authority=authority,
            config=configs[release],
            retrieval_client=retrieval,
            runtime_client=AuthoredDraftRuntime(fail),
            audit_sink=persist,
            correlation_id=correlation,
            release_id=release,
        )

    registry = ReleaseRegistry(output / "releases.sqlite")
    baseline = run("release-baseline")
    candidate_preflight = run("release-candidate")
    manifests = [
        _manifest(release, configs[release], prepared, observed)
        for release, observed in [
            ("release-baseline", baseline),
            ("release-candidate", candidate_preflight),
        ]
    ]
    for manifest in manifests:
        registry.register(manifest)
        registry.promote(manifest["release_id"])
    failed = run(_active(registry), fail=True)
    ledger = ApprovalLedger(output / "approval.sqlite")
    try:
        stage_proposal(
            failed,
            prepared,
            context=context,
            authority=authority,
            ledger=ledger,
            request_id="failed-request",
            now=1000,
        )
    except BoundaryError:
        failed_staging_rejected = True
    else:
        raise AssertionError("Failed answer entered approval workflow")
    registry.rollback()
    restored_release = _active(registry)
    restored = run(restored_release)
    restored_behavior = (
        restored_release == "release-baseline"
        and restored["assistance"]["status"] == "draft_for_review"
        and restored["assistance"]["answer"]["claims"]
        == baseline["assistance"]["answer"]["claims"]
        and restored["assistance"]["evidence"] == baseline["assistance"]["evidence"]
    )
    if not restored_behavior:
        raise AssertionError("Rollback did not restore baseline behavior")
    staged = stage_proposal(
        restored,
        prepared,
        context=context,
        authority=authority,
        ledger=ledger,
        request_id="review-restored",
        now=1000,
    )
    _write(output / "review-packet.json", staged["review_packet"])
    try:
        ledger.execute_simulation(
            **execution_context(staged, prepared, context, authority, 1001)
        )
    except BoundaryError:
        pending_rejected = True
    else:
        raise AssertionError("Pending proposal executed")
    # Separate simulated reviewer; not the generation path and not an actual human.
    ledger.approve(
        staged["proposal"]["proposal_id"],
        context.tenant_id,
        staged["proposal"]["fingerprint"],
        "adjuster-reviewer",
        "adjuster",
        1002,
    )
    authority.permission_epoch = "epoch-v2"
    try:
        ledger.execute_simulation(
            **execution_context(staged, prepared, context, authority, 1003)
        )
    except BoundaryError:
        changed_authority_rejected = True
    else:
        raise AssertionError("Stale review authority executed")
    # Renew review with a new request and exact current authority; never reset epoch.
    renewed_draft = run(restored_release)
    renewed = stage_proposal(
        renewed_draft,
        prepared,
        context=context,
        authority=authority,
        ledger=ledger,
        request_id="review-renewed",
        now=1004,
    )
    ledger.approve(
        renewed["proposal"]["proposal_id"],
        context.tenant_id,
        renewed["proposal"]["fingerprint"],
        "adjuster-reviewer",
        "adjuster",
        1005,
    )
    ledger = ApprovalLedger(output / "approval.sqlite")
    current = execution_context(renewed, prepared, context, authority, 1006)
    receipt = ledger.execute_simulation(**current)
    replay = ledger.execute_simulation(**current)
    report = dict(
        scope="executed_local_composed_synthetic_journey",
        correlation_id=correlation,
        intake_claim_id=prepared["claim"]["claim_id"],
        intake_source_sha256=prepared["source_sha256"],
        baseline=baseline,
        candidate_preflight=candidate_preflight,
        controlled_failure=failed,
        failed_staging_rejected=failed_staging_rejected,
        rollback=restored,
        rollback_behavior_verified=restored_behavior,
        pending_execution_rejected=pending_rejected,
        changed_authority_rejected=changed_authority_rejected,
        renewed_draft=renewed_draft,
        renewed_proposal=renewed["proposal"],
        receipt=receipt,
        replay_identical=receipt == replay,
        manifests=manifests,
        retrieval_queries=retrieval.queries,
        semantic_support="NOT_EVALUATED",
        human_reviewer="SIMULATED",
        live_aws="NOT_RUN",
        external_business_write=False,
        fixture_model_outputs="AUTHORED",
        cleanup={
            "cloud_resources_created": False,
            "local_files_retained": sorted(p.name for p in output.iterdir()),
        },
    )
    _write(output / "journey.json", report)
    _write(
        output / "review-labels.json",
        {
            "status": "AWAITING_INDEPENDENT_REVIEW",
            "answer_sha256": _digest(restored["assistance"]["answer"]),
            "evidence_sha256": _digest(restored["assistance"]["evidence"]),
            "reviewer_id": None,
            "support_verified": None,
            "rubric": "Check each statement against the full applicable policy and preserve qualifications.",
            "authored_fixture_expectation": "Exact reproduction of supplied policy text; not a real-model quality measurement.",
        },
    )
    artifacts = {
        "architecture_decision": {
            "chosen": "bounded local intake-to-draft-to-separate-review",
            "managed_seams": [
                "intake/extraction",
                "authoritative identity/claim/policy store",
                "Retrieve",
                "ApplyGuardrail/Converse",
                "audit sink",
                "approval service",
                "release routing",
            ],
            "cloud_seams_implemented": [
                "SDK-shaped retrieval/safety/generation adapters"
            ],
            "cloud_unimplemented": [
                "extraction ingestion",
                "identity/claim authority",
                "durable approval and traffic controller",
                "managed audit export",
            ],
        },
        "release_manifest": {
            "configurations": {
                name: asdict(config) for name, config in configs.items()
            },
            "manifests": manifests,
            "served_after_rollback": restored_release,
            "gate_scope": "One synthetic application-contract case per release; broader quality and security checks are separate.",
        },
        "dataset_manifest": {
            "manifest_sha256": sha256(raw).hexdigest(),
            "prepared": prepared,
            "quarantine": intake["quarantine"],
            "duplicates": intake["duplicates"],
            "split": "development_fixture_not_holdout",
        },
        "evaluation_report": {
            "journey_sha256": _digest(report),
            "rollback_behavior_verified": restored_behavior,
            "failed_staging_rejected": failed_staging_rejected,
            "pending_execution_rejected": pending_rejected,
            "changed_authority_rejected": changed_authority_rejected,
            "replay_identical": receipt == replay,
            "semantic_support": "NOT_EVALUATED",
            "review_labels_path": "review-labels.json",
            "cloud": "NOT_RUN",
        },
        "threat_control_review": {
            "executed": [
                "source applicability",
                "cited draft validation",
                "audit-before-return",
                "failed draft cannot stage",
                "separate approval",
                "current permission epoch",
                "idempotent replay",
            ],
            "limits": [
                "trusted simulated identities",
                "authored safety/model responses",
                "no production security assurance",
            ],
        },
        "cost_assumptions": {
            "aws_calls": 0,
            "aws_cost_measured": False,
            "performance_scope": "local measured boundary timings only",
            "model_token_cost": "NOT_MEASURED",
        },
        "runbook": {
            "failure": "invalid candidate citation withheld and staging rejected",
            "recovery": "read active release after rollback and rerun actual accepted intake",
            "cleanup": "retain this exact output directory for review; remove only its contents after retention decision",
        },
        "cleanup_verification": {
            "cloud_resources_created": False,
            "cloud_cleanup": "NOT_APPLICABLE_TO_LOCAL_RUN",
            "local_approval_and_release_databases": "RETAINED_FOR_REVIEW",
            "local_audit": "RETAINED_FOR_REVIEW",
            "managed_cleanup": "NOT_RUN",
        },
    }
    pack = {}
    for role in sorted(EVIDENCE_ROLES):
        path = output / (role + ".json")
        _write(path, artifacts[role])
        pack[role] = {
            "path": path.name,
            "sha256": sha256(path.read_bytes()).hexdigest(),
        }
    _write(output / "manifest.json", pack)
    _write(output / "integrity.json", verify_evidence_files(output, pack))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--sources", type=Path, default=Path("data/synthetic/ch03"))
    parser.add_argument(
        "--manifest", type=Path, default=Path("data/synthetic/ch03/manifest.json")
    )
    args = parser.parse_args()
    # demonstrate() creates --output-dir with mkdir(exist_ok=False); guard the
    # same existing-path/missing-parent-directory cases the other CLIs guard
    # (index_demo.py, capstone.py) so a reader rerunning this exact lab
    # command a second time gets a clean message instead of an unhandled
    # FileExistsError/FileNotFoundError traceback.
    if args.output_dir.exists() or not args.output_dir.parent.is_dir():
        parser.error("Choose a new output directory whose parent already exists")
    demonstrate(args.output_dir, sources=args.sources, manifest_path=args.manifest)
    print(
        "Saved the example journey and integrity pack. Use review-labels.json to record your independent support assessment."
    )


if __name__ == "__main__":
    main()
