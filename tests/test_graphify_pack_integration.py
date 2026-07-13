from __future__ import annotations

import json
from pathlib import Path

from agenvantage.repo_context import build_context_package
from agenvantage.tokenizer import TokenCounter


def _write_repo_and_graph(root: Path) -> tuple[Path, Path]:
    repo = root / "repo"
    files = {
        "src/service.py": (
            "def process_order(order):\n"
            "    return charge_card(order.total)\n"
        ),
        "src/payments.py": (
            "def charge_card(amount):\n"
            "    return {'captured': amount}\n"
        ),
        "src/order_model.py": (
            "class Order:\n"
            "    def __init__(self, total):\n"
            "        self.total = total\n"
        ),
        "src/order_validation.py": (
            "def validate_order(order):\n"
            "    return order.total >= 0\n"
        ),
        "tests/test_service.py": (
            "from src.service import process_order\n\n"
            "def test_process_order():\n"
            "    assert process_order(type('Order', (), {'total': 7})())\n"
        ),
    }
    for relative_path, text in files.items():
        path = repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    graph_path = root / "graph.json"
    graph_path.write_text(
        json.dumps(
            {
                "directed": False,
                "multigraph": False,
                "nodes": [
                    {
                        "id": "process",
                        "label": "process_order()",
                        "file_type": "code",
                        "source_file": "src/service.py",
                        "source_location": "L1",
                        "community": 1,
                    },
                    {
                        "id": "charge",
                        "label": "charge_card()",
                        "file_type": "code",
                        "source_file": "src/payments.py",
                        "source_location": "L1",
                        "community": 2,
                    },
                    {
                        "id": "external",
                        "label": "ExternalGateway",
                        "file_type": "concept",
                        "source_file": "",
                    },
                ],
                "links": [
                    {
                        "source": "process",
                        "target": "charge",
                        "relation": "calls",
                        "confidence": "EXTRACTED",
                        "confidence_score": 1.0,
                        "source_file": "src/service.py",
                    },
                    {
                        "source": "charge",
                        "target": "external",
                        "relation": "uses",
                        "confidence": "INFERRED",
                        "confidence_score": 0.7,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return repo, graph_path


def test_graphify_expands_feature_support_without_changing_hard_budget(
    tmp_path: Path, monkeypatch
) -> None:
    repo, graph_path = _write_repo_and_graph(tmp_path)
    monkeypatch.setenv("AGENVANTAGE_INDEX_ROOT", str(tmp_path / "index"))
    counter = TokenCounter()
    task = "Update order process_order validation across its dependency and regression tests"

    _, baseline = build_context_package(
        repo,
        task,
        2_000,
        counter,
        top_k=8,
        workflow="feature",
    )
    _, treatment = build_context_package(
        repo,
        task,
        2_000,
        counter,
        top_k=8,
        workflow="feature",
        graph_backend="graphify",
        graph_json=graph_path,
    )

    baseline_support = {
        item["relative_path"]
        for item in (baseline["change_surface"] or {}).get("supporting_targets", [])
    }
    treatment_support = {
        item["relative_path"]
        for item in (treatment["change_surface"] or {}).get("supporting_targets", [])
    }
    assert "src/payments.py" not in baseline_support
    assert "src/payments.py" in treatment_support
    assert treatment["graph"]["used"] is True
    assert treatment["graph"]["selected_relations"] == ["calls"]
    assert treatment["selected_context_tokens"] <= treatment["budget"]


def test_graphify_disabled_preserves_default_manifest_shape(
    tmp_path: Path, monkeypatch
) -> None:
    repo, _ = _write_repo_and_graph(tmp_path)
    monkeypatch.setenv("AGENVANTAGE_INDEX_ROOT", str(tmp_path / "index"))

    markdown, report = build_context_package(
        repo,
        "Update process_order",
        2_000,
        TokenCounter(),
        workflow="feature",
    )
    explicit_off_markdown, explicit_off_report = build_context_package(
        repo,
        "Update process_order",
        2_000,
        TokenCounter(),
        workflow="feature",
        graph_backend="off",
    )

    assert explicit_off_markdown == markdown
    assert explicit_off_report["selected_chunks"] == report["selected_chunks"]
    assert explicit_off_report["prompt_token_accounting"] == report["prompt_token_accounting"]
    assert report["graph"]["backend"] == "off"
    assert report["graph"]["enabled"] is False
    assert report["graph"]["used"] is False
    assert all(chunk["graph_score"] == 0 for chunk in report["selected_chunks"])


def test_auto_graph_skips_small_grounded_single_file_task(
    tmp_path: Path, monkeypatch
) -> None:
    repo, graph_path = _write_repo_and_graph(tmp_path)
    monkeypatch.setenv("AGENVANTAGE_INDEX_ROOT", str(tmp_path / "index"))

    _, report = build_context_package(
        repo,
        "Rename process_order in src/service.py",
        2_000,
        TokenCounter(),
        workflow="feature",
        graph_backend="auto",
        graph_json=graph_path,
    )

    assert report["graph"]["requested_mode"] == "auto"
    assert report["graph"]["decision"] == "skip"
    assert report["graph"]["attempted"] is False
    assert "small_repository" in report["graph"]["reasons"]


def test_auto_graph_uses_graph_for_large_cross_file_task(
    tmp_path: Path, monkeypatch
) -> None:
    repo, graph_path = _write_repo_and_graph(tmp_path)
    for index in range(80):
        path = repo / "src" / f"module_{index}.py"
        path.write_text(f"def helper_{index}():\n    return {index}\n", encoding="utf-8")
    monkeypatch.setenv("AGENVANTAGE_INDEX_ROOT", str(tmp_path / "index"))

    _, report = build_context_package(
        repo,
        "Update process_order across its dependency flow and regression tests",
        2_000,
        TokenCounter(),
        workflow="feature",
        graph_backend="auto",
        graph_json=graph_path,
    )

    assert report["graph"]["decision"] == "use"
    assert report["graph"]["attempted"] is True
    assert report["graph"]["used"] is True
    assert "large_repository" in report["graph"]["reasons"]
    assert "cross_file_task_language" in report["graph"]["reasons"]


def test_auto_graph_uses_graph_for_partially_grounded_investigation(
    tmp_path: Path, monkeypatch
) -> None:
    repo, graph_path = _write_repo_and_graph(tmp_path)
    for index in range(22):
        path = repo / "src" / f"support_{index}.py"
        path.write_text(f"def support_{index}():\n    return {index}\n", encoding="utf-8")
    monkeypatch.setenv("AGENVANTAGE_INDEX_ROOT", str(tmp_path / "index"))

    _, report = build_context_package(
        repo,
        "Debug the unknown checkout regression and trace its flow",
        2_000,
        TokenCounter(),
        workflow="feature",
        graph_backend="auto",
        graph_json=graph_path,
    )

    assert report["graph"]["decision"] == "use"
    assert report["graph"]["attempted"] is True
    assert any(reason.startswith("grounding_") for reason in report["graph"]["reasons"])
    assert "investigation_task_language" in report["graph"]["reasons"]
