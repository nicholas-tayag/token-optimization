from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from agenvantage.cli import _DEFAULT_BUDGET, _DEFAULT_FIXTURE, _format_experiment_summary


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
