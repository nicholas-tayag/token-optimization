"""Deterministic policy for optional repository-graph retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agenvantage.context_planner import ContextPlan


SMALL_REPOSITORY_FILES = 20
LARGE_REPOSITORY_FILES = 80
LARGE_REPOSITORY_CHUNKS = 240

_CROSS_FILE_TERMS = {
    "across",
    "caller",
    "callers",
    "callee",
    "callees",
    "cross-file",
    "dependencies",
    "dependency",
    "flow",
    "integration",
    "related",
}
_INVESTIGATION_TERMS = {
    "bug",
    "debug",
    "failure",
    "failing",
    "regression",
    "review",
    "trace",
}


@dataclass(frozen=True)
class GraphPolicyDecision:
    requested_mode: str
    enabled: bool
    decision: str
    reasons: tuple[str, ...]
    inputs: dict[str, Any]
    thresholds: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_mode": self.requested_mode,
            "enabled": self.enabled,
            "decision": self.decision,
            "reasons": list(self.reasons),
            "inputs": self.inputs,
            "thresholds": self.thresholds,
        }


def decide_graph_policy(
    *,
    requested_mode: str,
    plan: ContextPlan,
    workflow: str,
    scanned_files: int,
    candidate_chunks: int,
    repo_count: int,
    explicit_graph_allowed: bool,
    explicit_graph_reason: str,
) -> GraphPolicyDecision:
    """Choose graph retrieval after baseline lexical/structural exploration."""

    inputs = {
        "workflow": workflow,
        "scanned_files": scanned_files,
        "candidate_chunks": candidate_chunks,
        "repo_count": repo_count,
        "grounding_status": plan.grounding.status,
        "task_shapes": list(plan.shapes),
        "explicit_path_count": len(plan.paths),
        "identifier_count": len(plan.identifiers),
    }
    thresholds = {
        "small_repository_files": SMALL_REPOSITORY_FILES,
        "large_repository_files": LARGE_REPOSITORY_FILES,
        "large_repository_chunks": LARGE_REPOSITORY_CHUNKS,
    }
    if requested_mode == "off":
        return GraphPolicyDecision(
            requested_mode, False, "skip", ("explicit_off",), inputs, thresholds
        )
    if requested_mode == "graphify":
        return GraphPolicyDecision(
            requested_mode,
            explicit_graph_allowed,
            "use" if explicit_graph_allowed else "skip",
            (explicit_graph_reason,),
            inputs,
            thresholds,
        )

    terms = {term.casefold() for term in plan.task.replace("/", " ").split()}
    normalized_terms = {term.strip(".,:;!?()[]{}") for term in terms}
    cross_file = bool(normalized_terms & _CROSS_FILE_TERMS)
    investigation = bool(normalized_terms & _INVESTIGATION_TERMS)
    compound = "compound" in plan.shapes
    partially_grounded = plan.grounding.status != "grounded"
    explicit_signal = bool(plan.paths or plan.identifiers)
    large_repo = (
        scanned_files >= LARGE_REPOSITORY_FILES
        or candidate_chunks >= LARGE_REPOSITORY_CHUNKS
        or repo_count > 1
    )
    small_repo = scanned_files <= SMALL_REPOSITORY_FILES and repo_count == 1

    reasons: list[str] = []
    if partially_grounded:
        reasons.append(f"grounding_{plan.grounding.status}")
    if compound:
        reasons.append("compound_task")
    if cross_file:
        reasons.append("cross_file_task_language")
    if investigation:
        reasons.append("investigation_task_language")
    if explicit_signal:
        reasons.append("explicit_code_signal")
    if large_repo:
        reasons.append("large_repository")

    useful_shape = partially_grounded or compound or cross_file or investigation
    enabled = (large_repo and (useful_shape or explicit_signal)) or (
        not small_repo and (compound or cross_file or (partially_grounded and investigation))
    )
    if enabled:
        return GraphPolicyDecision(
            requested_mode, True, "use", tuple(reasons), inputs, thresholds
        )
    if small_repo:
        reasons.append("small_repository")
    if plan.grounding.status == "grounded":
        reasons.append("already_grounded")
    if not useful_shape:
        reasons.append("single_file_or_low_ambiguity_task")
    return GraphPolicyDecision(
        requested_mode, False, "skip", tuple(dict.fromkeys(reasons)), inputs, thresholds
    )


__all__ = ["GraphPolicyDecision", "decide_graph_policy"]
