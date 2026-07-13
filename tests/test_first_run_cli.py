from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _run(home: Path, *arguments: str) -> dict:
    completed = subprocess.run(
        [sys.executable, "-m", "agenvantage", *arguments],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home)},
    )
    return json.loads(completed.stdout)


def test_init_installs_all_agent_skills_and_is_idempotent(tmp_path: Path) -> None:
    first = _run(tmp_path, "init", "--json")
    second = _run(tmp_path, "init", "--json")

    assert first["ready"] is True
    assert [item["target"] for item in first["integrations"]] == [
        "cursor",
        "codex",
        "claude",
    ]
    assert all(item["current"] for item in first["integrations"])
    assert all(item["changed"] for item in first["integrations"])
    assert all(not item["changed"] for item in second["integrations"])
    assert first["mcp_command"].endswith(" -m agenvantage mcp")


def test_doctor_reports_skills_and_mcp_command(tmp_path: Path) -> None:
    _run(tmp_path, "init", "--json")

    report = _run(tmp_path, "doctor", "--json")

    assert report["status"] == "ready"
    assert all(item["current"] for item in report["skills"])
    assert report["mcp_command"].endswith(" -m agenvantage mcp")
    assert report["executables"]["git"]


def test_mcp_cli_serves_initialize_and_tools_list() -> None:
    requests = "\n".join(
        [
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {},
                }
            ),
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {},
                }
            ),
            "",
        ]
    )

    completed = subprocess.run(
        [sys.executable, "-m", "agenvantage", "mcp"],
        input=requests,
        check=True,
        capture_output=True,
        text=True,
    )
    responses = [json.loads(line) for line in completed.stdout.splitlines()]

    assert responses[0]["result"]["serverInfo"]["name"] == "agenvantage"
    assert {tool["name"] for tool in responses[1]["result"]["tools"]} == {
        "prepare_context",
        "search_graph",
        "context_status",
    }
