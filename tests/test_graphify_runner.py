from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from agenvantage.graphify_runner import run_graphify


def test_runner_checks_version_and_invokes_bounded_extract(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    output = tmp_path / "output"
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs):
        calls.append((command, kwargs))
        if command == ["graphify", "--version"]:
            return SimpleNamespace(returncode=0, stdout="graphify 0.9.13\n", stderr="")
        output.mkdir(parents=True, exist_ok=True)
        (output / "graph.json").write_text('{"nodes":[],"links":[]}', encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("agenvantage.graphify_runner.subprocess.run", fake_run)

    result = run_graphify(repo, output, timeout_seconds=5, max_output_bytes=1_000)

    assert result.available
    assert result.graph_path == output / "graph.json"
    assert calls[1][0] == [
        "graphify",
        "extract",
        ".",
        "--code-only",
        "--out",
        str(output.resolve()),
    ]
    assert calls[1][1]["timeout"] == 5
    assert calls[1][1]["check"] is False
    assert calls[1][1]["cwd"] == repo.resolve()
    assert "shell" not in calls[1][1]


def test_runner_falls_back_when_missing_or_incompatible(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def missing(_command: list[str], **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr("agenvantage.graphify_runner.subprocess.run", missing)
    absent = run_graphify(repo, tmp_path / "missing-output")
    assert absent.status == "unavailable"
    assert absent.reason == "graphify_missing"

    monkeypatch.setattr(
        "agenvantage.graphify_runner.subprocess.run",
        lambda _command, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="0.9.12\n",
            stderr="",
        ),
    )
    incompatible = run_graphify(repo, tmp_path / "old-output")
    assert incompatible.status == "unavailable"
    assert incompatible.reason == "incompatible_version"
    assert incompatible.version == "0.9.12"


def test_runner_falls_back_on_timeout_and_oversized_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    output = tmp_path / "output"
    invocation = 0

    def timeout_run(command: list[str], **kwargs):
        nonlocal invocation
        invocation += 1
        if invocation == 1:
            return SimpleNamespace(returncode=0, stdout="0.9.13\n", stderr="")
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr("agenvantage.graphify_runner.subprocess.run", timeout_run)
    timed_out = run_graphify(repo, output, timeout_seconds=1)
    assert timed_out.status == "unavailable"
    assert timed_out.reason == "extraction_timeout"

    invocation = 0

    def oversized_run(command: list[str], **_kwargs):
        nonlocal invocation
        invocation += 1
        if invocation == 1:
            return SimpleNamespace(returncode=0, stdout="0.9.13\n", stderr="")
        output.mkdir(parents=True, exist_ok=True)
        (output / "graph.json").write_text("x" * 101, encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("agenvantage.graphify_runner.subprocess.run", oversized_run)
    oversized = run_graphify(repo, output, max_output_bytes=100)
    assert oversized.status == "unavailable"
    assert oversized.reason == "output_too_large"
