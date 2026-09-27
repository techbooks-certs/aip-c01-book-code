"""Offline RAG diagnostics: toy retrieval and cited extracts, never a model answer."""

import hashlib
import json
import re
from typing import Any

from .baseline import BoundaryError, _identifier, _string
from .vector_index import PolicyIndex, SPACE, fixture_embedding

STOP = {
    "a",
    "an",
    "the",
    "is",
    "are",
    "of",
    "for",
    "to",
    "and",
    "does",
    "what",
    "with",
    "my",
}


def words(text):
    return set(re.findall(r"[a-z0-9]+", text.casefold())) - STOP


def identity(p):
    return f"{p.source_id}:{p.version}:{p.chunk_id}"


def retrieve(
    index: PolicyIndex,
    tenant: str,
    loss_date: str | None,
    query: str,
    mode="hybrid",
    candidate_limit=10,
    max_chars=1200,
    rerank=False,
) -> dict[str, Any]:
    """Filter, rank, optionally apply a lexical heuristic, then budget full extracts.

    The character limit measures the serialized evidence list, not model tokens.
    The reranker is a visible token-overlap heuristic, not a trained model.
    """
    _identifier(tenant, "tenant", r"tenant-[a-z]{1,20}")
    query = _string(query, "query", 1000)
    if mode not in {"lexical", "vector", "hybrid"} or type(rerank) is not bool:
        raise BoundaryError("Unsupported retrieval configuration")
    if type(candidate_limit) is not int or not 1 <= candidate_limit <= 20:
        raise BoundaryError("candidate_limit must be one to twenty")
    if type(max_chars) is not int or not 2 <= max_chars <= 10000:
        raise BoundaryError("max_chars must be two to ten thousand")
    base = dict(
        evidence_class="offline_fixture_retrieval",
        mode=mode,
        reranker="lexical_overlap_heuristic" if rerank else "none",
        evidence=[],
        candidate_ids=[],
        skipped_for_budget=[],
        context_chars=2,
        review_required=True,
    )
    if loss_date is None:
        return dict(base, status="clarification_required", reason="loss_date_required")
    passages = index.eligible_passages(tenant, loss_date)
    lookup = {identity(p): p for p in passages}
    query_words = words(query)
    lexical_scores = {
        identity(p): len(query_words & words(p.text + " " + p.source_id))
        for p in passages
    }
    lexical = sorted(
        (k for k, v in lexical_scores.items() if v > 0),
        key=lambda k: (-lexical_scores[k], k),
    )
    vector_hits = []
    if mode in {"vector", "hybrid"}:
        try:
            query_vector = fixture_embedding(query)
        except BoundaryError:
            # Input was validated above; unknown toy vocabulary has no vector.
            query_vector = None
        if query_vector is not None:
            vector_hits = [
                f"{p['source_id']}:{p['version']}:{p['chunk_id']}"
                for p in index.search(tenant, loss_date, query_vector, SPACE, top_k=20)
            ]
    lists = (
        [lexical]
        if mode == "lexical"
        else [vector_hits]
        if mode == "vector"
        else [lexical, vector_hits]
    )
    scores: dict[str, float] = {}
    for ranking in lists:
        for rank, key in enumerate(ranking[:candidate_limit], 1):
            scores[key] = scores.get(key, 0) + 1 / (60 + rank)
    candidates = sorted(scores, key=lambda k: (-scores[k], k))[:candidate_limit]
    if rerank:
        candidates.sort(key=lambda k: (-lexical_scores[k], -scores[k], k))
    base["candidate_ids"] = candidates
    evidence: list[dict[str, str]] = []
    for key in candidates:
        p = lookup[key]
        item = dict(
            citation_id=key,
            source_id=p.source_id,
            version=p.version,
            location=p.location,
            quote=p.text,
            text_sha256=hashlib.sha256(p.text.encode()).hexdigest(),
        )
        trial = evidence + [item]
        length = len(json.dumps(trial, ensure_ascii=False, separators=(",", ":")))
        if length > max_chars:
            base["skipped_for_budget"].append(key)
            continue
        evidence = trial
    base["evidence"] = evidence
    base["context_chars"] = len(
        json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
    )
    if not candidates:
        return dict(base, status="abstain", reason="no_fixture_candidates")
    if not evidence:
        return dict(base, status="abstain", reason="context_budget_insufficient")
    return dict(base, status="evidence_available", reason="extracts_require_review")


def validate_extracts(response, evidence):
    """Validate exact quoted extracts only; not a semantic entailment checker."""
    if not isinstance(response, dict) or set(response) != {"extracts"}:
        raise BoundaryError("An extracts-only response is required")
    extracts = response["extracts"]
    if not isinstance(extracts, list) or not 1 <= len(extracts) <= 20:
        raise BoundaryError("One to twenty extracts required")
    allowed = {item["citation_id"]: item["quote"] for item in evidence}
    used = set()
    for item in extracts:
        if not isinstance(item, dict) or set(item) != {"citation_id", "quote"}:
            raise BoundaryError("Invalid cited extract")
        cid = item["citation_id"]
        quote = item["quote"]
        if not isinstance(cid, str) or cid not in allowed or cid in used:
            raise BoundaryError("Unknown or duplicate citation")
        if not isinstance(quote, str) or quote != allowed[cid]:
            raise BoundaryError("The complete evidence quote must be preserved")
        used.add(cid)
    return response
