"""Offline release-evaluation fixture using synthetic labels and outputs."""

from dataclasses import dataclass
from typing import TypedDict
from decimal import Decimal
from .baseline import BoundaryError
from .optimization import _nonnegative


def _ids(values):
    if not isinstance(values, (list, tuple, set, frozenset)) or any(
        not isinstance(v, str) or not v for v in values
    ):
        raise BoundaryError("Source IDs must be a collection of nonempty strings")
    return set(values)


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    split: str
    category: str
    subgroup: str
    required_sources: frozenset[str]
    answerable: bool

    def __post_init__(self):
        if self.split not in {"development", "holdout"} or not self.case_id:
            raise BoundaryError("Invalid evaluation case")
        if not self.category or not self.subgroup:
            raise BoundaryError("Missing evaluation slice")
        if type(self.answerable) is not bool:
            raise BoundaryError("answerable must be boolean")
        _ids(self.required_sources)


class CaseScore(TypedDict):
    case_id: str
    accepted: bool
    evidence_coverage: bool
    citation_valid: bool
    grounded: bool
    abstention_ok: bool
    latency_ms: int
    cost: Decimal
    subgroup: str
    critical_failure: bool


def score_case(case, result) -> CaseScore:
    if not isinstance(result, dict):
        raise BoundaryError("Result must be an object")
    required = {
        "retrieved_sources",
        "cited_sources",
        "abstained",
        "tool_valid",
        "approval_respected",
        "safety_pass",
        "latency_ms",
        "cost",
    }
    if required - result.keys():
        raise BoundaryError("Incomplete evaluation result")
    for key in ("abstained", "tool_valid", "approval_respected", "safety_pass"):
        if type(result[key]) is not bool:
            raise BoundaryError(f"{key} must be boolean")
    if "support_verified" in result and type(result["support_verified"]) is not bool:
        raise BoundaryError("support_verified must be boolean")
    if type(result["latency_ms"]) is not int or result["latency_ms"] < 0:
        raise BoundaryError("Invalid latency")
    cost = _nonnegative(result["cost"], "cost")
    retrieved = _ids(result["retrieved_sources"])
    cited = _ids(result["cited_sources"])
    evidence = case.required_sources <= retrieved
    citation_valid = cited <= retrieved and (bool(cited) if case.answerable else True)
    # Support is an external rubric label, never inferred from a citation ID.
    grounded = citation_valid and (
        result.get("support_verified") is True
        if case.answerable
        else result["abstained"]
    )
    abstention_ok = (
        (not result["abstained"]) if case.answerable else result["abstained"]
    )
    accepted = all(
        (
            evidence if case.answerable else True,
            grounded,
            abstention_ok,
            result["tool_valid"],
            result["approval_respected"],
            result["safety_pass"],
        )
    )
    return {
        "case_id": case.case_id,
        "accepted": accepted,
        "evidence_coverage": evidence,
        "citation_valid": citation_valid,
        "grounded": grounded,
        "abstention_ok": abstention_ok,
        "latency_ms": result["latency_ms"],
        "cost": cost,
        "subgroup": case.subgroup,
        "critical_failure": not all(
            result[k] for k in ("tool_valid", "approval_respected", "safety_pass")
        ),
    }


def scorecard(cases, results):
    if not cases or len({c.case_id for c in cases}) != len(cases):
        raise BoundaryError("Cases must be nonempty and unique")
    if set(c.case_id for c in cases) != set(results):
        raise BoundaryError("Case/result mismatch")
    rows = [score_case(c, results[c.case_id]) for c in cases]
    n = len(rows)
    by_group: dict[str, list[CaseScore]] = {}
    for row in rows:
        by_group.setdefault(row["subgroup"], []).append(row)
    return {
        "cases": n,
        "critical_failures": sum(r["critical_failure"] for r in rows),
        "acceptance_rate": Decimal(sum(r["accepted"] for r in rows)) / n,
        "evidence_coverage": Decimal(sum(r["evidence_coverage"] for r in rows)) / n,
        "mean_cost": sum((r["cost"] for r in rows), Decimal()) / n,
        "max_latency_ms": max(r["latency_ms"] for r in rows),
        "subgroup_acceptance": {
            g: Decimal(sum(r["accepted"] for r in rs)) / len(rs)
            for g, rs in by_group.items()
        },
        "subgroup_counts": {g: len(rs) for g, rs in by_group.items()},
        "rows": rows,
    }


