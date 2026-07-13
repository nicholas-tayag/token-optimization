from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agenvantage.agent_launcher import resolve_agent_command


def test_codex_command_uses_context_stdin_and_workspace_sandbox(
    tmp_path: Path,
) -> None:
    command = resolve_agent_command(
        "codex",
        repo=tmp_path,
        executable="/opt/codex",
        agent_model="gpt-test",
    )

    assert command == [
        "/opt/codex",
        "exec",
        "--cd",
        str(tmp_path.resolve()),
        "--sandbox",
        "workspace-write",
        "--model",
        "gpt-test",
        "-",
    ]


def test_claude_command_uses_print_mode_and_accept_edits(tmp_path: Path) -> None:
    command = resolve_agent_command(
        "claude",
        repo=tmp_path,
        executable="/opt/claude",
        agent_model="sonnet",
    )

    assert command == [
        "/opt/claude",
        "--print",
        "--permission-mode",
        "acceptEdits",
        "--model",
        "sonnet",
    ]


def test_agent_command_rejects_unknown_provider(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        resolve_agent_command("other", repo=tmp_path)


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_agent_alias_dry_run_prepares_context_without_launching(
    tmp_path: Path,
    provider: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "def process_order(order):\n    return order\n",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "AGENVANTAGE_INDEX_ROOT": str(tmp_path / "index"),
    }

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agenvantage",
            provider,
            "Add process_order validation",
            "--repo",
            str(repo),
            "--executable",
            "/bin/echo",
            "--dry-run",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    payload = json.loads(completed.stdout)

    assert payload["provider"] == provider
    assert payload["repo"] == str(repo)
    assert payload["packed_prompt_tokens"] <= 6000
    assert payload["selected_paths"] == ["app.py"]
