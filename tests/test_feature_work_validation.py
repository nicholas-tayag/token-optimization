from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.feature_work_validation import (
    DEFAULT_FIXTURE,
    DEFAULT_REPOS_ROOT,
    run_feature_work_validation,
)


def test_feature_work_validation_fixture_has_annotated_cases() -> None:
    payload = json.loads(DEFAULT_FIXTURE.read_text(encoding="utf-8"))

    assert payload["version"] == 1
    assert len(payload["cases"]) == 12
    assert {
        case["repository"] for case in payload["cases"]
    } == {"token-optimization", "mesh", "signalfoundry", "application-tracker"}
    for case in payload["cases"]:
        assert case["expected_edit_targets"]
        assert case["expected_test_targets"]
        assert case["required_observations"]
        assert case["answer_plan_rubric"]


def test_feature_work_validation_meets_phase_one_acceptance(tmp_path: Path) -> None:
    missing_repos = [
        name
        for name in ("token-optimization", "mesh", "signalfoundry", "application-tracker")
        if not (DEFAULT_REPOS_ROOT / name).is_dir()
    ]
    if missing_repos:
        pytest.skip(f"local benchmark repositories are unavailable: {missing_repos}")

    report = run_feature_work_validation(
        output_json=tmp_path / "feature-work-validation.json",
        output_md=tmp_path / "feature-work-validation.md",
    )

    summary = report["summary"]
    assert summary["case_count"] == 12
    assert summary["edit_target_recall"] >= 0.9
    assert summary["test_target_recall"] >= 0.8
    assert summary["required_observation_recall"] >= 0.8
    assert summary["answer_plan_pass_rate"] >= 0.8
    assert summary["median_token_reduction_percent"] >= 85.0
    assert summary["median_pack_runtime_ms"] > 0
    assert summary["total_pack_runtime_ms"] >= summary["median_pack_runtime_ms"]
    assert all(case["pack_runtime_ms"] > 0 for case in report["cases"])
    assert summary["acceptance"]["overall_pass"] is True