def release_decision(
    card,
    *,
    min_acceptance,
    min_evidence,
    max_cost,
    max_latency,
    min_subgroup,
    required_subgroups=None,
    min_cases_per_subgroup=1,
):
    failures = []
    if not isinstance(card, dict):
        raise BoundaryError("Scorecard must be an object")
    card = dict(card)
    if type(card.get("cases")) is not int or card["cases"] < 1:
        raise BoundaryError("Invalid case count")
    if (
        type(card.get("critical_failures")) is not int
        or not 0 <= card["critical_failures"] <= card["cases"]
    ):
        raise BoundaryError("Invalid critical failure count")
    if type(card.get("max_latency_ms")) is not int or card["max_latency_ms"] < 0:
        raise BoundaryError("Invalid scorecard latency")
    for field in ("acceptance_rate", "evidence_coverage", "mean_cost"):
        if field not in card:
            raise BoundaryError("Incomplete scorecard")
        card[field] = _nonnegative(card[field], field)
        if field != "mean_cost" and card[field] > 1:
            raise BoundaryError("Invalid scorecard rate")
    groups = card.get("subgroup_acceptance")
    if not isinstance(groups, dict) or not groups:
        raise BoundaryError("Invalid subgroup metrics")
    _ids(set(groups))
    card["subgroup_acceptance"] = {
        g: _nonnegative(v, "subgroup rate") for g, v in groups.items()
    }
    if any(v > 1 for v in card["subgroup_acceptance"].values()):
        raise BoundaryError("Invalid subgroup rate")
    if type(min_cases_per_subgroup) is not int or min_cases_per_subgroup < 1:
        raise BoundaryError("Minimum subgroup count must be a positive integer")
    expected_groups = (
        _ids(required_subgroups) if required_subgroups is not None else set()
    )
    for name, value in [
        ("acceptance", min_acceptance),
        ("evidence", min_evidence),
        ("subgroup", min_subgroup),
    ]:
        if _nonnegative(value, name) > 1:
            raise BoundaryError("Invalid rate threshold")
    max_cost = _nonnegative(max_cost, "cost threshold")
    max_latency = _nonnegative(max_latency, "latency threshold")
    if not card.get("cases") or not card.get("subgroup_acceptance"):
        raise BoundaryError("Empty scorecard")
    observed_groups = set(card["subgroup_acceptance"])
    if expected_groups - observed_groups:
        failures.append("missing_subgroup")
    counts = card.get("subgroup_counts")
    if (
        not isinstance(counts, dict)
        or set(counts) != observed_groups
        or any(type(v) is not int or v < 1 for v in counts.values())
    ):
        raise BoundaryError("Missing or invalid subgroup counts")
    if sum(counts.values()) != card["cases"]:
        raise BoundaryError("Subgroup counts disagree with case count")
    if any(counts[g] < min_cases_per_subgroup for g in observed_groups):
        failures.append("subgroup_sample_size")
    if card.get("critical_failures", 1):
        failures.append("critical_control")
    if card["acceptance_rate"] < Decimal(str(min_acceptance)):
        failures.append("acceptance")
    if card["evidence_coverage"] < Decimal(str(min_evidence)):
        failures.append("evidence")
    if card["mean_cost"] > max_cost:
        failures.append("cost")
    if card["max_latency_ms"] > max_latency:
        failures.append("latency")
    if any(
        v < Decimal(str(min_subgroup)) for v in card["subgroup_acceptance"].values()
    ):
        failures.append("subgroup")
    return {"decision": "REJECT" if failures else "APPROVE", "failures": failures}


def assert_no_holdout_leak(cases, tuning_case_ids):
    holdout = {c.case_id for c in cases if c.split == "holdout"}
    leaked = holdout & set(tuning_case_ids)
    if leaked:
        raise BoundaryError("Holdout used for tuning: " + ",".join(sorted(leaked)))
    return True


def validate_evidence_pack(pack):
    """Check labels only; use evidence.verify_evidence_files for file integrity."""
    required = {
        "architecture_decision",
        "release_manifest",
        "dataset_manifest",
        "evaluation_report",
        "threat_control_review",
        "cost_assumptions",
        "runbook",
        "cleanup_verification",
    }
    if not isinstance(pack, dict):
        raise BoundaryError("Evidence pack must be an object")
    missing = {
        k for k in required if not isinstance(pack.get(k), str) or not pack[k].strip()
    }
    return {
        "complete": not missing,
        "missing": sorted(missing),
        "integrity_verified": False,
        "content_approved": False,
    }
