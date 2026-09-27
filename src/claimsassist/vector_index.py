"""Chapter 4 exact local vector/metadata exercise, not a semantic model or AWS store."""

from dataclasses import dataclass
from datetime import date
import hashlib
import math
import re
from typing import Any, TypedDict

from .baseline import BoundaryError, _identifier, _string

SPACE = "toy-claims-keyword-counts-v1"
DIMENSIONS = 3


def iso_date(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value
    ):
        raise BoundaryError("An explicit ISO date is required")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise BoundaryError("Invalid ISO date") from None
    return value


def vector(value: Any, dimensions: int = DIMENSIONS) -> tuple[float, ...]:
    if not isinstance(value, (tuple, list)) or len(value) != dimensions:
        raise BoundaryError("Vector dimension mismatch")
    if any(
        type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 1e6
        for v in value
    ):
        raise BoundaryError("Vector must contain bounded finite numbers")
    result = tuple(float(v) for v in value)
    if sum(v * v for v in result) == 0:
        raise BoundaryError("A zero vector cannot be compared with cosine similarity")
    return result


def fixture_embedding(text: str) -> tuple[float, ...]:
    """Three visible keyword groups: no learned semantics or quality claim."""
    text = _string(text, "embedding text", 4000)
    words = re.findall(r"[a-z]+", text.casefold())
    groups = (
        {"water", "rain", "leak", "roof"},
        {"theft", "stolen", "bicycle"},
        {"receipt", "evidence", "document", "documentation"},
    )
    return vector([sum(w in group for w in words) for group in groups])


@dataclass(frozen=True)
class Passage:
    tenant: str
    source_id: str
    version: str
    chunk_id: str
    valid_from: str
    valid_until: str | None
    text: str
    location: str
    embedding: tuple[float, ...]
    embedding_space: str = SPACE
    allowed: bool = True

    def __post_init__(self):
        _identifier(self.tenant, "tenant", r"tenant-[a-z]{1,20}")
        for name in ("source_id", "version", "chunk_id"):
            _identifier(getattr(self, name), name, r"[a-z0-9][a-z0-9-]{0,49}")
        iso_date(self.valid_from)
        if self.valid_until is not None:
            iso_date(self.valid_until)
            if self.valid_until <= self.valid_from:
                raise BoundaryError("Validity interval must be positive")
        if type(self.allowed) is not bool:
            raise BoundaryError("Access flag must be boolean")
        _string(self.text, "passage", 4000)
        _string(self.location, "citation location", 200)
        _string(self.embedding_space, "embedding space", 100)
        object.__setattr__(self, "embedding", vector(self.embedding))

    @property
    def key(self):
        return self.tenant, self.source_id, self.version, self.chunk_id


class SearchHit(TypedDict):
    source_id: str
    version: str
    chunk_id: str
    text: str
    location: str
    score: float
    text_sha256: str


class PolicyIndex:
    """In-memory exact search; caller supplies trusted identity and source scope.

    Atomic replacement here means one local state assignment after validation.
    It does not implement concurrent/distributed transactions or authentication.
    """

    def __init__(self, embedding_space: str = SPACE):
        self.embedding_space = _string(embedding_space, "embedding space", 100)
        self._records: dict[tuple[str, str, str, str], Passage] = {}

    def replace_source(
        self, trusted_tenant: str, source_id: str, passages: list[Passage]
    ):
        _identifier(trusted_tenant, "trusted_tenant", r"tenant-[a-z]{1,20}")
        _identifier(source_id, "source_id", r"[a-z0-9][a-z0-9-]{0,49}")
        if not isinstance(passages, list) or not 1 <= len(passages) <= 100:
            raise BoundaryError(
                "Replace a source with one to one hundred passages; deletion is explicit"
            )
        staged: dict[tuple[str, str, str, str], Passage] = {}
        intervals: dict[str, tuple[str, str | None]] = {}
        for passage in passages:
            if not isinstance(passage, Passage):
                raise BoundaryError("Validated Passage objects are required")
            if passage.tenant != trusted_tenant or passage.source_id != source_id:
                raise BoundaryError("Passage is outside the trusted source scope")
            if passage.embedding_space != self.embedding_space:
                raise BoundaryError(
                    "Embedding-space mismatch, even if dimensions match"
                )
            if passage.key in staged:
                raise BoundaryError("Duplicate passage identity")
            interval = (passage.valid_from, passage.valid_until)
            if passage.version in intervals and intervals[passage.version] != interval:
                raise BoundaryError("Version validity metadata is inconsistent")
            intervals[passage.version] = interval
            staged[passage.key] = passage
        ordered = sorted(intervals.values(), key=lambda item: item[0])
        for previous, current in zip(ordered, ordered[1:]):
            if previous[1] is None or previous[1] > current[0]:
                raise BoundaryError("Source-version validity intervals overlap")
        retained = {
            k: v
            for k, v in self._records.items()
            if k[:2] != (trusted_tenant, source_id)
        }
        retained.update(staged)
        self._records = retained

    def delete_source(self, trusted_tenant: str, source_id: str) -> int:
        _identifier(trusted_tenant, "trusted_tenant", r"tenant-[a-z]{1,20}")
        _identifier(source_id, "source_id", r"[a-z0-9][a-z0-9-]{0,49}")
        keys = [k for k in self._records if k[:2] == (trusted_tenant, source_id)]
        self._records = {k: v for k, v in self._records.items() if k not in keys}
        return len(keys)

    def search(
        self,
        trusted_tenant: str,
        loss_date: str,
        query_vector: Any,
        embedding_space: str,
        top_k: int = 5,
    ) -> list[SearchHit]:
        _identifier(trusted_tenant, "trusted_tenant", r"tenant-[a-z]{1,20}")
        iso_date(loss_date)
        if embedding_space != self.embedding_space:
            raise BoundaryError("Query embedding-space mismatch")
        query = vector(query_vector)
        if type(top_k) is not int or not 1 <= top_k <= 20:
            raise BoundaryError("top_k must be an integer from one to twenty")
        qnorm = math.sqrt(sum(v * v for v in query))
        result: list[SearchHit] = []
        for passage in self.eligible_passages(trusted_tenant, loss_date):
            pnorm = math.sqrt(sum(v * v for v in passage.embedding))
            score = sum(a * b for a, b in zip(query, passage.embedding)) / (
                qnorm * pnorm
            )
            if score <= 0:
                continue
            result.append(
                {
                    "source_id": passage.source_id,
                    "version": passage.version,
                    "chunk_id": passage.chunk_id,
                    "text": passage.text,
                    "location": passage.location,
                    "score": round(score, 8),
                    "text_sha256": hashlib.sha256(passage.text.encode()).hexdigest(),
                }
            )
        return sorted(
            result,
            key=lambda r: (-r["score"], r["source_id"], r["version"], r["chunk_id"]),
        )[:top_k]

    def eligible_passages(
        self, trusted_tenant: str, loss_date: str
    ) -> tuple[Passage, ...]:
        """Shared pre-ranking boundary for the Chapter 5 lexical/vector paths."""
        _identifier(trusted_tenant, "trusted_tenant", r"tenant-[a-z]{1,20}")
        iso_date(loss_date)
        return tuple(
            p
            for p in self._records.values()
            if p.tenant == trusted_tenant
            and p.allowed
            and p.valid_from <= loss_date
            and (p.valid_until is None or loss_date < p.valid_until)
        )

    @property
    def count(self):
        return len(self._records)
