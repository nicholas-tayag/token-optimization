"""Deterministic cost-aware routing for repository subagent tasks.

This module only classifies task text and returns a routing recommendation. It
does not select a vendor, call a provider, or depend on an agent runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

TaskKind = Literal[
    "research",
    "tests",
    "docs",
    "isolated_module",
    "architecture",
    "security",
    "migration",
    "cross_cutting",
    "general",
]
CostTier = Literal["low", "standard", "high"]
ReasoningEffort = Literal["low", "medium", "high"]


_KIND_RULES: tuple[tuple[TaskKind, tuple[str, ...]], ...] = (
    ("security", ("security", "vulnerability", "threat model", "secret")),
    ("migration", ("migration", "migrate", "backfill", "schema change")),
    (
        "architecture",
        ("architecture", "redesign", "system design", "distributed", "trade-off"),
    ),
    (
        "cross_cutting",
        (
            "cross-cutting",
            "cross cutting",
            "whole repo",
            "all services",
            "global",
            "public api",
            "shared schema",
            "build system",
            "dependency upgrade",
        ),
    ),
    ("research", ("research", "investigate", "compare", "survey", "find out")),
    ("tests", ("test", "tests", "testing", "coverage", "fixture")),
    ("docs", ("docs", "documentation", "readme", "guide", "changelog")),
    ("isolated_module", ("isolated", "single module", "one file", "small fix")),
)

_FAILED_ATTEMPT_TERMS = (
    "failed",
    "failure",
    "still broken",
    "didn't work",
    "did not work",
    "regression",
)


@dataclass(frozen=True)
class TaskDescriptor:
    """Normalized, provider-independent signals used by the routing policy."""

    task: str
    normalized_task: str
    kind: TaskKind
    matched_signals: tuple[str, ...] = ()
    failed_attempts: int = 0

    @classmethod
    def from_task(cls, task: str, *, failed_attempts: int = 0) -> "TaskDescriptor":
        if not isinstance(task, str) or not task.strip():
            raise ValueError("task must be a non-empty string")
        if failed_attempts < 0:
            raise ValueError("failed_attempts must be non-negative")

        normalized = " ".join(task.lower().split())
        kind, signals = _classify(normalized)
        failed_signals = tuple(term for term in _FAILED_ATTEMPT_TERMS if term in normalized)
        return cls(
            task=task.strip(),
            normalized_task=normalized,
            kind=kind,
            matched_signals=signals + failed_signals,
            failed_attempts=failed_attempts,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return stable JSON-compatible output for logs and orchestration."""
        return {
            "task": self.task,
            "normalized_task": self.normalized_task,
            "kind": self.kind,
            "matched_signals": list(self.matched_signals),
            "failed_attempts": self.failed_attempts,
        }


@dataclass(frozen=True)
class RoutingDecision:
    """A deterministic model-routing recommendation."""

    model_tier: CostTier
    estimated_relative_cost_tier: CostTier
    reasoning_effort: ReasoningEffort
    escalate: bool
    reasons: tuple[str, ...]
    descriptor: TaskDescriptor

    def to_dict(self) -> dict[str, Any]:
        """Return machine-readable routing output without provider details."""
        return {
            "model_tier": self.model_tier,
            "estimated_relative_cost_tier": self.estimated_relative_cost_tier,
            "reasoning_effort": self.reasoning_effort,
            "escalate": self.escalate,
            "reasons": list(self.reasons),
            "task": self.descriptor.to_dict(),
        }


def _classify(normalized_task: str) -> tuple[TaskKind, tuple[str, ...]]:
    for kind, terms in _KIND_RULES:
        matches = tuple(term for term in terms if term in normalized_task)
        if matches:
            return kind, matches
    return "general", ()


def route_task(
    task: str | TaskDescriptor,
    *,
    failed_attempts: int = 0,
) -> RoutingDecision:
    """Route a task to a relative reasoning/cost tier.

    Escalation is intentionally monotonic: any explicit high-risk task kind or
    failed attempt produces a high-reasoning recommendation. Bounded task kinds
    use the low tier; unknown work uses the standard tier.
    """
    descriptor = (
        task
        if isinstance(task, TaskDescriptor)
        else TaskDescriptor.from_task(task, failed_attempts=failed_attempts)
    )

    escalation_reasons: list[str] = []
    if descriptor.kind in {"architecture", "security", "migration", "cross_cutting"}:
        escalation_reasons.append(f"{descriptor.kind} work benefits from deeper reasoning")
    if descriptor.failed_attempts or any(
        term in descriptor.matched_signals for term in _FAILED_ATTEMPT_TERMS
    ):
        escalation_reasons.append("prior failure signal requires a fresh higher-reasoning attempt")

    if escalation_reasons:
        return RoutingDecision(
            model_tier="high",
            estimated_relative_cost_tier="high",
            reasoning_effort="high",
            escalate=True,
            reasons=tuple(escalation_reasons),
            descriptor=descriptor,
        )

    if descriptor.kind in {"research", "tests", "docs", "isolated_module"}:
        return RoutingDecision(
            model_tier="low",
            estimated_relative_cost_tier="low",
            reasoning_effort="low",
            escalate=False,
            reasons=(f"bounded {descriptor.kind} task is suitable for a low-cost model",),
            descriptor=descriptor,
        )

    return RoutingDecision(
        model_tier="standard",
        estimated_relative_cost_tier="standard",
        reasoning_effort="medium",
        escalate=False,
        reasons=("task scope is not bounded or high-risk enough for automatic escalation",),
        descriptor=descriptor,
    )
