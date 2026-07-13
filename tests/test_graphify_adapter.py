from __future__ import annotations

import json
from pathlib import Path

from agenvantage.graph_backend import (
    GRAPHIFY_EXPECTED_COMMIT,
    GRAPHIFY_EXPECTED_VERSION,
)
from agenvantage.graphify_adapter import GraphifyAdapter, load_graphify_snapshot


def _write_graph(repo: Path, graph_path: Path, **updates) -> None:
    files = {
        "src/service.py": "def process_order():\n    return charge_card()\n",
        "src/payments.py": "def charge_card():\n    return True\n",
        "tests/test_service.py": "def test_order():\n    assert True\n",
    }
    for relative, content in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    graph = {
        "directed": False,
        "multigraph": False,
        "nodes": [
            {
                "id": "process",
                "label": "process_order",
                "file_type": "code",
                "source_file": "src/service.py",
                "source_location": "L1",
                "community": 2,
                "unknown": "accepted",
            },
            {
                "id": "charge",
                "label": "charge_card",
                "file_type": "code",
                "source_file": "src/payments.py",
                "source_location": "L1",
                "community": 3,
            },
            {
                "id": "test",
                "label": "test_order",
                "file_type": "code",
                "source_file": "tests/test_service.py",
            },
        ],
        "links": [
            {
                "source": "process",
                "target": "charge",
                "relation": "calls",
                "confidence": "EXTRACTED",
                "source_file": "src/service.py",
                "ignored": True,
            },
            {
                "source": "test",
                "target": "process",
                "relation": "tests",
                "confidence": "INFERRED",
                "confidence_score": 0.85,
            },
        ],
    }
    graph.update(updates)
    graph_path.write_text(json.dumps(graph), encoding="utf-8")


def test_constants_pin_expected_graphify_release() -> None:
    assert GRAPHIFY_EXPECTED_VERSION == "0.9.13"
    assert GRAPHIFY_EXPECTED_COMMIT == "eec7a0183847cbdc8a87d92b233759a5204b89fe"


def test_adapter_parses_node_link_graph_and_ranks_bounded_candidates(
    tmp_path: Path,
) -> None:
    graph_path = tmp_path / "graph.json"
    _write_graph(tmp_path, graph_path)

    result = GraphifyAdapter(graph_path).candidates_for_task(
        tmp_path,
        "Change process_order in src/service.py and update its dependencies",
        max_hops=2,
    )

    assert result.available
    assert result.snapshot is not None
    assert result.snapshot.directed is False
    assert [(edge.source, edge.target) for edge in result.snapshot.edges] == [
        ("process", "charge"),
        ("test", "process"),
    ]
    assert [candidate.node_id for candidate in result.candidates] == [
        "process",
        "charge",
        "test",
    ]
    assert result.candidates[0].source_path == "src/service.py"
    assert result.candidates[0].source_location == "L1"
    assert result.candidates[1].relations == ("calls",)
    assert result.candidates[1].confidence == "EXTRACTED"
    assert result.candidates[2].hop == 1
    assert result.candidates[2].confidence == "INFERRED"
    assert "test->process:tests" in result.candidates[2].provenance


def test_adapter_returns_structured_fallback_for_missing_stale_and_invalid_graphs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    missing = load_graphify_snapshot(tmp_path, tmp_path / "missing.json")
    assert missing.status == "unavailable"
    assert missing.reason == "graph_missing"

    graph_path = tmp_path / "graph.json"
    _write_graph(tmp_path, graph_path, built_at_commit="old")
    monkeypatch.setattr(
        "agenvantage.graphify_adapter._git_head",
        lambda _repo: "current",
    )
    stale = load_graphify_snapshot(tmp_path, graph_path)
    assert stale.status == "unavailable"
    assert stale.reason == "stale_graph"

    graph_path.write_text('{"nodes": [], "links": "bad"}', encoding="utf-8")
    malformed = load_graphify_snapshot(tmp_path, graph_path)
    assert malformed.status == "error"
    assert malformed.reason is not None
    assert malformed.reason.startswith("invalid_graph:")


def test_adapter_rejects_escaping_paths_bad_anchors_and_oversized_json(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / "outside_graphify.py"
    outside.write_text("x = 1\n", encoding="utf-8")
    graph_path = tmp_path / "graph.json"
    _write_graph(
        tmp_path,
        graph_path,
        nodes=[
            {
                "id": "outside",
                "label": "outside",
                "source_file": str(outside),
                "source_location": "line 1",
            }
        ],
        links=[],
    )

    invalid = load_graphify_snapshot(tmp_path, graph_path)
    assert invalid.status == "error"
    assert invalid.reason is not None
    assert "escapes repository" in invalid.reason

    oversized = load_graphify_snapshot(
        tmp_path,
        graph_path,
        max_json_bytes=graph_path.stat().st_size - 1,
    )
    assert oversized.status == "unavailable"
    assert oversized.reason == "graph_too_large"
