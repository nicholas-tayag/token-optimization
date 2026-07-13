"""Backend-neutral models for optional repository graph candidate providers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

GRAPH_BACKEND_PROTOCOL_VERSION = 1
GRAPHIFY_EXPECTED_VERSION = "0.9.13"
GRAPHIFY_EXPECTED_COMMIT = "eec7a0183847cbdc8a87d92b233759a5204b89fe"

Confidence = Literal["EXTRACTED", "INFERRED", "AMBIGUOUS"]
ResultStatus = Literal["available", "unavailable", "error"]


@dataclass(frozen=True)
class GraphCandidate:
    """A source-backed graph node or task-ranked candidate."""

    node_id: str
    symbol: str
    source_path: str
    source_location: str | None = None
    community: str | None = None
    score: float = 0.0
    hop: int = 0
    confidence: Confidence = "EXTRACTED"
    relations: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()


@dataclass(frozen=True)
class GraphEdge:
    """A semantic edge; source and target are never reversed during parsing."""

    source: str
    target: str
    relation: str
    confidence: Confidence
    confidence_score: float
    provenance: str | None = None


@dataclass(frozen=True)
class GraphSnapshot:
    """Validated graph data whose nodes resolve to current repository files."""

    protocol_version: int
    nodes: tuple[GraphCandidate, ...]
    edges: tuple[GraphEdge, ...]
    built_at_commit: str | None = None
    directed: bool = False


@dataclass(frozen=True)
class GraphBackendResult:
    """Structured success or fallback from a graph backend."""

    status: ResultStatus
    candidates: tuple[GraphCandidate, ...] = ()
    snapshot: GraphSnapshot | None = None
    reason: str | None = None

    @property
    def available(self) -> bool:
        return self.status == "available"


class GraphBackend(Protocol):
    """Small integration surface implemented by optional graph providers."""

    def candidates_for_task(
        self,
        repo: Path,
        task: str,
        *,
        max_hops: int = 2,
        limit: int = 50,
    ) -> GraphBackendResult:
        """Return bounded, source-backed candidates or a structured fallback."""


__all__ = [
    "Confidence",
    "GRAPH_BACKEND_PROTOCOL_VERSION",
    "GRAPHIFY_EXPECTED_COMMIT",
    "GRAPHIFY_EXPECTED_VERSION",
    "GraphBackend",
    "GraphBackendResult",
    "GraphCandidate",
    "GraphEdge",
    "GraphSnapshot",
    "ResultStatus",
]
