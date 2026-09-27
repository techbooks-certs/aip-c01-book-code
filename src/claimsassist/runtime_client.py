"""IAM-authenticated optional AgentCore invocation, bounded JSON response only."""

import argparse
import json
from pathlib import Path
import re
from uuid import uuid4
import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from .baseline import BoundaryError, ProviderError, strict_json, _string
from .runtime_contract import validate_result


def invoke(client, *, runtime_arn, prompt, session_id, qualifier="candidate"):
    if (
        not isinstance(runtime_arn, str)
        or re.fullmatch(
            r"arn:aws:bedrock-agentcore:[a-z0-9-]+:[0-9]{12}:runtime/[A-Za-z][A-Za-z0-9_]*-[A-Za-z0-9]{10}",
            runtime_arn,
        )
        is None
    ):
        raise BoundaryError("Use an exact commercial-region runtime ARN")
    if (
        not isinstance(session_id, str)
        or re.fullmatch(r"[A-Za-z0-9_-]{33,100}", session_id) is None
    ):
        raise BoundaryError("Use a fresh bounded session identifier")
    if (
        not isinstance(qualifier, str)
        or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,47}", qualifier) is None
    ):
        raise BoundaryError("Invalid endpoint qualifier")
    prompt = _string(prompt, "prompt", 2000)
    response = None
    try:
        response = client.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            runtimeSessionId=session_id,
            qualifier=qualifier,
            contentType="application/json",
            accept="application/json",
            payload=json.dumps({"prompt": prompt}).encode(),
        )
        if (
            response.get("statusCode") != 200
            or response.get("contentType", "").split(";")[0] != "application/json"
        ):
            raise ProviderError("provider_failure")
        body = response["response"].read(65537)
        if len(body) > 65536:
            raise BoundaryError("Runtime response exceeds client bound")
        result = strict_json(body.decode("utf-8"))
        return validate_result(result)
    except (BotoCoreError, ClientError, UnicodeDecodeError, KeyError):
        raise ProviderError("provider_failure") from None
    finally:
        if response is not None and "response" in response:
            response["response"].close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--runtime-arn", required=True)
    parser.add_argument("--qualifier", default="candidate")
    parser.add_argument(
        "--prompt",
        default="Explain the fictional corrosion exclusion and its review exception.",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--acknowledge-charges", action="store_true", required=True)
    args = parser.parse_args()
    # Same existing-output/missing-directory guard used by the sibling CLIs
    # (index_demo.py, capstone.py): fail with a clear message before making
    # any billable request, rather than letting a bare open("x", ...) raise
    # an unhandled traceback when a reader reruns this exact command at an
    # output path that already has a prior successful result.
    if args.output.exists() or not args.output.parent.is_dir():
        parser.error("Choose a new output path in an existing directory")
    # Reserve the evidence destination before making a billable request, but
    # release the reservation on failure so a retry is not blocked by an empty
    # leftover file at the same path (exclusive "x" creation would refuse it).
    with args.output.open("x") as handle:
        pass
    try:
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        client = session.client(
            "bedrock-agentcore",
            config=Config(
                connect_timeout=5, read_timeout=180, retries={"total_max_attempts": 1}
            ),
        )
        result = invoke(
            client,
            runtime_arn=args.runtime_arn,
            prompt=args.prompt,
            session_id=str(uuid4()),
            qualifier=args.qualifier,
        )
        with args.output.open("w", encoding="utf-8") as handle:
            json.dump(
                {
                    "scope": "live_agentcore_invocation",
                    "result": result,
                    "quality_evaluated": False,
                },
                handle,
                indent=2,
            )
            handle.write("\n")
    except BaseException:
        args.output.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    main()
