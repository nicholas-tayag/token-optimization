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


def test_validate_provider_dry_run_summary() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agenvantage",
            "validate-provider",
            "--dry-run",
            "--summary",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "AgenVantage provider-validation fixture" in completed.stdout
    assert "cache_ready=True" in completed.stdout


def test_validate_provider_normalize_records_summary(tmp_path: Path) -> None:
    records_path = tmp_path / "records.json"
    output_path = tmp_path / "provider-validation.json"
    pricing_path = tmp_path / "pricing.json"
    costs_path = tmp_path / "costs.json"
    otel_path = tmp_path / "provider-validation-otel.json"
    records_path.write_text(
        json.dumps(
            [
                {
                    "case_id": "checkout-payment-connectivity-a",
                    "policy_id": "full_unaligned",
                    "failure_type": "payment_service_unreachable",
                    "latency_ms": 930.0,
                    "input_tokens": 2200,
                    "cached_input_tokens": 0,
                    "output_tokens": 220,
                    "grade": {
                        "correctness_pass": True,
                        "safety_pass": True,
                        "grounded_citation_pass": True,
                        "overall_pass": True,
                        "score": 1.0,
                    },
                },
                {
                    "case_id": "checkout-payment-connectivity-a",
                    "policy_id": "budgeted_cache_aligned",
                    "failure_type": "payment_service_unreachable",
                    "latency_ms": 710.0,
                    "input_tokens": 1700,
                    "cached_input_tokens": 1200,
                    "output_tokens": 220,
                    "grade": {
                        "correctness_pass": True,
                        "safety_pass": True,
                        "grounded_citation_pass": True,
                        "overall_pass": True,
                        "score": 1.0,
                    },
                },
            ],
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    pricing_path.write_text(
        json.dumps(
            {
                "provider": "openai",
                "model": "gpt-test",
                "captured_at": "2026-07-02",
                "source_url": "https://developers.openai.com/api/docs/pricing",
                "prices_per_million": {
                    "input": 1.0,
                    "cached_input": 0.1,
                    "output": 2.0,
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    costs_path.write_text(
        json.dumps(
            {
                "data": [
                    {
                        "object": "bucket",
                        "start_time": 0,
                        "end_time": 9999999999,
                        "results": [
                            {
                                "object": "organization.costs.result",
                                "amount": {"value": 0.004, "currency": "usd"},
                                "line_item": "responses",
                                "project_id": "proj_eval",
                            }
                        ],
                    }
                ]
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agenvantage",
            "validate-provider",
            "--normalize",
            str(records_path),
            "--pricing",
            str(pricing_path),
            "--environment-scope",
            "production",
            "--reconcile-costs",
            str(costs_path),
            "--records",
            str(output_path),
            "--otel-export",
            str(otel_path),
            "--summary",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "AgenVantage provider validation" in completed.stdout
    assert "Paired deltas (candidate - baseline):" in completed.stdout
    assert "Evidence readiness:" in completed.stdout
    assert "Cost reconciliation:" in completed.stdout
    assert "OTLP-style export written to" in completed.stdout
    report = json.loads(output_path.read_text(encoding="utf-8"))
    otel_export = json.loads(otel_path.read_text(encoding="utf-8"))
    assert report["environment_scope"] == "production"
    assert report["claim_audit"]["real_api_cost_savings"]["supported"] is True
    assert report["cost_reconciliation"]["recorded_organization_total_cost_usd"] == 0.004
    assert otel_export["resourceSpans"][0]["scopeSpans"][0]["spans"]


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
        "prompt_token_accounting": {
            "original_user_prompt_tokens": 5,
            "full_scan_prompt_tokens": 2048,
            "packed_prompt_tokens": 512,
            "prompt_tokens_saved_vs_full_scan": 1536,
            "prompt_reduction_percent_vs_full_scan": 75.0,
        },
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
    assert "Prompt tokens: user=5 full-scan=2048 packed=512" in summary
    assert "Prompt savings: 1536 tokens (75.0%)" in summary
    assert "Uncovered concepts: retry" in summary
    assert "src/rate_limiter.py" in summary


def test_format_pack_summary_reports_feature_change_surface() -> None:
    report = {
        "task": "Add rate limiter tests",
        "repo_count": 1,
        "scanned_files": 4,
        "budget": 6000,
        "selected_context_tokens": 700,
        "candidate_context_tokens": 4000,
        "local_reduction_percent_vs_candidate_context": 82.5,
        "local_tokens_omitted_vs_candidate_context": 3300,
        "prompt_token_accounting": {
            "original_user_prompt_tokens": 4,
            "full_scan_prompt_tokens": 4000,
            "packed_prompt_tokens": 700,
            "prompt_tokens_saved_vs_full_scan": 3300,
            "prompt_reduction_percent_vs_full_scan": 82.5,
        },
        "candidate_chunks": 12,
        "uncovered_query_terms": [],
        "provenance": {"enabled": False},
        "change_surface": {
            "edit_targets": [{"path": "src/rate_limiter.py"}],
            "test_targets": [{"path": "tests/test_rate_limiter.py"}],
            "config_targets": [],
            "supporting_targets": [{"path": "src/redis_client.py"}],
            "missing_signals": ["No config target was needed."],
        },
        "selected_chunks": [
            {"path": "src/rate_limiter.py", "tokens": 400},
            {"path": "tests/test_rate_limiter.py", "tokens": 300},
        ],
    }

    summary = _format_pack_summary(report, "feature")

    assert "Likely edit files: src/rate_limiter.py" in summary
    assert "Tests to inspect: tests/test_rate_limiter.py" in summary
    assert "Supporting files: src/redis_client.py" in summary
    assert "Missing signals:" in summary


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


def test_pack_feature_handoff_json_emits_agent_ready_payload(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_rate_limiter.py").write_text(
        "def test_rate_limiter_fail_open():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agenvantage",
            "pack",
            "--preset",
            "feature",
            "--task",
            "Add tests for rate limiter Redis fail open behavior",
            "--handoff-json",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(completed.stdout)
    assert payload["workflow"] == "feature"
    assert payload["task_suffix"] == "Add tests for rate limiter Redis fail open behavior"
    assert "# AgenVantage Context Package" in payload["system_prefix"]
    assert "prompt_markdown" in payload
    assert payload["prompt_token_accounting"]["packed_prompt_tokens"] > 0
    assert "prompt_tokens_saved_vs_full_scan" in payload["prompt_token_accounting"]
    assert payload["selected_chunks"]
    assert "src/rate_limiter.py" in payload["change_surface"]["edit_targets"]
    assert "tests/test_rate_limiter.py" in payload["change_surface"]["test_targets"]


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
