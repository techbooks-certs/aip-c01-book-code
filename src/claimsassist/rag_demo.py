"""Compare deterministic retrieval configurations on explicitly labeled toy cases."""

import argparse
from dataclasses import replace
import json
from pathlib import Path
from typing import TypedDict

from .index_demo import passage
from .retrieval import retrieve, validate_extracts
from .vector_index import PolicyIndex

PARENT = "Fictional policy: gradual pipe corrosion is excluded. Exception: resulting water damage may be considered only after adjuster review."
DETACHED = (
    "Exception: resulting water damage may be considered only after adjuster review."
)


class RetrievalCase(TypedDict):
    id: str
    query: str
    date: str | None
    status: str
    citation: str | None
    required: str | None


CASES: list[RetrievalCase] = [
    dict(
        id="parent-condition",
        query="water damage from gradual pipe corrosion",
        date="2026-09-20",
        status="evidence_available",
        citation="pol-4821:v1:clause-1",
        required="gradual pipe corrosion is excluded",
    ),
    dict(
        id="exact-policy-id",
        query="pol-4821",
        date="2026-09-20",
        status="evidence_available",
        citation="pol-4821:v1:clause-1",
        required="gradual pipe corrosion is excluded",
    ),
    dict(
        id="theft-evidence",
        query="stolen bicycle receipt",
        date="2026-09-20",
        status="evidence_available",
        citation="goods-27:v1:clause-1",
        required="receipt",
    ),
    dict(
        id="unsupported-topic",
        query="airport lounge reimbursement",
        date="2026-09-20",
        status="abstain",
        citation=None,
        required=None,
    ),
    dict(
        id="missing-date",
        query="water damage",
        date=None,
        status="clarification_required",
        citation=None,
        required=None,
    ),
    dict(
        id="before-corpus-validity",
        query="water damage",
        date="2025-12-31",
        status="abstain",
        citation=None,
        required=None,
    ),
]


def build_index(chunking="parent"):
    if chunking not in {"parent", "detached"}:
        raise ValueError("Unknown synthetic chunking variant")
    index = PolicyIndex()
    records = [
        passage(
            "tenant-amber",
            "pol-4821",
            "v1",
            PARENT if chunking == "parent" else DETACHED,
        ),
        passage(
            "tenant-amber",
            "goods-27",
            "v1",
            "Fictional theft checklist: retain the stolen bicycle receipt as evidence.",
        ),
        passage(
            "tenant-birch",
            "pol-4821",
            "v1",
            "Private Birch water damage evidence not available to Amber.",
        ),
        replace(
            passage("tenant-amber", "revoked", "v1", "Revoked private water evidence."),
            allowed=False,
        ),
    ]
    for p in records:
        index.replace_source(p.tenant, p.source_id, [p])
    return index


def compare():
    observations = []
    for chunking, mode, rerank in [
        ("detached", "hybrid", True),
        ("parent", "vector", False),
        ("parent", "lexical", False),
        ("parent", "hybrid", True),
    ]:
        index = build_index(chunking)
        rows = []
        for case in CASES:
            result = retrieve(
                index,
                "tenant-amber",
                case["date"],
                case["query"],
                mode=mode,
                candidate_limit=3,
                max_chars=1200,
                rerank=rerank,
            )
            expected_citation = case["citation"]
            citation_found = (
                expected_citation in [x["citation_id"] for x in result["evidence"]]
                if expected_citation
                else None
            )
            required_preserved = (
                any(
                    case["required"] in x["quote"]
                    for x in result["evidence"]
                    if x["citation_id"] == expected_citation
                )
                if case["required"]
                else None
            )
            if result["evidence"]:
                validate_extracts(
                    {
                        "extracts": [
                            dict(citation_id=x["citation_id"], quote=x["quote"])
                            for x in result["evidence"]
                        ]
                    },
                    result["evidence"],
                )
            rows.append(
                dict(
                    case_id=case["id"],
                    expected_status=case["status"],
                    status_match=result["status"] == case["status"],
                    expected_citation_found=citation_found,
                    required_text_preserved=required_preserved,
                    result=result,
                )
            )
        observations.append(
            dict(chunking=chunking, mode=mode, rerank=rerank, cases=rows)
        )
    return dict(
        evidence_class="offline_fixture_retrieval",
        observations=observations,
        limits=[
            "Six authored diagnostic cases are not a representative quality benchmark",
            "Keyword-count vectors and overlap reranking are not trained models",
            "Exact extracts only; no generated answer or semantic entailment evaluation",
            "Uses local retrieval components and reports their behavior, rather than AWS service performance.",
        ],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or not args.output.parent.is_dir():
        parser.error("Choose a new output path in an existing directory")
    result = compare()
    with args.output.open("x", encoding="utf-8") as out:
        json.dump(result, out, indent=2)
        out.write("\n")
    print("Saved 24 synthetic retrieval observations; deliberate failures are labeled")


if __name__ == "__main__":
    main()
