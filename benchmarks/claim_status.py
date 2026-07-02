from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from agenvantage.provider_validation import (
    fixture_readiness_report,
    load_provider_validation_dataset,
    summarize_saved_provider_validation_report,
    summarize_provider_validation_records,
)
from agenvantage.tokenizer import TokenCounter


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_USE_CASE_JSON = REPO_ROOT / "artifacts" / "use-case-validation.json"
DEFAULT_PROVIDER_JSON = REPO_ROOT / "artifacts" / "provider-validation.json"
DEFAULT_PROVIDER_FIXTURE = REPO_ROOT / "examples" / "provider_validation_cases.json"
DEFAULT_OUTPUT_JSON = REPO_ROOT / "artifacts" / "claim-status.json"
DEFAULT_OUTPUT_MD = REPO_ROOT / "docs" / "claim-status.md"


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _narrow_claim_status(use_case_report: dict[str, Any] | None) -> dict[str, Any]:
    if use_case_report is None:
        return {
            "supported": False,
            "statement": "AgenVantage improves pre-inference repository-context packaging.",
            "reason": "No use-case validation artifact was found.",
        }

    summary = use_case_report.get("summary", {})
    weighted_reduction = summary.get("weighted_reduction_percent")
    grounding_pass_rate = summary.get("grounding_sufficiency_pass_rate")
    answer_rubric_pass_rate = summary.get("answer_rubric_pass_rate")
    case_count = summary.get("case_count")
    supported = bool(
        case_count
        and weighted_reduction is not None
        and grounding_pass_rate is not None
        and answer_rubric_pass_rate is not None
    )
    return {
        "supported": supported,
        "statement": (
            "AgenVantage reduces local candidate repository context while preserving "
            "grounding for many coding-task benchmarks."
        ),
        "reason": (
            f"The current artifact covers {case_count} hand-authored repository tasks with "
            f"{weighted_reduction}% weighted candidate-context reduction, "
            f"{grounding_pass_rate} grounding sufficiency pass rate, and "
            f"{answer_rubric_pass_rate} answer-rubric pass rate."
            if supported
            else "The use-case validation artifact is missing required summary metrics."
        ),
    }


def _unsupported_claim(
    supported: bool, reason: str, evidence: list[str], next_evidence: list[str]
) -> dict[str, Any]:
    return {
        "supported": supported,
        "reason": reason,
        "evidence": evidence,
        "next_evidence": next_evidence,
    }


