"""Optional single-fictional-tenant SQS -> Lambda -> AgentCore integration.

No tenant, runtime, model or approval context is accepted from a job. Only IAM
operators with queue send permission can submit. Read-only inference can repeat
across a crash; the durable result has a conditional idempotency boundary.
"""

import argparse
import hashlib
import json
import os
import re
import time
from uuid import uuid4

import boto3
from botocore.config import Config
from .baseline import strict_json
from .runtime_contract import validate_result


def parse_job(raw):
    if not isinstance(raw, str) or len(raw.encode()) > 8192:
        raise ValueError("invalid job")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate field")
            result[key] = value
        return result

    job = json.loads(
        raw,
        object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")),
    )
    if not isinstance(job, dict) or set(job) != {"request_id", "prompt"}:
        raise ValueError("invalid job fields")
    if not isinstance(job["request_id"], str) or not re.fullmatch(
        r"[a-z0-9-]{1,64}", job["request_id"]
    ):
        raise ValueError("invalid request id")
    if (
        not isinstance(job["prompt"], str)
        or not job["prompt"].strip()
        or len(job["prompt"]) > 2000
    ):
        raise ValueError("invalid prompt")
    return job


def run_job(raw, *, table, agent, runtime_arn, qualifier, release_id, now=None):
    """Consume a job with a lease; duplicate completed jobs return stored result.

    A conditional write protects receipt ownership, not exactly-once inference.
    There are no external effect tools in the configured runtime.
    """
    job = parse_job(raw)
    if not re.fullmatch(r"release-[a-z0-9-]{1,40}", release_id):
        raise ValueError("invalid configured release")
    now = int(time.time()) if now is None else now
    if type(now) is not int or now < 0:
        raise ValueError("invalid clock")
    key = job["request_id"]
    owner = str(uuid4())
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "job": job,
                "release": release_id,
                "runtime": runtime_arn,
                "qualifier": qualifier,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    existing = table.get_item(Key={"request_id": key}, ConsistentRead=True).get("Item")
    if existing:
        if existing.get("fingerprint") != fingerprint:
            raise ValueError("idempotency conflict")
        if existing.get("state") == "SUCCEEDED":
            return json.loads(existing["result"])
    item = {
        "request_id": key,
        "fingerprint": fingerprint,
        "state": "RUNNING",
        "owner": owner,
        "lease_until": now + 240,
        "release_id": release_id,
    }
    if not existing:
        table.put_item(
            Item=item, ConditionExpression="attribute_not_exists(request_id)"
        )
    else:
        table.put_item(
            Item=item,
            ConditionExpression="fingerprint = :fp AND #state <> :done AND lease_until < :now",
            ExpressionAttributeNames={"#state": "state"},
            ExpressionAttributeValues={
                ":fp": fingerprint,
                ":done": "SUCCEEDED",
                ":now": now,
            },
        )
    response = None
    try:
        response = agent.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            qualifier=qualifier,
            runtimeSessionId=str(uuid4()),
            contentType="application/json",
            accept="application/json",
            payload=json.dumps({"prompt": job["prompt"]}).encode(),
        )
        if (
            response.get("statusCode") != 200
            or response.get("contentType", "").split(";")[0] != "application/json"
        ):
            raise ValueError("runtime response rejected")
        raw_result = response["response"].read(65537)
        if len(raw_result) > 65536:
            raise ValueError("runtime response too large")
        result = validate_result(strict_json(raw_result.decode("utf-8")))
        record = {
            "request_id": key,
            "release_id": release_id,
            "status": "SUCCEEDED",
            "result": result,
        }
        serialized = json.dumps(record, sort_keys=True)
        if len(serialized.encode()) > 70000:
            raise ValueError("stored result too large")
        table.update_item(
            Key={"request_id": key},
            UpdateExpression="SET #state = :done, #result = :result, lease_until = :zero",
            ConditionExpression="#owner = :owner AND fingerprint = :fp",
            ExpressionAttributeNames={
                "#state": "state",
                "#result": "result",
                "#owner": "owner",
            },
            ExpressionAttributeValues={
                ":done": "SUCCEEDED",
                ":result": serialized,
                ":zero": 0,
                ":owner": owner,
                ":fp": fingerprint,
            },
        )
        return record
    except Exception:
        # Do not overwrite a newer lease owner. No raw prompt/provider error log.
        table.update_item(
            Key={"request_id": key},
            UpdateExpression="SET #state = :retry, lease_until = :zero",
            ConditionExpression="#owner = :owner AND fingerprint = :fp",
            ExpressionAttributeNames={"#state": "state", "#owner": "owner"},
            ExpressionAttributeValues={
                ":retry": "RETRYABLE",
                ":zero": 0,
                ":owner": owner,
                ":fp": fingerprint,
            },
        )
        raise
    finally:
        if response is not None and "response" in response:
            response["response"].close()


def handle_batch(event, *, process):
    failures = []
    for record in event.get("Records", []):
        try:
            process(record["body"])
        except Exception:
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}


def handler(event, context):
    settings = {
        key: os.environ[key]
        for key in ("TABLE_NAME", "RUNTIME_ARN", "RUNTIME_QUALIFIER", "RELEASE_ID")
    }
    table = boto3.resource("dynamodb").Table(settings["TABLE_NAME"])
    agent = boto3.client(
        "bedrock-agentcore",
        config=Config(
            connect_timeout=5, read_timeout=150, retries={"total_max_attempts": 1}
        ),
    )
    return handle_batch(
        event,
        process=lambda raw: run_job(
            raw,
            table=table,
            agent=agent,
            runtime_arn=settings["RUNTIME_ARN"],
            qualifier=settings["RUNTIME_QUALIFIER"],
            release_id=settings["RELEASE_ID"],
        ),
    )


def main():
    parser = argparse.ArgumentParser(
        description="Submit one synthetic job to an explicitly selected queue"
    )
    for name in ("profile", "region", "queue-url", "request-id"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument(
        "--prompt", default="Explain the fictional corrosion exclusion."
    )
    parser.add_argument("--acknowledge-charges", action="store_true", required=True)
    args = parser.parse_args()
    raw = json.dumps({"request_id": args.request_id, "prompt": args.prompt})
    parse_job(raw)
    client = boto3.Session(profile_name=args.profile, region_name=args.region).client(
        "sqs"
    )
    response = client.send_message(QueueUrl=args.queue_url, MessageBody=raw)
    print(
        json.dumps(
            {
                "status": "SUBMITTED_NOT_COMPLETED",
                "message_id": response["MessageId"],
                "request_id": args.request_id,
            }
        )
    )


if __name__ == "__main__":
    main()
