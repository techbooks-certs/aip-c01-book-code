"""Chapter 3 offline intake: synthetic text forms and transcript fixtures.

No OCR, transcription service, model, malware scanner or general PII detector is
implemented. Deduplication is within one batch; production needs durable state.
"""

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, NoReturn

from .baseline import BoundaryError, Claim, _identifier, _string, strict_json

VERSION = "intake-v1"


class IntakeError(BoundaryError):
    pass


def reject(code: str) -> NoReturn:
    raise IntakeError(code)


def identity_fields(claim_id: Any, policy: Any, loss_date: Any) -> dict:
    try:
        _identifier(claim_id, "claim_id", r"claim-[0-9]{4}")
        _identifier(policy, "policy_number", r"PL-[0-9]{6}")
    except BoundaryError:
        reject("invalid_identifier")
    if loss_date is not None:
        if not isinstance(loss_date, str) or not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}", loss_date
        ):
            reject("ambiguous_or_invalid_date")
        try:
            date.fromisoformat(loss_date)
        except ValueError:
            reject("ambiguous_or_invalid_date")
    return {"claim_id": claim_id, "policy_number": policy, "loss_date": loss_date}


def safe_text(value: Any) -> str:
    try:
        return _string(value, "extracted text", 3500)
    except BoundaryError:
        reject("invalid_extracted_text")


def parse_form(text: str) -> tuple[dict, list[dict]]:
    lines = text.splitlines()
    expected = ["Claim-ID", "Policy-Number", "Loss-Date", "Description"]
    if len(lines) != 4:
        reject("form_schema")
    values = {}
    for line, key in zip(lines, expected):
        prefix = key + ": "
        if not line.startswith(prefix):
            reject("form_schema")
        values[key] = line[len(prefix) :].strip()
    fields = identity_fields(
        values["Claim-ID"],
        values["Policy-Number"],
        None if values["Loss-Date"] == "unknown" else values["Loss-Date"],
    )
    return fields, [{"locator": "line:4", "text": safe_text(values["Description"])}]


def parse_transcript(text: str) -> tuple[dict, list[dict]]:
    try:
        data = strict_json(text)
    except BoundaryError:
        reject("transcript_json")
    fields = {"schema_version", "claim_id", "policy_number", "loss_date", "segments"}
    if (
        not isinstance(data, dict)
        or set(data) != fields
        or type(data["schema_version"]) is not int
        or data["schema_version"] != 1
    ):
        reject("transcript_schema")
    identity = identity_fields(
        data["claim_id"], data["policy_number"], data["loss_date"]
    )
    segments = data["segments"]
    if not isinstance(segments, list) or not 1 <= len(segments) <= 50:
        reject("transcript_segments")
    result = []
    previous_end = 0
    for i, segment in enumerate(segments):
        if not isinstance(segment, dict) or set(segment) != {
            "start_ms",
            "end_ms",
            "text",
        }:
            reject("transcript_segments")
        start, end = segment["start_ms"], segment["end_ms"]
        if (
            any(type(v) is not int for v in (start, end))
            or not 0 <= start < end <= 3600000
            or start < previous_end
        ):
            reject("transcript_timestamps")
        previous_end = end
        result.append(
            {
                "locator": f"segment:{i}",
                "start_ms": start,
                "end_ms": end,
                "text": safe_text(segment["text"]),
            }
        )
    return identity, result


def validate_entry(entry: Any, tenant: str) -> None:
    fields = {"source_id", "tenant_id", "claim_id", "kind", "filename", "sha256"}
    if not isinstance(entry, dict) or set(entry) != fields:
        reject("manifest_entry_schema")
    if entry["tenant_id"] != tenant:
        reject("tenant_scope")
    try:
        _identifier(entry["source_id"], "source_id", r"src-[a-z0-9-]{1,40}")
        _identifier(entry["claim_id"], "claim_id", r"claim-[0-9]{4}")
        _identifier(entry["filename"], "filename", r"[a-z0-9][a-z0-9_.-]{0,99}")
        _identifier(entry["sha256"], "sha256", r"[0-9a-f]{64}")
    except BoundaryError:
        reject("manifest_identifier")
    if entry["kind"] not in ("text_form", "transcript_fixture"):
        reject("unsupported_media")