def build_claim_status_report(
    use_case_report: dict[str, Any] | None,
    provider_report: dict[str, Any] | None,
    provider_dataset: Any,
    provider_fixture_report: dict[str, Any],
) -> dict[str, Any]:
    narrow_claim = _narrow_claim_status(use_case_report)
    use_case_summary = (use_case_report or {}).get("summary", {})

    if provider_report is not None:
        provider_summary = summarize_saved_provider_validation_report(
            provider_report,
            dataset=provider_dataset,
        )
        claim_audit = provider_summary.get("claim_audit", {})
        environment_scope = provider_summary.get("environment_scope")
        provider_evidence = [
            f"Provider validation artifact present with {provider_summary.get('record_count', 0)} recorded requests.",
            f"Environment scope: {environment_scope or 'unknown'}.",
        ]
        cost_reconciliation = provider_report.get("cost_reconciliation")
        if isinstance(cost_reconciliation, dict):
            provider_evidence.append(
                "Cost reconciliation data is present for request-level versus organization-level spend."
            )
    else:
        provider_summary = None
        claim_audit = {}
        environment_scope = provider_fixture_report.get("environment_scope")
        provider_evidence = [
            "No provider-validation artifact is present under artifacts/provider-validation.json.",
            (
                "The synthetic provider fixture is cache-ready "
                f"({provider_fixture_report.get('average_cache_aligned_stable_prefix_tokens')} stable-prefix tokens), "
                "but that is readiness evidence, not measured provider usage."
            ),
        ]

    real_cost = claim_audit.get("real_api_cost_savings", {})
    experiment_latency = claim_audit.get("latency_improvement", {})
    production_latency = claim_audit.get("latency_improvement_in_production", {})
    broad_quality = claim_audit.get("broad_quality_retention", {})
    end_to_end = claim_audit.get("end_to_end_context_overload", {})

    resume_claims = {
        "solved_agent_context_overload_end_to_end": _unsupported_claim(
            bool(end_to_end.get("supported")),
            end_to_end.get(
                "reason",
                "No end-to-end provider-backed artifact exists yet.",
            ),
            [
                (
                    f"Use-case benchmark shows {use_case_summary.get('weighted_reduction_percent')}% "
                    "weighted candidate-context reduction with repository-grounding checks."
                )
                if use_case_report is not None
                else "No checked use-case benchmark artifact is present."
            ]
            + provider_evidence,
            [
                "Record provider-backed cost and latency results for the compared policies.",
                "Run a broad answer-quality evaluation with enough distinct tasks to satisfy the declared tolerance.",
                "Keep the result production-scoped if you want the strongest end-to-end claim.",
            ],
        ),
        "proved_real_api_cost_savings": _unsupported_claim(
            bool(real_cost.get("supported")),
            real_cost.get(
                "reason",
                "No measured provider-cost artifact is present yet.",
            ),
            provider_evidence,
            [
                "Run validate-provider live with a real pricing snapshot, or normalize saved raw telemetry into provider-validation records.",
                "If you have an OpenAI Costs API export, reconcile it against the saved provider report.",
                "Show lower mean request cost for budgeted_cache_aligned than full_unaligned.",
            ],
        ),
        "proved_latency_improvements_in_production": _unsupported_claim(
            bool(production_latency.get("supported")),
            production_latency.get(
                "reason",
                "No production-scoped latency artifact is present yet.",
            ),
            provider_evidence
            + [
                f"Current provider fixture scope: {environment_scope or 'unknown'}.",
            ],
            [
                "Collect latency measurements from a production-scoped workload or telemetry source, then normalize or replay them.",
                "Show a better p50 than full_unaligned with enough production requests.",
            ],
        ),
        "proved_downstream_model_answer_quality_retention_across_broad_workloads": _unsupported_claim(
            bool(broad_quality.get("supported")),
            broad_quality.get(
                "reason",
                "No broad provider-backed answer-quality artifact is present yet.",
            ),
            [
                (
                    f"Local repository benchmark currently covers {use_case_summary.get('case_count')} "
                    "hand-authored retrieval tasks, which is narrower than broad downstream model quality."
                )
                if use_case_report is not None
                else "No checked retrieval benchmark artifact is present."
            ]
            + provider_evidence,
            [
                "Run provider validation across at least 30 distinct cases and 6 or more failure types.",
                "Show no material regression in correctness, safety, and grounded citation pass rates.",
            ],
        ),
    }

    return {
        "project": "AgenVantage",
        "narrow_supported_claim": narrow_claim,
        "use_case_validation_summary": use_case_summary or None,
        "provider_fixture_readiness": provider_fixture_report,
        "provider_validation_summary": provider_summary,
        "resume_claims": resume_claims,
    }


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AgenVantage Claim Status",
        "",
        "## Current Supported Claim",
        "",
        f"- Supported: `{report['narrow_supported_claim']['supported']}`",
        f"- Statement: {report['narrow_supported_claim']['statement']}",
        f"- Reason: {report['narrow_supported_claim']['reason']}",
        "",
        "## Current Evidence",
        "",
    ]

    use_case_summary = report.get("use_case_validation_summary")
    if use_case_summary:
        lines.extend(
            [
                f"- Use-case cases: `{use_case_summary.get('case_count')}`",
                f"- Weighted candidate-context reduction: `{use_case_summary.get('weighted_reduction_percent')}%`",
                f"- Grounding sufficiency pass rate: `{use_case_summary.get('grounding_sufficiency_pass_rate')}`",
                f"- Answer-rubric pass rate: `{use_case_summary.get('answer_rubric_pass_rate')}`",
            ]
        )
    else:
        lines.append("- No use-case validation artifact found.")

    fixture = report["provider_fixture_readiness"]
    lines.extend(
        [
            f"- Provider fixture scope: `{fixture.get('environment_scope')}`",
            f"- Provider fixture cases: `{fixture.get('case_count')}`",
            f"- Cache-ready stable prefix: `{fixture.get('average_cache_aligned_stable_prefix_tokens')}` tokens",
            f"- Average budgeted reduction vs full: `{fixture.get('average_budgeted_reduction_percent_vs_full')}%`",
        ]
    )
    if report.get("provider_validation_summary") is None:
        lines.append("- No live provider-validation artifact found.")
    else:
        lines.append(
            f"- Live provider records: `{report['provider_validation_summary'].get('record_count')}`"
        )

    lines.extend(
        [
            "",
            "## Resume-Risk Claims",
            "",
            "| Claim | Supported | Reason |",
            "| --- | --- | --- |",
        ]
    )
    for claim_name, payload in report["resume_claims"].items():
        lines.append(
            f"| `{claim_name}` | `{payload['supported']}` | {payload['reason']} |"
        )

    lines.extend(["", "## What To Run Next", ""])
    seen: set[str] = set()
    for payload in report["resume_claims"].values():
        for item in payload["next_evidence"]:
            if item in seen:
                continue
            seen.add(item)
            lines.append(f"- {item}")

    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Summarize what AgenVantage can and cannot currently claim."
    )
    parser.add_argument("--use-case-json", type=Path, default=DEFAULT_USE_CASE_JSON)
    parser.add_argument("--provider-json", type=Path, default=DEFAULT_PROVIDER_JSON)
    parser.add_argument("--provider-fixture", type=Path, default=DEFAULT_PROVIDER_FIXTURE)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_OUTPUT_MD)
    args = parser.parse_args()

    use_case_report = _load_json(args.use_case_json)
    provider_report = _load_json(args.provider_json)

    dataset = load_provider_validation_dataset(args.provider_fixture)
    provider_fixture = fixture_readiness_report(dataset, TokenCounter())
    report = build_claim_status_report(
        use_case_report,
        provider_report,
        dataset,
        provider_fixture,
    )

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    args.output_md.write_text(_render_markdown(report), encoding="utf-8")

    print(json.dumps(report, indent=2))
    print(f"\nJSON report written to {args.output_json.resolve()}")
    print(f"Markdown report written to {args.output_md.resolve()}")


if __name__ == "__main__":
    main()
