"""Deterministic optimization fixtures with illustrative rates only."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import hashlib

from .baseline import BoundaryError


def _nonnegative(value, name):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise BoundaryError(f"Invalid {name}") from None
    if not number.is_finite() or number < 0:
        raise BoundaryError(f"{name} must be nonnegative")
    return number


@dataclass(frozen=True)
class WorkflowSample:
    input_tokens: int
    output_tokens: int
    retrieval_cost: Decimal
    tool_cost: Decimal
    human_minutes: Decimal
    success: bool
    quality: Decimal
    safety_pass: bool
    ttft_ms: int
    total_ms: int

    def __post_init__(self):
        for name in ("input_tokens", "output_tokens", "ttft_ms", "total_ms"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise BoundaryError(f"{name} must be nonnegative")
        if self.total_ms < self.ttft_ms:
            raise BoundaryError("total_ms cannot be less than ttft_ms")
        for name in ("retrieval_cost", "tool_cost", "human_minutes", "quality"):
            object.__setattr__(self, name, _nonnegative(getattr(self, name), name))
        if type(self.success) is not bool or type(self.safety_pass) is not bool:
            raise BoundaryError("Outcome flags must be booleans")
        if not Decimal("0") <= self.quality <= Decimal("1"):
            raise BoundaryError("quality must be between zero and one")


def sample_cost(sample, *, input_per_million, output_per_million, human_per_minute):
    token_cost = Decimal(sample.input_tokens) / Decimal(1_000_000) * _nonnegative(
        input_per_million, "input rate"
    ) + Decimal(sample.output_tokens) / Decimal(1_000_000) * _nonnegative(
        output_per_million, "output rate"
    )
    return (
        token_cost
        + _nonnegative(sample.retrieval_cost, "retrieval cost")
        + _nonnegative(sample.tool_cost, "tool cost")
        + sample.human_minutes * _nonnegative(human_per_minute, "human rate")
    )


def summarize(samples, rates, quality_floor):
    if not samples:
        raise BoundaryError("samples cannot be empty")
    floor = _nonnegative(quality_floor, "quality floor")
    if floor > 1:
        raise BoundaryError("Quality floor exceeds one")
    accepted = [
        s for s in samples if s.success and s.safety_pass and s.quality >= floor
    ]
    total = sum((sample_cost(s, **rates) for s in samples), Decimal("0"))
    ordered = sorted(s.total_ms for s in samples)
    rank = max(0, (95 * len(ordered) + 99) // 100 - 1)
    return {
        "requests": len(samples),
        "accepted": len(accepted),
        "total_cost": total,
        "safety_failures": sum(not s.safety_pass for s in samples),
        "cost_per_accepted": None if not accepted else total / len(accepted),
        "acceptance_rate": Decimal(len(accepted)) / len(samples),
        "mean_quality": sum((s.quality for s in samples), Decimal("0")) / len(samples),
        "p95_total_ms": ordered[rank],
    }


def choose_candidate(
    baseline,
    candidates,
    *,
    max_cost_per_accepted,
    max_p95_ms,
    min_acceptance_rate,
    min_quality,
):
    """Return the lowest-cost candidate meeting all declared release constraints."""
    max_cost_per_accepted = _nonnegative(max_cost_per_accepted, "cost ceiling")
    max_p95_ms = _nonnegative(max_p95_ms, "latency ceiling")
    min_acceptance_rate = _nonnegative(min_acceptance_rate, "acceptance floor")
    min_quality = _nonnegative(min_quality, "quality floor")
    if min_acceptance_rate > 1 or min_quality > 1:
        raise BoundaryError("Rate floor exceeds one")
    if not isinstance(candidates, dict):
        raise BoundaryError("Candidates must be a mapping")
    eligible = []
    for name, metrics in candidates.items():
        if not isinstance(metrics, dict):
            raise BoundaryError("Candidate metrics must be an object")
        metrics = dict(metrics)
        # Missing safety evidence is ineligible, not equivalent to zero failures.
        if (
            type(metrics.get("safety_failures")) is not int
            or metrics["safety_failures"] != 0
        ):
            continue
        for field in ("p95_total_ms", "acceptance_rate", "mean_quality"):
            if field not in metrics:
                raise BoundaryError("Incomplete candidate metrics")
            metrics[field] = _nonnegative(metrics[field], field)
        if metrics["acceptance_rate"] > 1 or metrics["mean_quality"] > 1:
            raise BoundaryError("Candidate rate exceeds one")
        if metrics.get("cost_per_accepted") is not None:
            metrics["cost_per_accepted"] = _nonnegative(
                metrics["cost_per_accepted"], "cost per accepted"
            )
        if (
            metrics["cost_per_accepted"] is not None
            and metrics["cost_per_accepted"] <= max_cost_per_accepted
            and metrics["p95_total_ms"] <= max_p95_ms
            and metrics["acceptance_rate"] >= min_acceptance_rate
            and metrics["mean_quality"] >= min_quality
        ):
            eligible.append((metrics["cost_per_accepted"], name))
    return baseline if not eligible else min(eligible)[1]


def prompt_cache_net(
    input_tokens,
    requests,
    hit_rate,
    *,
    standard_rate,
    write_multiplier,
    read_multiplier,
):
    """Estimate prompt-cache input cost; all rates are caller-supplied illustrations."""
    tokens = _nonnegative(input_tokens, "tokens") / Decimal(1_000_000)
    count = _nonnegative(requests, "requests")
    hit_rate = _nonnegative(hit_rate, "hit rate")
    if hit_rate > 1:
        raise BoundaryError("Hit rate exceeds one")
    standard_rate = _nonnegative(standard_rate, "standard rate")
    write_multiplier = _nonnegative(write_multiplier, "write multiplier")
    read_multiplier = _nonnegative(read_multiplier, "read multiplier")
    hits = count * hit_rate
    misses = count - hits
    uncached = tokens * count * Decimal(str(standard_rate))
    cached = tokens * misses * Decimal(str(standard_rate)) * Decimal(
        str(write_multiplier)
    ) + tokens * hits * Decimal(str(standard_rate)) * Decimal(str(read_multiplier))
    return {"uncached": uncached, "cached": cached, "savings": uncached - cached}


def safe_cache_key(
    tenant,
    source_version,
    prompt_version,
    model_ref,
    query_digest,
    *,
    authorization_scope,
    permission_epoch,
):
    """Partition cache identity; callers must still authorize every cache read."""
    parts = (
        tenant,
        source_version,
        prompt_version,
        model_ref,
        query_digest,
        authorization_scope,
        permission_epoch,
    )
    if any(not isinstance(x, str) or not x for x in parts):
        raise BoundaryError("Cache key boundaries must be nonempty strings")
    return hashlib.sha256(
        json.dumps(parts, ensure_ascii=True, separators=(",", ":")).encode()
    ).hexdigest()


def required_concurrency(arrival_per_second, average_service_seconds, headroom=1.0):
    """Little's Law planning estimate, rounded up to a whole concurrent request."""
    from math import ceil

    arrival_per_second = _nonnegative(arrival_per_second, "arrival rate")
    average_service_seconds = _nonnegative(average_service_seconds, "service time")
    headroom = _nonnegative(headroom, "headroom")
    if headroom < 1:
        raise BoundaryError("Invalid capacity input")
    return ceil(arrival_per_second * average_service_seconds * headroom)


def latency_waterfall(stages):
    if (
        not isinstance(stages, dict)
        or not stages
        or any(
            not isinstance(k, str) or not k or type(ms) is not int or ms < 0
            for k, ms in stages.items()
        )
    ):
        raise BoundaryError("Invalid latency stages")
    return {
        "total_ms": sum(stages.values()),
        "largest_stage": max(stages, key=lambda stage: stages[stage]),
    }
