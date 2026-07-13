from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agenvantage.observability import record_pack_trace, write_observability_dashboard
from agenvantage.studio import (
    build_doctor_payload,
    build_studio_status,
    graph_policy_chip,
    render_context_preview,
    render_setup_panel,
    render_studio_hub,
    studio_links,
    studio_root,
    write_studio_pages,
)


def test_graph_policy_chip_describes_auto_decision() -> None:
    chip = graph_policy_chip(
        {
            "backend": "auto",
            "decision": "use",
            "reasons": ["large_repository", "cross_file_task_language"],
        }
    )

    assert chip["label"] == "auto → use"
    assert "large repository" in chip["detail"]
    assert chip["tone"] == "positive"


def test_write_studio_pages_renders_hub_setup_and_preview(tmp_path: Path) -> None:
    report = {
        "task": "Add upload validation",
        "preset": "feature",
        "prompt_token_accounting": {
            "packed_prompt_tokens": 1200,
            "full_scan_prompt_tokens": 9000,
            "prompt_tokens_saved_vs_full_scan": 7800,
        },
        "graph": {
            "backend": "auto",
            "decision": "skip",
            "reasons": ["small_repository"],
        },
        "change_surface": {
            "edit_targets": [{"path": "src/server.py"}],
            "test_targets": [{"path": "tests/test_server.py"}],
            "missing_signals": [],
        },
        "selected_chunks": [
            {
                "id": "src/server.py#L1-L20",
                "relative_path": "src/server.py",
                "tokens": 120,
            }
        ],
    }
    status = build_studio_status(
        repo=tmp_path,
        doctor=build_doctor_payload(scope="personal", command="agenvantage"),
        checkup={"overall_status": "pass", "findings": [], "metrics": {"trace_count": 0}},
    )

    paths = write_studio_pages(tmp_path, status=status, preview_report=report, preset_name="feature")

    hub = paths["hub"].read_text(encoding="utf-8")
    setup = paths["setup"].read_text(encoding="utf-8")
    preview = paths["preview"].read_text(encoding="utf-8")

    assert "AgenVantage Studio" in hub
    assert "Install and verify" in hub
    assert "Setup" in setup
    assert "Context preview" in preview
    assert "src/server.py" in preview
    assert "auto → skip" in preview


def test_observability_dashboard_includes_graph_chip_and_change_surface(tmp_path: Path) -> None:
    db_path = tmp_path / ".agenvantage" / "observability.db"
    report = {
        "task": "Fix progress bar final position",
        "preset": "debug",
        "repo_count": 1,
        "prompt_token_accounting": {
            "full_scan_prompt_tokens": 5000,
            "packed_prompt_tokens": 900,
            "prompt_tokens_saved_vs_full_scan": 4100,
            "prompt_reduction_percent_vs_full_scan": 82.0,
        },
        "selected_chunks": [{"path": "src/click/_termui_impl.py"}],
        "change_surface": {
            "edit_targets": [{"path": "src/click/_termui_impl.py"}],
            "test_targets": [{"path": "tests/test_termui.py"}],
            "missing_signals": [],
        },
        "graph": {
            "backend": "auto",
            "decision": "use",
            "used": True,
            "reasons": ["investigation_task_language"],
        },
    }
    record_pack_trace(
        db_path,
        markdown="# AgenVantage Context Package\n",
        report=report,
        repo_path=tmp_path,
        workflow="debug",
    )

    html = write_observability_dashboard(db_path, tmp_path / "dashboard.html").read_text(encoding="utf-8")

    assert "auto → use" in html
    assert "Change surface" in html
    assert "src/click/_termui_impl.py" in html
    assert "Open AgenVantage Studio" in html


def test_studio_links_use_relative_paths(tmp_path: Path) -> None:
    links = studio_links(tmp_path)

    assert links["hub"] == "index.html"
    assert links["preview"] == "context-preview.html"
    assert links["observability"] == "../observability-dashboard.html"


def test_pack_preview_flag_writes_html(tmp_path: Path, monkeypatch) -> None:
    import os

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    monkeypatch.setenv("AGENVANTAGE_INDEX_ROOT", str(tmp_path / "index"))

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agenvantage",
            "pack",
            "--task",
            "Explain run()",
            "--repo",
            str(repo),
            "--graph-backend",
            "off",
            "--preview",
        ],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "AGENVANTAGE_INDEX_ROOT": str(tmp_path / "index")},
    )

    preview = studio_root(repo) / "context-preview.html"
    assert preview.is_file()
    assert "Context preview written to" in completed.stdout
    assert "Explain run()" in preview.read_text(encoding="utf-8")


def test_studio_demo_writes_hub_and_preview(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# Demo repo\n", encoding="utf-8")
    monkeypatch.setenv("AGENVANTAGE_INDEX_ROOT", str(tmp_path / "index"))

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agenvantage",
            "studio",
            "demo",
            "--repo",
            str(repo),
            "--graph-backend",
            "off",
            "--no-browser",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "AgenVantage studio demo completed." in completed.stdout
    assert (studio_root(repo) / "index.html").is_file()
    assert (studio_root(repo) / "context-preview.html").is_file()
    assert (repo / ".agenvantage" / "observability-dashboard.html").is_file()
