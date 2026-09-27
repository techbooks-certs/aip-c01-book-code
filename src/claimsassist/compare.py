"""Chapter 2 comparison harness. Offline by default; no quality winner inferred."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter
from typing import Any, Callable

from .baseline import (
    BoundaryError,
    ModelConfig,
    OutputError,
    ProviderError,
    run_baseline,
    strict_json,
)
from .cli import FixtureClient, live_client
from .routing import Route, RoutePolicy, preflight, run_with_fallback


def read_json(path: Path, limit: int = 65536) -> tuple[Any, str]:
    with path.open("rb") as source:
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise BoundaryError("Comparison input exceeds the file limit")
    return strict_json(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()


def load_config(data: Any) -> tuple[list[Route], RoutePolicy]:
    if not isinstance(data, dict) or set(data) != {
        "schema_version",
        "allowed_regions",
        "routes",
    }:
        raise BoundaryError("Comparison configuration fields do not match version 1")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise BoundaryError("Unsupported comparison configuration version")
    policy = RoutePolicy(data["allowed_regions"])
    items = data["routes"]
    if not isinstance(items, list) or len(items) != 2:
        raise BoundaryError("Comparison requires exactly two configured routes")
    routes = []
    for item in items:
        fields = {
            "name",
            "model_id",
            "max_output_tokens",
            "source_region",
            "destination_regions",
        }
        if not isinstance(item, dict) or set(item) != fields:
            raise BoundaryError("Invalid route configuration fields")
        routes.append(
            Route(
                item["name"],
                ModelConfig(item["model_id"], item["max_output_tokens"]),
                item["source_region"],
                item["destination_regions"],
            )
        )
    if len({r.name for r in routes}) != 2:
        raise BoundaryError("Route names must be unique")
    for route in routes:
        policy.check(route)
    return routes, policy


def compare_cases(
    cases: list[Any],
    tenant: str,
    routes: list[Route],
    policy: RoutePolicy,
    factory: Callable,
    evidence_class: str,
    *,
    clock: Callable = perf_counter,
) -> list[dict]:
    if evidence_class not in {"offline_fixture", "live_aws"}:
        raise BoundaryError("Unknown evidence class")
    if not isinstance(cases, list) or not 1 <= len(cases) <= 20:
        raise BoundaryError("Supply one to twenty synthetic development cases")
    for case in cases:
        preflight(case, tenant, routes, policy)
    if len({c["claim_id"] for c in cases}) != len(cases):
        raise BoundaryError("Comparison claim IDs must be unique")
    clients = {route.name: factory(route) for route in routes}
    rows = []
    for number, case in enumerate(cases):
        ordered = routes if number % 2 == 0 else list(reversed(routes))
        for route in ordered:
            client = clients[route.name]
            start = clock()
            row = {
                "claim_id": case["claim_id"],
                "route": route.name,
                "model_reference": route.model.model_id,
                "evidence_class": evidence_class,
                "quality_review": "PENDING",
                "quality_score": None,
                "usage": None,
                "draft": None,
            }
            try:
                draft = run_baseline(case, tenant, client, route.model)
            except OutputError:
                row["status"] = "output_rejected"
            except ProviderError as exc:
                row["status"] = exc.category
            else:
                row.update(status="success", usage=draft["usage"], draft=draft)
            row["elapsed_ms"] = round((clock() - start) * 1000, 3)
            rows.append(row)
    return rows


class SimulatedUnavailable:
    def converse(self, **kwargs: Any) -> Any:
        class Failure(Exception):
            response = {"Error": {"Code": "ServiceUnavailableException"}}

        raise Failure("Synthetic failure; no service was called")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--tenant", default="tenant-amber")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--profile")
    parser.add_argument("--acknowledge-charges", action="store_true")
    parser.add_argument("--simulate-fallback", action="store_true")
    args = parser.parse_args(argv)
    if args.live and not (args.profile and args.acknowledge_charges):
        parser.error("Live comparison requires --profile and --acknowledge-charges")
    if not args.live and (args.profile or args.acknowledge_charges):
        parser.error("Cloud options require --live")
    if args.live and args.simulate_fallback:
        parser.error("The failure-injection exercise is offline only")
    try:
        config, config_hash = read_json(args.config)
        cases, cases_hash = read_json(args.cases)
        routes, policy = load_config(config)
        if args.output.exists() or not args.output.parent.is_dir():
            raise BoundaryError("Choose a new evidence file in an existing directory")
        if args.live and any(r.model.model_id.startswith("offline-") for r in routes):
            raise BoundaryError(
                "Replace fixture model references with approved live resources"
            )
        evidence = "live_aws" if args.live else "offline_fixture"
        factory = (
            (lambda route: live_client(args.profile, route.source_region))
            if args.live
            else (lambda route: FixtureClient())
        )
        rows = compare_cases(cases, args.tenant, routes, policy, factory, evidence)
        report = {
            "schema_version": 1,
            "evidence_class": evidence,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "config_sha256": config_hash,
            "cases_sha256": cases_hash,
            "configuration": config,
            "rows": rows,
            "quality_status": "PENDING human rubric; fixture output is not model-quality evidence",
            "limitations": [
                "No inferred winner or billed-cost total",
                "Failed-call usage may be unknown; SDK attempts are not separate rows",
                "Region declarations require independent verification against AWS",
                "Elapsed time excludes client construction and includes SDK retries",
            ],
            "fallback_exercise": None,
        }
        if args.simulate_fallback:
            report["fallback_exercise"] = run_with_fallback(
                cases[0],
                args.tenant,
                routes,
                policy,
                lambda route: SimulatedUnavailable()
                if route.name == routes[0].name
                else FixtureClient(),
            )
        with args.output.open("x", encoding="utf-8") as target:
            json.dump(report, target, ensure_ascii=True, indent=2)
            target.write("\n")
        print(
            f"Saved {len(rows)} {evidence} observations; quality review remains pending"
        )
        return 0 if all(row["status"] == "success" for row in rows) else 3
    except (BoundaryError, ProviderError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (OSError, UnicodeError):
        print("Could not read inputs or create the evidence file", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
