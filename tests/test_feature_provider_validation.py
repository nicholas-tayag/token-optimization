from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from agenvantage.feature_provider_validation import (
    feature_provider_fixture_readiness_report,
    grade_feature_provider_response,
    load_feature_provider_dataset,
    run_feature_provider_validation,
)
from agenvantage.provider_validation import PricingSnapshot
from agenvantage.tokenizer import TokenCounter


def _init_feature_repo(root: Path) -> None:
    root.mkdir()
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "docs").mkdir()
    (root / "src" / "server.py").write_text(
        "def handle_search(query):\n"
        "    diagnostics = {'source': 'local', 'result_count': 0}\n"
        "    return {'results': [], 'diagnostics': diagnostics}\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_server.py").write_text(
        "from src.server import handle_search\n\n"
        "def test_search_diagnostics():\n"
        "    response = handle_search('needle')\n"
        "    assert response['diagnostics']['source'] == 'local'\n",
        encoding="utf-8",
    )
    (root / "docs" / "archive.md").write_text(
        ("Historical release notes unrelated to search diagnostics.\n" * 500),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True, text=True)


def _write_feature_fixture(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "description": "Tiny feature-provider validation fixture.",
                "provider_validation": {
                    "dataset_id": "tiny-feature-provider-validation",
                    "environment_scope": "local_feature_work",
                    "minimum_cacheable_prefix_tokens": 128,
                    "recommended_warm_requests_per_policy": 2,
                    "minimum_distinct_cases_for_broad_claim": 2,
                    "minimum_failure_types_for_broad_claim": 1,
                },
                "cases": [
                    {
                        "case_id": "toy-search-diagnostics",
                        "repository": "toy",
                        "budget": 2000,
                        "top_k": 8,
                        "task": "Add search diagnostics and cover them in the server test.",
                        "expected_edit_targets": ["src/server.py"],
                        "expected_test_targets": ["tests/test_server.py"],
                        "expected_config_targets": [],
                        "required_observations": [
                            {
                                "label": "search_diagnostics_exist",
                                "any_of": [["diagnostics", "source"]],
                            }
                        ],
                        "answer_plan_rubric": ["Names server and test files."],
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


class _FakeFeatureTransport:
    def create_response(self, payload: dict[str, Any]) -> dict[str, Any]:
        cache_key = str(payload["prompt_cache_key"])
        if ":full_unaligned:" in cache_key:
            input_tokens = 2400
            cached_tokens = 0
        elif ":full_cache_aligned:" in cache_key:
            input_tokens = 2400
            cached_tokens = 1600
        elif ":budgeted_cache_aligned:" in cache_key:
            input_tokens = 900
            cached_tokens = 500
        else:
            input_tokens = 900
            cached_tokens = 0
        return {
            "output_text": json.dumps(
                {
                    "edit_files": ["src/server.py"],
                    "test_files": ["tests/test_server.py"],
                    "config_files": [],
                    "observations": [
                        {
                            "label": "search_diagnostics_exist",
                            "supported": True,
                            "evidence_files": ["src/server.py"],
                        }
                    ],
                    "missing_signals": [],
                    "implementation_steps": [
                        "Update src/server.py to keep search diagnostics explicit.",
                        "Extend tests/test_server.py around diagnostics.",
                    ],
                }
            ),
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": 120,
                "input_tokens_details": {"cached_tokens": cached_tokens},
            },
        }


def test_feature_provider_fixture_readiness_reports_prompt_savings(tmp_path: Path) -> None:
    repos_root = tmp_path / "repos"
    repos_root.mkdir()
    _init_feature_repo(repos_root / "toy")
    fixture = tmp_path / "feature_cases.json"
    _write_feature_fixture(fixture)
    dataset = load_feature_provider_dataset(fixture)

    pricing = PricingSnapshot(
        provider="openai",
        model="gpt-test",
        captured_at="2026-07-03",
        source_url="https://developers.openai.com/api/docs/pricing",
        input_price_per_million=1.0,
        cached_input_price_per_million=0.1,
        output_price_per_million=2.0,
    )

    report = feature_provider_fixture_readiness_report(
        dataset,
        repos_root,
        TokenCounter(),
        pricing=pricing,
    )

    assert report["workflow"] == "feature_provider_validation"
    assert report["case_count"] == 1
    assert report["pricing_snapshot"]["model"] == "gpt-test"
    assert (
        report["summary"]["policies"]["budgeted_unaligned"][
            "median_prompt_reduction_percent_vs_full_scan"
        ]
        > 0
    )
    cost = report["summary"]["estimated_input_cost"]
    assert cost["median_full_unaligned_cold_input_cost_usd"] > cost[
        "median_budgeted_cache_aligned_warm_input_cost_usd"
    ]
    assert cost["median_budgeted_cache_aligned_warm_input_cost_reduction_percent"] > 0
    assert report["cases"][0]["policies"]["full_unaligned"]["local_prompt_tokens"] > report[
        "cases"
    ][0]["policies"]["budgeted_unaligned"]["local_prompt_tokens"]
    assert "estimated_input_cost" in report["cases"][0]["policies"]["budgeted_cache_aligned"]


def test_grade_feature_provider_response_checks_files_and_observations(tmp_path: Path) -> None:
    fixture = tmp_path / "feature_cases.json"
    _write_feature_fixture(fixture)
    case = load_feature_provider_dataset(fixture).cases[0]

    grade = grade_feature_provider_response(
        case,
        json.dumps(
            {
                "edit_files": ["src/server.py"],
                "test_files": ["tests/test_server.py"],
                "config_files": [],
                "observations": [
                    {
                        "label": "search_diagnostics_exist",
                        "supported": True,
                        "evidence_files": ["src/server.py"],
                    }
                ],
                "missing_signals": [],
                "implementation_steps": ["Implement the diagnostics response."],
            }
        ),
        ("src/server.py", "tests/test_server.py"),
    )

    assert grade["correctness_pass"] is True
    assert grade["grounded_citation_pass"] is True
    assert grade["overall_pass"] is True


def test_run_feature_provider_validation_records_cost_and_quality(tmp_path: Path) -> None:
    repos_root = tmp_path / "repos"
    repos_root.mkdir()
    _init_feature_repo(repos_root / "toy")
    fixture = tmp_path / "feature_cases.json"
    _write_feature_fixture(fixture)
    dataset = load_feature_provider_dataset(fixture)
    pricing = PricingSnapshot(
        provider="openai",
        model="gpt-test",
        captured_at="2026-07-03",
        source_url="https://developers.openai.com/api/docs/pricing",
        input_price_per_million=1.0,
        cached_input_price_per_million=0.1,
        output_price_per_million=2.0,
    )

    report = run_feature_provider_validation(
        dataset,
        repos_root,
        TokenCounter(),
        _FakeFeatureTransport(),
        "gpt-test",
        pricing,
        max_cases=1,
    )

    assert report["workflow"] == "feature_provider_validation"
    assert report["record_count"] == 4
    assert report["policies"]["full_unaligned"]["mean_request_cost_usd"] > report[
        "policies"
    ]["budgeted_cache_aligned"]["mean_request_cost_usd"]
    assert report["policies"]["budgeted_cache_aligned"]["cache_hit_rate"] == 1.0
    assert report["claim_audit"]["real_api_cost_savings"]["supported"] is True
    assert report["claim_audit"]["broad_quality_retention"]["supported"] is False
    assert all(record["grade"]["overall_pass"] for record in report["records"])


def test_validate_feature_provider_dry_run_cli(tmp_path: Path) -> None:
    repos_root = tmp_path / "repos"
    repos_root.mkdir()
    _init_feature_repo(repos_root / "toy")
    fixture = tmp_path / "feature_cases.json"
    _write_feature_fixture(fixture)
    pricing = tmp_path / "pricing.json"
    pricing.write_text(
        json.dumps(
            {
                "provider": "openai",
                "model": "gpt-test",
                "captured_at": "2026-07-03",
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

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agenvantage",
            "validate-feature-provider",
            "--fixture",
            str(fixture),
            "--repos-root",
            str(repos_root),
            "--pricing",
            str(pricing),
            "--dry-run",
            "--summary",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "AgenVantage feature-provider fixture" in completed.stdout
    assert "Median prompt reduction:" in completed.stdout
    assert "Estimated input cost:" in completed.stdout
    assert "Estimated warm input savings:" in completed.stdout
