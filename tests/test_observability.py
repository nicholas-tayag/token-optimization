from __future__ import annotations

from pathlib import Path

from agenvantage.observability import (
    init_observability_store,
    list_traces,
    load_trace,
    record_pack_trace,
    record_trace_artifact,
    record_trace_span,
    write_observability_dashboard,
)


def test_record_pack_trace_persists_metrics_and_artifacts(tmp_path: Path) -> None:
    db_path = tmp_path / ".agenvantage" / "observability.db"
    init_observability_store(db_path)
    report = {
        "task": "Add diagnostics.",
        "preset": "feature",
        "repo_count": 1,
        "prompt_token_accounting": {
            "full_scan_prompt_tokens": 1000,
            "packed_prompt_tokens": 120,
            "prompt_tokens_saved_vs_full_scan": 880,
            "prompt_reduction_percent_vs_full_scan": 88.0,
        },
        "selected_chunks": [
            {
                "id": "src/server.py#L1-L10",
                "path": "src/server.py",
            }
        ],
        "change_surface": {
            "edit_targets": [{"path": "src/server.py"}],
            "test_targets": [],
            "missing_signals": ["No likely test target was identified."],
        },
    }

    trace = record_pack_trace(
        db_path,
        markdown="# AgenVantage Context Package\n",
        report=report,
        repo_path=tmp_path,
        workflow="feature",
    )

    assert trace.tokens_saved == 880
    assert trace.reduction_percent == 88.0
    assert trace.selected_file_count == 1
    traces = list_traces(db_path)
    assert [item.trace_id for item in traces] == [trace.trace_id]
    loaded = load_trace(db_path, trace.trace_id)
    assert loaded["task"] == "Add diagnostics."
    assert loaded["metadata"]["selected_files"] == ["src/server.py"]
    assert loaded["metadata"]["missing_signals"] == ["No likely test target was identified."]
    assert loaded["spans"][0]["kind"] == "context.pack"
    assert {artifact["kind"] for artifact in loaded["artifacts"]} == {
        "context_markdown",
        "decision_manifest",
    }


def test_write_observability_dashboard_renders_trace_metrics(tmp_path: Path) -> None:
    db_path = tmp_path / ".agenvantage" / "observability.db"
    report = {
        "task": "Add diagnostics.",
        "preset": "feature",
        "repo_count": 1,
        "prompt_token_accounting": {
            "full_scan_prompt_tokens": 1000,
            "packed_prompt_tokens": 125,
            "prompt_tokens_saved_vs_full_scan": 875,
            "prompt_reduction_percent_vs_full_scan": 87.5,
        },
        "selected_chunks": [{"path": "src/server.py"}],
        "change_surface": {
            "edit_targets": [{"path": "src/server.py"}],
            "test_targets": [],
            "missing_signals": [],
        },
    }
    record_pack_trace(
        db_path,
        markdown="# AgenVantage Context Package\n",
        report=report,
        repo_path=tmp_path,
        workflow="feature",
    )

    dashboard_path = write_observability_dashboard(db_path, tmp_path / "dashboard.html")

    html = dashboard_path.read_text(encoding="utf-8")
    assert "Agent Observability" in html
    assert "Add diagnostics." in html
    assert "875" in html
    assert "87.50%" in html
    assert "src/server.py" in html


def test_record_trace_artifact_and_span_attach_to_existing_trace(tmp_path: Path) -> None:
    db_path = tmp_path / ".agenvantage" / "observability.db"
    report = {
        "task": "Compare strategies.",
        "prompt_token_accounting": {
            "full_scan_prompt_tokens": 1000,
            "packed_prompt_tokens": 100,
            "prompt_tokens_saved_vs_full_scan": 900,
            "prompt_reduction_percent_vs_full_scan": 90.0,
        },
        "selected_chunks": [{"path": "src/server.py"}],
        "change_surface": {"missing_signals": []},
    }
    trace = record_pack_trace(
        db_path,
        markdown="# Context\n",
        report=report,
        repo_path=tmp_path,
        workflow="experiments.compare",
    )

    artifact_id = record_trace_artifact(
        db_path,
        trace.trace_id,
        kind="experiment_comparison",
        content='{"ok": true}',
        metadata={"format": "json"},
    )
    span_id = record_trace_span(
        db_path,
        trace.trace_id,
        name="Experiment comparison",
        kind="experiment.compare",
        input_tokens=100,
        metadata={"variant_count": 4},
    )

    loaded = load_trace(db_path, trace.trace_id)
    assert artifact_id in {artifact["artifact_id"] for artifact in loaded["artifacts"]}
    assert span_id in {span["span_id"] for span in loaded["spans"]}
    assert any(span["kind"] == "experiment.compare" for span in loaded["spans"])
