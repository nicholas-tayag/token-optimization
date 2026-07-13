from __future__ import annotations

import json
from pathlib import Path

import pytest


RESULTS = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "paired_feature_implementation_results.json"
)


def test_paired_feature_results_recompute_from_leaf_records() -> None:
    payload = json.loads(RESULTS.read_text(encoding="utf-8"))
    cases = payload["cases"]
    aggregate = payload["aggregate"]

    for case in cases:
        for variant in ("control", "treatment"):
            record = case[variant]
            assert record["wall_time_seconds"] > 0
            assert record["input_tokens"] >= record["cached_input_tokens"] >= 0
            assert record["uncached_input_tokens"] == (
                record["input_tokens"] - record["cached_input_tokens"]
            )

    def total(variant: str, field: str) -> int | float:
        return sum(case[variant][field] for case in cases)

    assert aggregate["case_count"] == len(cases)
    assert aggregate["control_successes"] == sum(
        bool(case["control"]["task_success"]) for case in cases
    )
    assert aggregate["treatment_successes"] == sum(
        bool(case["treatment"]["task_success"]) for case in cases
    )
    assert aggregate["control_input_tokens"] == total("control", "input_tokens")
    assert aggregate["treatment_input_tokens"] == total("treatment", "input_tokens")
    assert aggregate["control_cached_input_tokens"] == total(
        "control", "cached_input_tokens"
    )
    assert aggregate["treatment_cached_input_tokens"] == total(
        "treatment", "cached_input_tokens"
    )
    assert aggregate["control_uncached_input_tokens"] == total(
        "control", "uncached_input_tokens"
    )
    assert aggregate["treatment_uncached_input_tokens"] == total(
        "treatment", "uncached_input_tokens"
    )
    assert aggregate["control_output_tokens"] == total("control", "output_tokens")
    assert aggregate["treatment_output_tokens"] == total(
        "treatment", "output_tokens"
    )
    assert aggregate["control_reasoning_output_tokens"] == total(
        "control", "reasoning_output_tokens"
    )
    assert aggregate["treatment_reasoning_output_tokens"] == total(
        "treatment", "reasoning_output_tokens"
    )
    assert aggregate["control_wall_time_seconds"] == pytest.approx(total(
        "control", "wall_time_seconds"
    ))
    assert aggregate["treatment_wall_time_seconds"] == pytest.approx(total(
        "treatment", "wall_time_seconds"
    ))

    control_per_success = aggregate["control_input_tokens"] / aggregate[
        "control_successes"
    ]
    treatment_per_success = aggregate["treatment_input_tokens"] / aggregate[
        "treatment_successes"
    ]
    assert aggregate["control_input_tokens_per_success"] == control_per_success
    assert aggregate["treatment_input_tokens_per_success"] == treatment_per_success
