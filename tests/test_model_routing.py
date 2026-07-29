from __future__ import annotations

import pytest

from agenvantage.model_routing import TaskDescriptor, route_task


@pytest.mark.parametrize(
    ("task", "kind"),
    [
        ("Research the retry behavior in this module", "research"),
        ("Add tests for the parser", "tests"),
        ("Update the README troubleshooting guide", "docs"),
        ("Make an isolated change in one file", "isolated_module"),
    ],
)
def test_bounded_work_uses_low_cost_routing(task: str, kind: str) -> None:
    decision = route_task(task)

    assert decision.descriptor.kind == kind
    assert decision.model_tier == "low"
    assert decision.estimated_relative_cost_tier == "low"
    assert decision.reasoning_effort == "low"
    assert decision.escalate is False


@pytest.mark.parametrize(
    "task",
    [
        "Redesign the architecture for all services",
        "Threat model the authentication flow",
        "Perform the database schema migration and backfill",
        "Make a cross-cutting change across the whole repo",
    ],
)
def test_high_risk_work_escalates(task: str) -> None:
    decision = route_task(task)

    assert decision.model_tier == "high"
    assert decision.estimated_relative_cost_tier == "high"
    assert decision.reasoning_effort == "high"
    assert decision.escalate is True


def test_failed_attempt_overrides_bounded_task() -> None:
    decision = route_task("Add tests for the parser", failed_attempts=1)

    assert decision.descriptor.kind == "tests"
    assert decision.model_tier == "high"
    assert decision.escalate is True
    assert "prior failure" in decision.reasons[0]


def test_descriptor_normalizes_text_and_serializes_machine_output() -> None:
    descriptor = TaskDescriptor.from_task("  Update   the README  ")

    assert descriptor.normalized_task == "update the readme"
    assert descriptor.to_dict() == {
        "task": "Update   the README",
        "normalized_task": "update the readme",
        "kind": "docs",
        "matched_signals": ["readme"],
        "failed_attempts": 0,
    }
    output = route_task(descriptor).to_dict()
    assert output["estimated_relative_cost_tier"] == "low"
    assert output["reasoning_effort"] == "low"


def test_unknown_work_uses_standard_tier() -> None:
    decision = route_task("Implement the requested behavior")

    assert decision.model_tier == "standard"
    assert decision.estimated_relative_cost_tier == "standard"
    assert decision.reasoning_effort == "medium"
    assert decision.escalate is False


@pytest.mark.parametrize("task", ["", "   ", None])
def test_task_must_be_non_empty(task: str | None) -> None:
    with pytest.raises(ValueError, match="non-empty string"):
        route_task(task)  # type: ignore[arg-type]


def test_failed_attempts_must_be_non_negative() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        TaskDescriptor.from_task("Run tests", failed_attempts=-1)
