"""Chapter 1 command line: offline by default, explicit opt-in for paid inference."""

import argparse
import json
from pathlib import Path
import sys
from time import perf_counter
from typing import Any

from .baseline import (
    BoundaryError,
    ModelConfig,
    ProviderError,
    run_baseline,
    strict_json,
)


class FixtureClient:
    """A deterministic teaching fixture, not an AI model or factual summarizer."""

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "text": json.dumps(
                                {
                                    "summary": "Synthetic fixture draft for testing the application boundary; this is not model-generated analysis.",
                                    "missing_information": [
                                        "A human must review the original synthetic description."
                                    ],
                                }
                            )
                        }
                    ],
                }
            },
            "stopReason": "end_turn",
            "usage": {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0},
        }


def live_client(profile: str, region: str) -> Any:
    """Optional cloud path; SDK import/client construction never occur offline."""
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        raise BoundaryError(
            "Live mode requires Boto3; install the documented cloud dependencies first"
        ) from None
    try:
        return boto3.Session(profile_name=profile).client(
            "bedrock-runtime",
            region_name=region,
            config=Config(
                connect_timeout=5,
                read_timeout=60,
                retries={"mode": "standard", "total_max_attempts": 3},
            ),
        )
    except Exception:
        raise BoundaryError(
            "Could not initialize the configured AWS session; verify profile and Region"
        ) from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--tenant",
        default="tenant-amber",
        help="Simulated trusted identity context for this teaching CLI",
    )
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--acknowledge-charges", action="store_true")
    parser.add_argument("--profile")
    parser.add_argument("--region")
    parser.add_argument("--model-id")
    parser.add_argument("--max-output-tokens", type=int, default=512)
    args = parser.parse_args(argv)
    if args.live and not (
        args.acknowledge_charges and args.profile and args.region and args.model_id
    ):
        parser.error(
            "Live mode requires --acknowledge-charges, --profile, --region and --model-id"
        )
    if not args.live and (
        args.profile or args.region or args.model_id or args.acknowledge_charges
    ):
        parser.error(
            "Cloud options require --live; offline mode always uses a labeled fixture"
        )
    try:
        # Limit bytes before parsing. Character limits are a separate application rule.
        with args.input.open("rb") as source:
            payload = source.read(65537)
        if len(payload) > 65536:
            raise BoundaryError("Input file exceeds the 64 KiB lab limit")
        data = strict_json(payload.decode("utf-8"))
        config = ModelConfig(
            args.model_id if args.live else "offline-fixture", args.max_output_tokens
        )
        # Validate before creating any SDK client (including credential discovery).
        from .baseline import Claim, _identifier

        claim = Claim.from_mapping(data)
        tenant = _identifier(args.tenant, "trusted_tenant", r"tenant-[a-z]{1,20}")
        if claim.tenant_id != tenant:
            raise BoundaryError("Claim is outside the trusted tenant scope")
        if args.output and args.output.exists():
            raise BoundaryError("Output already exists; choose a new evidence path")
        client = (
            live_client(args.profile, args.region) if args.live else FixtureClient()
        )
        start = perf_counter()
        result = run_baseline(data, tenant, client, config)
        result["evidence_class"] = "live_aws" if args.live else "offline_fixture"
        result["elapsed_ms"] = round((perf_counter() - start) * 1000, 3)
        result["synthetic_input_required"] = True
        encoded = json.dumps(result, ensure_ascii=True, indent=2) + "\n"
        if args.output:
            # Exclusive creation avoids silently replacing previous evidence.
            with args.output.open("x", encoding="utf-8") as target:
                target.write(encoded)
        else:
            sys.stdout.write(encoded)
        return 0
    except (BoundaryError, ProviderError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (OSError, UnicodeError):
        print(
            "Could not read UTF-8 input or write the requested output path",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
