"""Chapter 2: explicit route eligibility and bounded, read-only fallback.

Destination declarations are trusted configuration, not discovery or proof of
AWS routing. Production controls must verify the actual resource configuration.
"""

from dataclasses import dataclass
import re
from typing import Any, Callable

from .baseline import BoundaryError, Claim, ConverseClient, ModelConfig, ProviderError
from .baseline import run_baseline, _identifier


def _regions(values: Any) -> frozenset[str]:
    if not isinstance(values, (list, tuple, frozenset)) or not values:
        raise BoundaryError("A nonempty explicit Region set is required")
    if any(
        not isinstance(x, str)
        or re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-[0-9]+", x) is None
        for x in values
    ):
        raise BoundaryError("Invalid Region declaration")
    if len(set(values)) != len(values):
        raise BoundaryError("Duplicate Region declaration")
    return frozenset(values)


@dataclass(frozen=True)
class Route:
    name: str
    model: ModelConfig
    source_region: str
    destination_regions: frozenset[str]
    contract: str = "claims-draft-v1"

    def __post_init__(self) -> None:
        _identifier(self.name, "route name", r"[a-z][a-z0-9-]{0,39}")
        if not isinstance(self.model, ModelConfig):
            raise BoundaryError("Route requires a validated ModelConfig")
        _regions([self.source_region])
        object.__setattr__(
            self, "destination_regions", _regions(self.destination_regions)
        )
        if self.contract != "claims-draft-v1":
            raise BoundaryError("Unsupported route output contract")


@dataclass(frozen=True)
class RoutePolicy:
    allowed_regions: frozenset[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_regions", _regions(self.allowed_regions))

    def check(self, route: Route) -> None:
        if (
            not ({route.source_region} | route.destination_regions)
            <= self.allowed_regions
        ):
            raise BoundaryError("Route is outside the declared Region policy")


def preflight(data: Any, tenant: str, routes: list[Route], policy: RoutePolicy) -> None:
    """Validate every route before constructing any client."""
    trusted = _identifier(tenant, "trusted_tenant", r"tenant-[a-z]{1,20}")
    claim = Claim.from_mapping(data)
    if claim.tenant_id != trusted:
        raise BoundaryError("Claim is outside the trusted tenant scope")
    if not 1 <= len(routes) <= 2 or len({r.name for r in routes}) != len(routes):
        raise BoundaryError("Select one or two uniquely named routes")
    for route in routes:
        policy.check(route)


def run_with_fallback(
    data: Any,
    tenant: str,
    routes: list[Route],
    policy: RoutePolicy,
    client_factory: Callable[[Route], ConverseClient],
) -> dict[str, Any]:
    """Try at most two approved routes; output rejection never triggers fallback.

    No deadline/circuit breaker is implemented here. SDK retries are separate.
    Failed-call usage is unknown; this report is not a complete bill.
    """
    preflight(data, tenant, routes, policy)
    attempts = []
    for index, route in enumerate(routes):
        client = client_factory(route)
        try:
            result = run_baseline(data, tenant, client, route.model)
        except ProviderError as exc:
            attempts.append(
                {"route": route.name, "status": exc.category, "usage": None}
            )
            if exc.category not in {"throttled", "service_unavailable"}:
                raise
            if index + 1 == len(routes):
                return {
                    "status": "unavailable",
                    "review_required": True,
                    "attempts": attempts,
                    "draft": None,
                }
        else:
            attempts.append(
                {"route": route.name, "status": "success", "usage": result["usage"]}
            )
            return {
                "status": "draft_for_human_review",
                "review_required": True,
                "attempts": attempts,
                "draft": result,
            }
    raise AssertionError("Validated route loop must return or raise")
