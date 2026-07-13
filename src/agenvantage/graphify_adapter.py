"""Read-only adapter for Graphify's clustered NetworkX node-link JSON."""

from __future__ import annotations

import json
import re
import subprocess
from collections import deque
from dataclasses import replace
from pathlib import Path
from typing import Any

from agenvantage.graph_backend import (
    GRAPH_BACKEND_PROTOCOL_VERSION,
    Confidence,
    GraphBackendResult,
    GraphCandidate,
    GraphEdge,
    GraphSnapshot,
)

DEFAULT_MAX_GRAPH_BYTES = 20 * 1024 * 1024
_CONFIDENCE_WEIGHTS: dict[Confidence, float] = {
    "EXTRACTED": 1.0,
    "INFERRED": 0.7,
    "AMBIGUOUS": 0.25,
}
_CONFIDENCE_ORDER: dict[Confidence, int] = {
    "EXTRACTED": 2,
    "INFERRED": 1,
    "AMBIGUOUS": 0,
}
_LINE_ANCHOR = re.compile(r"L[1-9][0-9]*\Z")


class _InvalidGraph(ValueError):
    pass


def _git_head(repo: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _required_string(raw: dict[str, Any], field: str, kind: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise _InvalidGraph(f"{kind} requires a non-empty {field} string.")
    return value.strip()


def _source_path(repo: Path, value: str) -> str:
    supplied = Path(value)
    candidate = supplied if supplied.is_absolute() else repo / supplied
    resolved = candidate.resolve()
    try:
        relative = resolved.relative_to(repo)
    except ValueError as exc:
        raise _InvalidGraph(f"Source path escapes repository: {value}") from exc
    if not resolved.is_file():
        raise _InvalidGraph(f"Source file does not exist: {relative.as_posix()}")
    return relative.as_posix()


def _parse_location(raw: dict[str, Any]) -> str | None:
    value = raw.get("source_location")
    if value is None or value == "":
        return None
    if not isinstance(value, str) or _LINE_ANCHOR.fullmatch(value) is None:
        raise _InvalidGraph("source_location must be an L<number> anchor.")
    return value


def _parse_confidence(raw: dict[str, Any]) -> tuple[Confidence, float]:
    value = _required_string(raw, "confidence", "Graph edge")
    if value not in _CONFIDENCE_WEIGHTS:
        raise _InvalidGraph(f"Unsupported edge confidence label: {value}")
    confidence: Confidence = value  # type: ignore[assignment]
    default = _CONFIDENCE_WEIGHTS[confidence]
    score = raw.get("confidence_score", default)
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise _InvalidGraph("confidence_score must be a number.")
    normalized = float(score)
    if not 0.0 <= normalized <= 1.0:
        raise _InvalidGraph("confidence_score must be between 0 and 1.")
    return confidence, normalized


def _parse_snapshot(repo: Path, raw: Any) -> GraphSnapshot:
    if not isinstance(raw, dict):
        raise _InvalidGraph("Graph root must be a JSON object.")
    raw_nodes = raw.get("nodes")
    raw_links = raw.get("links")
    if not isinstance(raw_nodes, list) or not isinstance(raw_links, list):
        raise _InvalidGraph("Graph requires nodes and links lists.")

    graph_metadata = raw.get("graph", {})
    if graph_metadata is not None and not isinstance(graph_metadata, dict):
        raise _InvalidGraph("Graph metadata must be an object.")
    built_at_commit = raw.get("built_at_commit")
    if built_at_commit is None and isinstance(graph_metadata, dict):
        built_at_commit = graph_metadata.get("built_at_commit")
    if built_at_commit is not None and (
        not isinstance(built_at_commit, str) or not built_at_commit.strip()
    ):
        raise _InvalidGraph("built_at_commit must be a non-empty string.")
    if isinstance(built_at_commit, str):
        built_at_commit = built_at_commit.strip()

    nodes: list[GraphCandidate] = []
    node_ids: set[str] = set()
    for item in raw_nodes:
        if not isinstance(item, dict):
            raise _InvalidGraph("Every graph node must be an object.")
        node_id = _required_string(item, "id", "Graph node")
        if node_id in node_ids:
            raise _InvalidGraph(f"Duplicate graph node id: {node_id}")
        node_ids.add(node_id)
        symbol = _required_string(item, "label", "Graph node")
        source_value = item.get("source_file")
        # Graphify can retain external or synthesized concept nodes without a
        # source file. They are valid graph topology but never eligible
        # AgenVantage candidates because they cannot be resolved to immutable
        # current source text.
        if not isinstance(source_value, str) or not source_value.strip():
            continue
        try:
            path = _source_path(repo, source_value.strip())
        except _InvalidGraph as exc:
            if "does not exist" in str(exc):
                continue
            raise
        community_value = item.get("community")
        community = None if community_value is None else str(community_value).strip() or None
        nodes.append(
            GraphCandidate(
                node_id=node_id,
                symbol=symbol,
                source_path=path,
                source_location=_parse_location(item),
                community=community,
                provenance=(path,),
            )
        )

    resolved_node_ids = {node.node_id for node in nodes}
    edges: list[GraphEdge] = []
    for item in raw_links:
        if not isinstance(item, dict):
            raise _InvalidGraph("Every graph link must be an object.")
        source = _required_string(item, "source", "Graph edge")
        target = _required_string(item, "target", "Graph edge")
        if source not in resolved_node_ids or target not in resolved_node_ids:
            continue
        relation = _required_string(item, "relation", "Graph edge")
        confidence, confidence_score = _parse_confidence(item)
        provenance_value = item.get("source_file")
        provenance = None
        if provenance_value is not None:
            if not isinstance(provenance_value, str) or not provenance_value.strip():
                raise _InvalidGraph("Edge source_file must be a non-empty string.")
            provenance = _source_path(repo, provenance_value.strip())
        edges.append(
            GraphEdge(
                source=source,
                target=target,
                relation=relation,
                confidence=confidence,
                confidence_score=confidence_score,
                provenance=provenance,
            )
        )

    directed_value = raw.get("directed", False)
    if not isinstance(directed_value, bool):
        raise _InvalidGraph("directed must be a boolean.")
    return GraphSnapshot(
        protocol_version=GRAPH_BACKEND_PROTOCOL_VERSION,
        nodes=tuple(sorted(nodes, key=lambda node: node.node_id)),
        edges=tuple(
            sorted(
                edges,
                key=lambda edge: (
                    edge.source,
                    edge.target,
                    edge.relation,
                    edge.confidence,
                    edge.provenance or "",
                ),
            )
        ),
        built_at_commit=built_at_commit,
        directed=directed_value,
    )


def load_graphify_snapshot(
    repo: Path,
    graph_path: Path,
    *,
    max_json_bytes: int = DEFAULT_MAX_GRAPH_BYTES,
) -> GraphBackendResult:
    """Validate a Graphify graph without raising for expected bad artifacts."""

    repo = Path(repo).resolve()
    graph_path = Path(graph_path)
    if max_json_bytes <= 0:
        raise ValueError("max_json_bytes must be positive.")
    if not repo.is_dir():
        return GraphBackendResult(status="unavailable", reason="repository_missing")
    try:
        size = graph_path.stat().st_size
    except OSError:
        return GraphBackendResult(status="unavailable", reason="graph_missing")
    if size > max_json_bytes:
        return GraphBackendResult(status="unavailable", reason="graph_too_large")
    try:
        raw = json.loads(graph_path.read_text(encoding="utf-8"))
        snapshot = _parse_snapshot(repo, raw)
    except (OSError, UnicodeError, json.JSONDecodeError, _InvalidGraph) as exc:
        return GraphBackendResult(status="error", reason=f"invalid_graph: {exc}")

    if snapshot.built_at_commit is not None:
        head = _git_head(repo)
        if head is None:
            return GraphBackendResult(status="unavailable", reason="git_head_unavailable")
        if snapshot.built_at_commit != head:
            return GraphBackendResult(status="unavailable", reason="stale_graph")
    return GraphBackendResult(status="available", snapshot=snapshot)


def _is_seed(candidate: GraphCandidate, task: str) -> bool:
    normalized_task = task.replace("\\", "/")
    if re.search(
        rf"(?<![\w./-]){re.escape(candidate.source_path)}(?![\w./-])",
        normalized_task,
        re.IGNORECASE,
    ):
        return True
    basename = Path(candidate.source_path).name
    if re.search(
        rf"(?<![\w./-]){re.escape(basename)}(?![\w./-])",
        normalized_task,
        re.IGNORECASE,
    ):
        return True
    symbol = re.sub(r"\(\)\Z", "", candidate.symbol.strip())
    return re.search(
        rf"(?<![A-Za-z0-9_]){re.escape(symbol)}(?![A-Za-z0-9_])",
        task,
        re.IGNORECASE,
    ) is not None


def _weaker(first: Confidence, second: Confidence) -> Confidence:
    return first if _CONFIDENCE_ORDER[first] <= _CONFIDENCE_ORDER[second] else second


def rank_graph_candidates(
    snapshot: GraphSnapshot,
    task: str,
    *,
    max_hops: int = 2,
    limit: int = 50,
) -> tuple[GraphCandidate, ...]:
    """Seed exact symbols/paths and apply confidence-weighted 1-2 hop traversal."""

    if max_hops not in {1, 2}:
        raise ValueError("max_hops must be 1 or 2.")
    if limit <= 0:
        raise ValueError("limit must be positive.")
    nodes = {node.node_id: node for node in snapshot.nodes}
    seeds = sorted(node.node_id for node in snapshot.nodes if _is_seed(node, task))
    if not seeds:
        return ()

    adjacency: dict[str, list[GraphEdge]] = {node_id: [] for node_id in nodes}
    for edge in snapshot.edges:
        adjacency[edge.source].append(edge)
        if edge.target != edge.source:
            adjacency[edge.target].append(edge)
    for values in adjacency.values():
        values.sort(
            key=lambda edge: (
                edge.source,
                edge.target,
                edge.relation,
                edge.confidence,
                edge.provenance or "",
            )
        )

    ranked: dict[str, GraphCandidate] = {}
    queue: deque[tuple[str, int, float, Confidence, tuple[str, ...], tuple[str, ...]]] = deque()
    for seed in seeds:
        queue.append((seed, 0, 1.0, "EXTRACTED", (), ("task_exact_match",)))

    while queue:
        node_id, hop, score, confidence, relations, provenance = queue.popleft()
        current = ranked.get(node_id)
        candidate = replace(
            nodes[node_id],
            score=round(score, 6),
            hop=hop,
            confidence=confidence,
            relations=relations,
            provenance=tuple(dict.fromkeys((*nodes[node_id].provenance, *provenance))),
        )
        if current is not None and (
            current.score > candidate.score
            or (current.score == candidate.score and current.hop <= candidate.hop)
        ):
            continue
        ranked[node_id] = candidate
        if hop >= max_hops:
            continue
        for edge in adjacency[node_id]:
            neighbor = edge.target if edge.source == node_id else edge.source
            edge_weight = _CONFIDENCE_WEIGHTS[edge.confidence]
            if edge.confidence != "EXTRACTED":
                edge_weight *= edge.confidence_score
            next_score = score * edge_weight * (0.7 if hop == 0 else 0.5)
            edge_provenance = f"{edge.source}->{edge.target}:{edge.relation}"
            if edge.provenance:
                edge_provenance += f"@{edge.provenance}"
            queue.append(
                (
                    neighbor,
                    hop + 1,
                    next_score,
                    _weaker(confidence, edge.confidence),
                    tuple(dict.fromkeys((*relations, edge.relation))),
                    tuple(dict.fromkeys((*provenance, edge_provenance))),
                )
            )

    return tuple(
        sorted(
            ranked.values(),
            key=lambda candidate: (
                -candidate.score,
                candidate.hop,
                -_CONFIDENCE_ORDER[candidate.confidence],
                candidate.community or "",
                candidate.source_path,
                candidate.source_location or "",
                candidate.symbol,
                candidate.node_id,
            ),
        )[:limit]
    )


class GraphifyAdapter:
    """Graph backend backed by an existing Graphify graph.json artifact."""

    def __init__(self, graph_path: Path, *, max_json_bytes: int = DEFAULT_MAX_GRAPH_BYTES):
        self.graph_path = Path(graph_path)
        self.max_json_bytes = max_json_bytes

    def candidates_for_task(
        self,
        repo: Path,
        task: str,
        *,
        max_hops: int = 2,
        limit: int = 50,
    ) -> GraphBackendResult:
        loaded = load_graphify_snapshot(
            repo,
            self.graph_path,
            max_json_bytes=self.max_json_bytes,
        )
        if not loaded.available or loaded.snapshot is None:
            return loaded
        try:
            candidates = rank_graph_candidates(
                loaded.snapshot,
                task,
                max_hops=max_hops,
                limit=limit,
            )
        except ValueError as exc:
            return GraphBackendResult(
                status="error",
                snapshot=loaded.snapshot,
                reason=f"invalid_request: {exc}",
            )
        return GraphBackendResult(
            status="available",
            candidates=candidates,
            snapshot=loaded.snapshot,
        )


__all__ = [
    "DEFAULT_MAX_GRAPH_BYTES",
    "GraphifyAdapter",
    "load_graphify_snapshot",
    "rank_graph_candidates",
]
