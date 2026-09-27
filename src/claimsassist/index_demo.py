"""Run a deterministic synthetic source update/deletion exercise; no AWS calls."""

import argparse
from dataclasses import replace
import json
from pathlib import Path

from .vector_index import Passage, PolicyIndex, SPACE, fixture_embedding


def passage(tenant, source, version, text, start="2026-01-01", until=None):
    return Passage(
        tenant,
        source,
        version,
        "clause-1",
        start,
        until,
        text,
        "synthetic policy clause 1",
        fixture_embedding(text),
    )


def demonstrate():
    index = PolicyIndex()
    old = passage(
        "tenant-amber",
        "water-policy",
        "v1",
        "Fictional water policy: retain photographs as evidence.",
    )
    checklist = passage(
        "tenant-amber",
        "water-checklist",
        "v1",
        "Fictional water checklist: record the loss date.",
    )
    other = passage(
        "tenant-birch",
        "water-policy",
        "v1",
        "Fictional private water policy for another tenant.",
    )
    for p in (old, checklist, other):
        index.replace_source(p.tenant, p.source_id, [p])
    query = fixture_embedding("water evidence")
    before = index.search("tenant-amber", "2026-09-20", query, SPACE)
    new = passage(
        "tenant-amber",
        "water-policy",
        "v2",
        "Fictional water policy: retain photographs and a repair receipt as evidence.",
        "2026-09-01",
    )
    index.replace_source(
        "tenant-amber", "water-policy", [replace(old, valid_until="2026-09-01"), new]
    )
    after_update = index.search("tenant-amber", "2026-09-20", query, SPACE)
    historical = index.search("tenant-amber", "2026-08-31", query, SPACE)
    removed = index.delete_source("tenant-amber", "water-checklist")
    after_delete = index.search("tenant-amber", "2026-09-20", query, SPACE)
    return {
        "evidence_class": "offline_fixture_vectors",
        "embedding_space": SPACE,
        "before": before,
        "after_update": after_update,
        "historical": historical,
        "deleted_passages": removed,
        "after_delete": after_delete,
        "other_tenant": index.search("tenant-birch", "2026-09-20", query, SPACE),
        "limitations": [
            "Keyword-count vectors are not learned semantic embeddings",
            "Uses an in-memory exact scan rather than an approximate index or a cloud vector store.",
            "Identity and permission flags simulate trusted application context",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists() or not args.output.parent.is_dir():
        parser.error("Choose a new report path in an existing directory")
    report = demonstrate()
    with args.output.open("x", encoding="utf-8") as out:
        json.dump(report, out, indent=2)
        out.write("\n")
    print("Saved the synthetic version, historical-date and deletion observations")


if __name__ == "__main__":
    main()
