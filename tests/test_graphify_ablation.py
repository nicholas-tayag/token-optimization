from __future__ import annotations

import json
from pathlib import Path

from benchmarks.graphify_ablation import DEFAULT_FIXTURE, _summary

RESULTS = Path(__file__).resolve().parents[1] / "examples" / "graphify_ablation_results.json"


def _case(
    *,
    baseline_covered: int,
    graph_covered: int,
    baseline_missing: int,
    graph_missing: int,
    graph_edit: list[str] | None = None,
) -> dict:
    slots = {f"slot-{index}": {} for index in range(4)}
    expected_edit = ["src/main.py"]
    expected_test = ["tests/test_main.py"]
    baseline = {
        "covered_evidence_slots": [f"slot-{index}" for index in range(baseline_covered)],
        "missing_expansion_count": baseline_missing,
        "surface_edit_targets": expected_edit,
        "surface_test_targets": expected_test,
        "packed_prompt_tokens": 1000,
        "pack_runtime_ms": 10.0,
        "graph_runtime_ms": 0.0,
        "graph": {"used": False},
    }
    graphify = {
        "covered_evidence_slots": [f"slot-{index}" for index in range(graph_covered)],
        "missing_expansion_count": graph_missing,
        "surface_edit_targets": expected_edit if graph_edit is None else graph_edit,
        "surface_test_targets": expected_test,
        "packed_prompt_tokens": 1010,
        "pack_runtime_ms": 12.0,
        "graph_runtime_ms": 2.0,
        "graph": {"used": True},
    }
    return {
        "repository_id": "repo",
        "evidence_slots": slots,
        "expected_edit_targets": expected_edit,
        "expected_test_targets": expected_test,
        "baseline": baseline,
        "graphify": graphify,
    }


def test_graphify_ablation_fixture_has_required_scope() -> None:
    payload = json.loads(DEFAULT_FIXTURE.read_text(encoding="utf-8"))

    assert payload["graphify"]["version"] == "0.9.13"
    assert len(payload["repositories"]) == 2
    assert len(payload["cases"]) == 10
    assert all(case["evidence_slots"] for case in payload["cases"])


def test_summary_accepts_missing_expansion_gain_without_recall_regression() -> None:
    summary = _summary(
        [
            _case(
                baseline_covered=2,
                graph_covered=2,
                baseline_missing=4,
                graph_missing=2,
            )
        ]
    )

    assert summary["evidence_recall_delta_percentage_points"] == 0.0
    assert summary["missing_expansion_reduction_percent"] == 50.0
    assert summary["acceptance"]["overall_pass"] is True


def test_summary_rejects_edit_target_regression() -> None:
    summary = _summary(
        [
            _case(
                baseline_covered=2,
                graph_covered=3,
                baseline_missing=4,
                graph_missing=2,
                graph_edit=[],
            )
        ]
    )

    assert summary["acceptance"]["recall_or_expansion_gate"] is True
    assert summary["acceptance"]["no_edit_test_recall_regression"] is False
    assert summary["acceptance"]["overall_pass"] is False


def test_checked_graphify_results_recompute_missing_expansion_totals() -> None:
    payload = json.loads(RESULTS.read_text(encoding="utf-8"))

    assert payload["summary"]["case_count"] == len(payload["cases"]) == 10
    assert payload["summary"]["baseline_missing_expansions"] == sum(
        case["baseline_missing_expansions"] for case in payload["cases"]
    )
    assert payload["summary"]["graphify_missing_expansions"] == sum(
        case["graphify_missing_expansions"] for case in payload["cases"]
    )
    assert payload["summary"]["acceptance"]["overall_pass"] is True
    assert payload["claim_boundary"]["task_level_usefulness_supported"] is False
