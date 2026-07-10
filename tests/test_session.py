from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from agenvantage.session import (
    build_session_task,
    create_feature_session,
    load_session_artifact,
)
from agenvantage.tokenizer import TokenCounter


def _create_feature_repo(root: Path) -> None:
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "docs").mkdir()
    (root / "src" / "search.py").write_text(
        "def search_memories(query):\n"
        "    diagnostics = {'source': 'local', 'count': 0}\n"
        "    return {'results': [], 'diagnostics': diagnostics}\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_search.py").write_text(
        "from src.search import search_memories\n\n"
        "def test_search_diagnostics():\n"
        "    assert search_memories('needle')['diagnostics']['source'] == 'local'\n",
        encoding="utf-8",
    )
    (root / "docs" / "archive.md").write_text(
        "Historical context unrelated to memory search diagnostics.\n" * 80,
        encoding="utf-8",
    )


def test_feature_session_splits_stable_prefix_from_dynamic_task(tmp_path: Path) -> None:
    _create_feature_repo(tmp_path)

    session = create_feature_session(
        [tmp_path],
        "Add memory search diagnostics and cover them in tests.",
        budget=2600,
        counter=TokenCounter(),
        top_k=8,
        instructions="Use the provided repository context.",
        session_id="feature-test",
    )
    task = build_session_task(
        session,
        "Add a follow-up metric for empty memory search results.",
        TokenCounter(),
    )

    assert session["workflow"] == "feature_session"
    assert session["session_id"] == "feature-test"
    assert "## Current Task" in session["initial_prompt_markdown"]
    assert "Add memory search diagnostics" not in session["stable_prefix_markdown"]
    assert task["workflow"] == "feature_session_task"
    assert task["cache"]["stable_prefix_sha256"] == session["cache"]["stable_prefix_sha256"]
    assert task["cache"]["prompt_tokens"] > task["cache"]["dynamic_packet_tokens"]
    assert (
        task["cache"]["estimated_reusable_tokens_after_cache_hit"]
        == task["cache"]["stable_prefix_tokens"]
    )


def test_session_cli_init_and_task_emit_machine_readable_artifacts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _create_feature_repo(repo)
    session_path = tmp_path / "session.json"
    task_manifest = tmp_path / "task.json"

    init = subprocess.run(
        [
            sys.executable,
            "-m",
            "agenvantage",
            "session",
            "init",
            "--repo",
            str(repo),
            "--task",
            "Add memory search diagnostics and test coverage.",
            "--output",
            str(session_path),
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    session = json.loads(init.stdout)
    saved_session = load_session_artifact(session_path)

    assert session["session_id"] == saved_session["session_id"]
    assert "Session written to" in init.stderr
    assert saved_session["cache"]["initial_prompt_tokens"] > 0

    task = subprocess.run(
        [
            sys.executable,
            "-m",
            "agenvantage",
            "session",
            "task",
            "--session",
            str(session_path),
            "--task",
            "Add an empty-result diagnostic counter.",
            "--manifest",
            str(task_manifest),
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    task_payload = json.loads(task.stdout)
    saved_task = json.loads(task_manifest.read_text(encoding="utf-8"))

    assert task_payload["workflow"] == "feature_session_task"
    assert task_payload["cache"]["stable_prefix_sha256"] == saved_session["cache"][
        "stable_prefix_sha256"
    ]
    assert saved_task["prompt_markdown"] == task_payload["prompt_markdown"]
    assert "Session task manifest written to" in task.stderr
