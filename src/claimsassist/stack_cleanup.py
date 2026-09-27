"""Narrow CloudFormation cleanup: inspect by exact ARN; delete only on opt-in.

Only the tagged runtime stack is accepted. The separately retained image
repository and service-generated logs require their own inventory decisions.
"""

import argparse
import json
from pathlib import Path
import re
import boto3
from .baseline import BoundaryError


def cleanup(
    client,
    *,
    stack_id,
    expected_account,
    expected_region,
    execute=False,
    confirmation="",
):
    match = re.fullmatch(
        r"arn:aws:cloudformation:([a-z0-9-]+):([0-9]{12}):stack/(claimsassist-runtime-[A-Za-z0-9-]+)/[A-Za-z0-9-]+",
        stack_id,
    )
    if not match or match[1] != expected_region or match[2] != expected_account:
        raise BoundaryError(
            "Stack ARN, account or Region does not match the runtime lab"
        )
    if type(execute) is not bool or (execute and confirmation != stack_id):
        raise BoundaryError("Deletion requires exact stack ARN confirmation")
    stacks = client.describe_stacks(StackName=stack_id).get("Stacks", [])
    if len(stacks) != 1 or stacks[0].get("StackId") != stack_id:
        raise BoundaryError("Expected exactly the identified stack")
    stack = stacks[0]
    tags = {t["Key"]: t["Value"] for t in stack.get("Tags", [])}
    if tags.get("Project") != "AIP-C01" or tags.get("Lab") != "ch08-agentcore":
        raise BoundaryError("Required ownership tags are absent")
    if stack.get("EnableTerminationProtection", False):
        raise BoundaryError("Termination protection is enabled; no change made")
    if stack.get("StackStatus", "").endswith("_IN_PROGRESS"):
        raise BoundaryError("An operation is already in progress")
    resources = client.describe_stack_resources(StackName=stack_id).get(
        "StackResources", []
    )
    expected = {
        "ExecutionRole": "AWS::IAM::Role",
        "Runtime": "AWS::BedrockAgentCore::Runtime",
        "CandidateEndpoint": "AWS::BedrockAgentCore::RuntimeEndpoint",
    }
    actual = {
        row.get("LogicalResourceId"): row.get("ResourceType") for row in resources
    }
    if len(resources) != 3 or actual != expected:
        raise BoundaryError(
            "Stack resources do not match the dedicated runtime template"
        )
    report = {
        "stack_id": stack_id,
        "action": "PLAN_ONLY",
        "stack_deletion_verified": False,
        "remaining_inventory": [
            "separate ECR repository and images",
            "service-generated logs",
            "local evidence files",
        ],
    }
    if execute:
        client.delete_stack(StackName=stack_id)
        report["action"] = "DELETE_REQUESTED"
        # Waiting is explicit in the runbook; accepting the request is not verification.
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("profile", "region", "account", "stack-id"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-stack-id", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # Same existing-output/missing-directory guard used by the sibling CLIs
    # (index_demo.py, capstone.py): fail with a clear message before doing
    # any work, rather than letting a bare open("x", ...) raise an unhandled
    # traceback when a reader reruns this exact command at an output path
    # that already has a prior successful result.
    if args.output.exists() or not args.output.parent.is_dir():
        parser.error("Choose a new output path in an existing directory")
    # Reserve the evidence destination first, but release the reservation on
    # failure so a retry is not blocked by an empty leftover file. cleanup()
    # raises BoundaryError for many ordinary conditions a reader following
    # this lab is likely to hit on a first attempt (a mistyped
    # --confirm-stack-id, a stack still under termination protection, an
    # operation already in progress) -- and client construction itself can
    # raise for a bad --profile -- so exclusive "x" creation held open across
    # all of that would otherwise leave a permanent stub blocking every retry.
    with args.output.open("x") as handle:
        pass
    try:
        client = boto3.Session(
            profile_name=args.profile, region_name=args.region
        ).client("cloudformation")
        report = cleanup(
            client,
            stack_id=args.stack_id,
            expected_account=args.account,
            expected_region=args.region,
            execute=args.execute,
            confirmation=args.confirm_stack_id,
        )
        with args.output.open("w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
            handle.write("\n")
    except BaseException:
        args.output.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    main()
