from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from agenvantage.cli import (
    _DEFAULT_BUDGET,
    _DEFAULT_FIXTURE,
    _format_experiment_summary,
    _format_pack_summary,
)


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
    (root / "src").mkdir()
    (root / "src" / "rate_limiter.py").write_text(
        "def rate_limiter(redis):\n"
        "    # fail open when redis is unavailable\n"
        "    return redis.consume('ratelimit')\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", "."], cwd=root, check=True, capture_output=True, text=True
    )


def test_format_experiment_summary_includes_policy_rows() -> None:
    report = {
        "scenario_id": "demo-scenario",
        "scenario_description": "Synthetic demo case.",
        "tokenizer": {"model": "gpt-4o-mini", "encoding": "o200k_base"},
        "policies": [
            {
                "policy": "full",
                "input_tokens": 100,
                "stable_prefix_tokens": 40,
                "tokens_saved_vs_full": 0,
                "token_reduction_percent_vs_full": 0.0,
                "excluded_components": [],
            },
            {
                "policy": "cache_aligned",
                "input_tokens": 100,
                "stable_prefix_tokens": 55,
                "tokens_saved_vs_full": 0,
                "token_reduction_percent_vs_full": 0.0,
                "excluded_components": [],
            },
            {
                "policy": "budgeted",
                "input_tokens": 80,
                "stable_prefix_tokens": 55,
                "budget": 90,
                "tokens_saved_vs_full": 20,
                "token_reduction_percent_vs_full": 20.0,
                "excluded_components": [{"id": "noise", "reason": "exceeds token budget"}],
            },
        ],
    }

    summary = _format_experiment_summary(report)

    assert "demo-scenario" in summary
    assert "full" in summary
    assert "budgeted" in summary
    assert "excluded: noise" in summary


def test_run_command_uses_defaults_and_writes_report(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agenvantage",
            "run",
            "--summary",
            "--output",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Policy comparison:" in completed.stdout
    assert completed.stderr == ""
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["scenario_id"] == "synthetic-checkout-latency-incident"
    assert _DEFAULT_FIXTURE.is_file()
    assert _DEFAULT_BUDGET == 360


def test_format_pack_summary_reports_budget_and_files() -> None:
    report = {
        "task": "Explain the rate limiter",
        "repo_count": 1,
        "scanned_files": 3,
        "budget": 6000,
        "selected_context_tokens": 512,
        "candidate_context_tokens": 2048,
        "local_reduction_percent_vs_candidate_context": 75.0,
        "local_tokens_omitted_vs_candidate_context": 1536,
        "candidate_chunks": 9,
        "uncovered_query_terms": ["retry"],
        "provenance": {"enabled": True, "include_diff": True, "include_log": False, "selected_provenance_tokens": 40},
        "selected_chunks": [
            {"path": "src/rate_limiter.py", "tokens": 300},
            {"path": "src/rate_limiter.py", "tokens": 100},
            {"path": "tests/test_rate_limiter.py", "tokens": 112},
        ],
    }

    summary = _format_pack_summary(report, "review")

    assert "Preset:  review" in summary
    assert "512 / 6000 tokens used" in summary
    assert "75.0%" in summary
    assert "Uncovered concepts: retry" in summary
    assert "src/rate_limiter.py" in summary


def test_pack_command_uses_smart_defaults_and_summary(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agenvantage",
            "pack",
            "--task",
            "Explain the rate limiter fail open behavior",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "AgenVantage context package" in completed.stdout
    assert "Preset:  explain" in completed.stdout
    assert "Reduction:" in completed.stdout


def test_pack_stdout_emits_markdown_package(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agenvantage",
            "pack",
            "--task",
            "Explain the rate limiter fail open behavior",
            "--stdout",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "# AgenVantage Context Package" in completed.stdout
    assert "## Task" in completed.stdout


def test_pack_preset_debug_enables_provenance_in_manifest(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agenvantage",
            "pack",
            "--preset",
            "debug",
            "--task",
            "Debug the rate limiter",
            "--json",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    report = json.loads(completed.stdout)
    assert report["preset"] == "debug"
    assert report["provenance"]["include_diff"] is True
    assert report["provenance"]["include_log"] is True


def test_pack_reads_project_config_budget(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / ".agenvantage.toml").write_text(
        "[pack]\nbudget = 512\npreset = \"review\"\n", encoding="utf-8"
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agenvantage",
            "pack",
            "--task",
            "Review the rate limiter",
            "--json",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    report = json.loads(completed.stdout)
    assert report["budget"] == 512
    assert report["preset"] == "review"


def test_demo_command_writes_default_report(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "oncall-report.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agenvantage",
            "demo",
            "--output",
            str(output),
            "--no-browser",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "AgenVantage experiment" in completed.stdout
    assert "Dashboard:" in completed.stdout
    assert output.is_file()