def process_batch(entries: Any, root: Path, trusted_tenant: str) -> dict:
    _identifier(trusted_tenant, "trusted_tenant", r"tenant-[a-z]{1,20}")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 50:
        raise BoundaryError("Intake requires one to fifty manifest entries")
    root = root.resolve()
    if not root.is_dir():
        raise BoundaryError("Source directory does not exist")
    accepted = []
    quarantine = []
    duplicates = []
    seen: dict[tuple[str, str, str, str, str], str] = {}
    source_ids: dict[str, tuple[str, str, str, str]] = {}
    for index, entry in enumerate(entries):
        try:
            validate_entry(entry, trusted_tenant)
            source_id = entry["source_id"]
            signature = (
                entry["claim_id"],
                entry["kind"],
                entry["sha256"],
                entry["filename"],
            )
            if source_id in source_ids and source_ids[source_id] != signature:
                reject("source_identity_conflict")
            source_ids[source_id] = signature
            path = root / entry["filename"]
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                reject("source_path")
            try:
                with path.open("rb") as source:
                    raw = source.read(65537)
            except OSError:
                reject("source_unreadable")
            if len(raw) > 65536:
                reject("source_too_large")
            if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
                reject("source_hash_mismatch")
            try:
                text = raw.decode("utf-8")
            except UnicodeError:
                reject("source_encoding")
            identity, segments = (
                parse_form(text)
                if entry["kind"] == "text_form"
                else parse_transcript(text)
            )
            if identity["claim_id"] != entry["claim_id"]:
                reject("claim_identity_mismatch")
            # Deliberately narrow teaching trigger, not a general injection detector.
            if any(
                "ignore previous instructions" in s["text"].casefold() for s in segments
            ):
                reject("suspicious_instruction_review")
            redactions = 0
            for segment in segments:
                redactions += segment["text"].count("synthetic@example.invalid")
                segment["text"] = segment["text"].replace(
                    "synthetic@example.invalid", "[CONTACT REDACTED]"
                )
            description = "\n".join(s["text"] for s in segments)
            if len(description) > 3500:
                reject("combined_text_too_large")
            claim = {
                "schema_version": 1,
                "claim_id": identity["claim_id"],
                "tenant_id": trusted_tenant,
                "description": description,
            }
            Claim.from_mapping(claim)
            key = (
                trusted_tenant,
                identity["claim_id"],
                entry["kind"],
                entry["sha256"],
                VERSION,
            )
            if key in seen:
                duplicates.append(
                    {
                        "entry_index": index,
                        "source_id": source_id,
                        "duplicate_of": seen[key],
                    }
                )
                continue
            seen[key] = source_id
            accepted.append(
                {
                    "source_id": source_id,
                    "source_sha256": entry["sha256"],
                    "source_filename": entry["filename"],
                    "pipeline_version": VERSION,
                    "kind": entry["kind"],
                    "fields": identity,
                    "segments": segments,
                    "synthetic_contact_redactions": redactions,
                    "review_required": True,
                    "claim": claim,
                }
            )
        except IntakeError as exc:
            quarantine.append({"entry_index": index, "reason": str(exc)})
        except BoundaryError:
            quarantine.append(
                {"entry_index": index, "reason": "normalized_claim_invalid"}
            )
    return {
        "schema_version": 1,
        "evidence_class": "offline_synthetic_intake",
        "pipeline_version": VERSION,
        "accepted": accepted,
        "quarantine": quarantine,
        "duplicates": duplicates,
        "limits": [
            "Deduplication applies only within this batch",
            "Contact masking and suspicious-instruction trigger cover only named teaching examples",
            "No OCR, ASR, malware scanning or cloud extraction was performed",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--sources", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--tenant", default="tenant-amber")
    args = parser.parse_args(argv)
    try:
        if args.output.exists() or not args.output.parent.is_dir():
            raise BoundaryError("Choose a new report path in an existing directory")
        with args.manifest.open("rb") as source:
            raw = source.read(65537)
        if len(raw) > 65536:
            raise BoundaryError("Manifest exceeds the 64 KiB limit")
        report = process_batch(
            strict_json(raw.decode("utf-8")), args.sources, args.tenant
        )
        report["manifest_sha256"] = hashlib.sha256(raw).hexdigest()
        with args.output.open("x", encoding="utf-8") as out:
            json.dump(report, out, ensure_ascii=True, indent=2)
            out.write("\n")
        print(
            f"Accepted {len(report['accepted'])}; quarantined {len(report['quarantine'])}; duplicates {len(report['duplicates'])}"
        )
        return 3 if report["quarantine"] else 0
    except (BoundaryError, OSError, UnicodeError):
        print(
            "Intake configuration, manifest or output path failed validation",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
