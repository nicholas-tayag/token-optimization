from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.session_cache_validation import (
    DEFAULT_FIXTURE,
    DEFAULT_REPOS_ROOT,
    run_session_cache_validation,
)


def _create_repo(root: Path) -> None:
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
        "Historical release notes unrelated to memory search diagnostics.\n" * 500,
        encoding="utf-8",
    )


def _write_fixture(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "description": "Tiny session cache fixture.",
                "cases": [
                    {
                        "case_id": "toy-search-session",
                        "repository": "toy",
                        "budget": 2000,
                        "top_k": 8,
                        "task": "Add search diagnostics and cover them in the server test.",
                        "expected_edit_targets": ["src/search.py"],
                        "expected_test_targets": ["tests/test_search.py"],
                        "expected_config_targets": [],
                        "required_observations": [
                            {
                                "label": "search_diagnostics_exist",
                                "any_of": [["diagnostics", "source"]],
                            }
                        ],
                        "answer_plan_rubric": ["Names server and test files."],
                        "session_followup_task": "Add an empty-result diagnostic counter.",
                    }
                ],
                "session_cache_acceptance": {
                    "minimum_cache_eligible_rate": 0.0,
                    "minimum_median_reusable_prefix_percent": 80.0,
                    "maximum_median_dynamic_packet_tokens": 120,
                    "minimum_median_warm_reduction_percent_vs_full_scan": 80.0,
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_session_cache_validation_reports_reusable_prefix_metrics(tmp_path: Path) -> None:
    repos_root = tmp_path / "repos"
    repos_root.mkdir()
    toy = repos_root / "toy"
    toy.mkdir()
    _create_repo(toy)
    fixture = tmp_path / "session_cases.json"
    _write_fixture(fixture)

    report = run_session_cache_validation(
        fixture=fixture,
        repos_root=repos_root,
        output_json=tmp_path / "session-cache-validation.json",
        output_md=tmp_path / "session-cache-validation.md",
    )

    summary = report["summary"]
    case = report["cases"][0]
    assert summary["case_count"] == 1
    assert summary["median_dynamic_packet_tokens"] <= 120
    assert summary["median_reusable_prefix_percent"] >= 80.0
    assert summary["acceptance"]["overall_pass"] is True
    assert case["stable_prefix_tokens"] > case["dynamic_packet_tokens"]
    assert case["estimated_warm_tokens_saved_vs_full_scan"] > 0


def test_session_cache_validation_meets_default_feature_fixture_acceptance(
    tmp_path: Path,
) -> None:
    missing_repos = [
        name
        for name in ("token-optimization", "mesh", "signalfoundry", "application-tracker")
        if not (DEFAULT_REPOS_ROOT / name).is_dir()
    ]
    if missing_repos:
        pytest.skip(f"local benchmark repositories are unavailable: {missing_repos}")

    report = run_session_cache_validation(
        fixture=DEFAULT_FIXTURE,
        repos_root=DEFAULT_REPOS_ROOT,
        output_json=tmp_path / "session-cache-validation.json",
        output_md=tmp_path / "session-cache-validation.md",
    )

    summary = report["summary"]
    assert summary["case_count"] == 12
    assert summary["cache_eligible_rate"] >= 1.0
    assert summary["median_reusable_prefix_percent"] >= 95.0
    assert summary["median_dynamic_packet_tokens"] <= 120
    assert summary["median_estimated_warm_reduction_percent_vs_full_scan"] >= 95.0
    assert summary["acceptance"]["overall_pass"] is True
