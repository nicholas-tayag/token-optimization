from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.feature_work_validation import (
    DEFAULT_FIXTURE,
    DEFAULT_REPOS_ROOT,
    _context_plan_readiness,
    _summarize,
    run_feature_work_validation,
)


def test_context_plan_readiness_preserves_boundary_predicate() -> None:
    common = {
        "selected_edit_recall": 1.0,
        "selected_test_recall": 1.0,
        "selected_config_recall": 1.0,
        "expected_test_targets": ["tests/test_feature.py"],
        "expected_config_targets": [],
        "missing_signals": [],
        "guard_ready": True,
        "sufficiency_status": "sufficient",
        "handoff_ready": True,
    }
    assert _context_plan_readiness(observation_recall=0.8, **common) is True
    assert _context_plan_readiness(observation_recall=0.7999, **common) is False


def test_manual_answer_plan_rubric_does_not_affect_readiness() -> None:
    case = {
        "context_plan_readiness": True,
        "answer_plan_rubric": ["manual note"],
        "repository": "repo",
        "edit_target_recall": 1.0,
        "test_target_recall": 1.0,
        "selected_edit_target_recall": 1.0,
        "selected_test_target_recall": 1.0,
        "required_observation_recall": 0.8,
        "token_reduction_percent": 90.0,
        "prompt_tokens_saved_vs_full_scan": 1,
        "packed_prompt_tokens": 1,
        "full_scan_prompt_tokens": 2,
        "pack_runtime_ms": 1,
        "selected_chunk_count": 1,
        "has_missing_signal_warning": False,
    }
    acceptance = {
        "minimum_edit_target_recall": 0.0,
        "minimum_test_target_recall": 0.0,
        "minimum_required_observation_recall": 0.0,
        "minimum_context_plan_readiness_rate": 0.8,
        "minimum_median_token_reduction_percent": 0.0,
    }
    original = _summarize([case], acceptance)
    case["answer_plan_rubric"] = ["different manual note", "not scored"]
    changed = _summarize([case], acceptance)
    assert changed["context_plan_readiness_rate"] == original["context_plan_readiness_rate"]
    assert changed["acceptance"]["passes"] == original["acceptance"]["passes"]


def test_context_plan_readiness_acceptance_uses_point_eighty_five_boundary() -> None:
    base = {
        "answer_plan_rubric": [],
        "repository": "repo",
        "edit_target_recall": 1.0,
        "test_target_recall": 1.0,
        "selected_edit_target_recall": 1.0,
        "selected_test_target_recall": 1.0,
        "required_observation_recall": 1.0,
        "token_reduction_percent": 90.0,
        "prompt_tokens_saved_vs_full_scan": 1,
        "packed_prompt_tokens": 1,
        "full_scan_prompt_tokens": 2,
        "pack_runtime_ms": 1,
        "selected_chunk_count": 1,
        "has_missing_signal_warning": False,
    }
    acceptance = {
        "minimum_edit_target_recall": 0.0,
        "minimum_test_target_recall": 0.0,
        "minimum_required_observation_recall": 0.0,
        "minimum_context_plan_readiness_rate": 0.85,
        "minimum_median_token_reduction_percent": 0.0,
    }
    passing = [dict(base, context_plan_readiness=index < 17) for index in range(20)]
    failing = [dict(base, context_plan_readiness=index < 16) for index in range(20)]
    assert _summarize(passing, acceptance)["acceptance"]["overall_pass"] is True
    assert _summarize(failing, acceptance)["acceptance"]["overall_pass"] is False


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
    assert summary["context_plan_readiness_rate"] >= 0.85
    assert summary["median_token_reduction_percent"] >= 85.0
    assert summary["median_pack_runtime_ms"] > 0
    assert summary["total_pack_runtime_ms"] >= summary["median_pack_runtime_ms"]
    assert all(case["pack_runtime_ms"] > 0 for case in report["cases"])
    assert summary["acceptance"]["overall_pass"] is True
