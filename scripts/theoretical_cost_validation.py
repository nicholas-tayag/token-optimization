#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from agenvantage.provider_validation import (
    assemble_validation_packages,
    load_pricing_snapshot,
    load_provider_validation_dataset,
)
from agenvantage.tokenizer import TokenCounter


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = REPO_ROOT / "examples" / "provider_validation_cases.json"
DEFAULT_PRICING = REPO_ROOT / "artifacts" / "openai-pricing.json"
DEFAULT_OUTPUT = REPO_ROOT / "artifacts" / "theoretical-input-cost-validation.json"
POLICIES = (
    "full_unaligned",
    "full_cache_aligned",
    "budgeted_unaligned",
    "budgeted_cache_aligned",
)


def _estimated_input_cost_usd(
    *,
    input_tokens: int,
    cached_input_tokens: int,
    input_price_per_million: float,
    cached_input_price_per_million: float,
) -> float:
    uncached_input_tokens = max(input_tokens - cached_input_tokens, 0)
    return round(
        (
            (uncached_input_tokens * input_price_per_million)
            + (cached_input_tokens * cached_input_price_per_million)
        )
        / 1_000_000,
        8,
    )


def build_theoretical_cost_report(
    *,
    fixture: Path,
    pricing_path: Path,
    budget: int | None = None,
) -> dict[str, Any]:
    dataset = load_provider_validation_dataset(fixture)
    pricing = load_pricing_snapshot(pricing_path)
    counter = TokenCounter(pricing.model)
    effective_budget = budget if budget is not None else dataset.budget

    policy_totals: dict[str, dict[str, Any]] = {
        policy: {
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "uncached_input_tokens": 0,
            "estimated_input_cost_usd": 0.0,
        }
        for policy in POLICIES
    }
    case_reports: list[dict[str, Any]] = []

    for case in dataset.cases:
        packages = assemble_validation_packages(dataset, case, counter, effective_budget)
        case_report: dict[str, Any] = {
            "case_id": case.case_id,
            "failure_type": case.failure_type,
        }
        for policy in POLICIES:
            package = packages[policy]
            cached_input_tokens = package.stable_prefix_tokens if "cache_aligned" in policy else 0
            uncached_input_tokens = package.input_tokens - cached_input_tokens
            estimated_input_cost_usd = _estimated_input_cost_usd(
                input_tokens=package.input_tokens,
                cached_input_tokens=cached_input_tokens,
                input_price_per_million=pricing.input_price_per_million,
                cached_input_price_per_million=pricing.cached_input_price_per_million,
            )
            case_report[policy] = {
                "input_tokens": package.input_tokens,
                "stable_prefix_tokens": package.stable_prefix_tokens,
                "cached_input_tokens": cached_input_tokens,
                "uncached_input_tokens": uncached_input_tokens,
                "estimated_input_cost_usd": estimated_input_cost_usd,
            }
            policy_totals[policy]["input_tokens"] += package.input_tokens
            policy_totals[policy]["cached_input_tokens"] += cached_input_tokens
            policy_totals[policy]["uncached_input_tokens"] += uncached_input_tokens
            policy_totals[policy]["estimated_input_cost_usd"] += estimated_input_cost_usd
        case_reports.append(case_report)

    for policy, totals in policy_totals.items():
        totals["estimated_input_cost_usd"] = round(totals["estimated_input_cost_usd"], 8)
        totals["mean_input_tokens"] = round(totals["input_tokens"] / len(case_reports), 2)
        totals["mean_estimated_input_cost_usd"] = round(
            totals["estimated_input_cost_usd"] / len(case_reports), 8
        )

    baseline = policy_totals["full_unaligned"]
    comparisons_vs_full_unaligned: dict[str, dict[str, Any]] = {}
    for policy, totals in policy_totals.items():
        input_tokens_saved = baseline["input_tokens"] - totals["input_tokens"]
        estimated_input_cost_saved = round(
            baseline["estimated_input_cost_usd"] - totals["estimated_input_cost_usd"],
            8,
        )
        comparisons_vs_full_unaligned[policy] = {
            "total_input_tokens_saved": input_tokens_saved,
            "input_reduction_percent": round(
                (input_tokens_saved / baseline["input_tokens"]) * 100, 2
            )
            if baseline["input_tokens"]
            else 0.0,
            "estimated_total_input_cost_saved_usd": estimated_input_cost_saved,
            "estimated_input_cost_reduction_percent": round(
                (estimated_input_cost_saved / baseline["estimated_input_cost_usd"]) * 100,
                2,
            )
            if baseline["estimated_input_cost_usd"]
            else 0.0,
        }

    return {
        "report_type": "theoretical_input_cost_validation",
        "dataset_id": dataset.dataset_id,
        "description": dataset.description,
        "environment_scope": dataset.environment_scope,
        "case_count": len(case_reports),
        "budget": effective_budget,
        "model": pricing.model,
        "pricing_snapshot": pricing.to_dict(),
        "assumption": (
            "Input-side theoretical cost only. Cache-aligned policies treat the leading "
            "stable prefix as cached input at the published cached-input rate. Output "
            "tokens, retries, cache misses, warm-up behavior, and provider billing "
            "reconciliation are excluded."
        ),
        "policy_totals": policy_totals,
        "comparisons_vs_full_unaligned": comparisons_vs_full_unaligned,
        "cases": case_reports,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Estimate input-side theoretical API savings from the provider-validation fixture."
    )
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--pricing", type=Path, default=DEFAULT_PRICING)
    parser.add_argument("--budget", type=int, default=None)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main() -> None:
    args = _parser().parse_args()
    report = build_theoretical_cost_report(
        fixture=args.fixture,
        pricing_path=args.pricing,
        budget=args.budget,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.output.resolve()}")
    for policy in POLICIES:
        totals = report["policy_totals"][policy]
        comparison = report["comparisons_vs_full_unaligned"][policy]
        print(
            f"{policy}: mean_input_tokens={totals['mean_input_tokens']} "
            f"mean_estimated_input_cost_usd={totals['mean_estimated_input_cost_usd']:.8f} "
            f"saved_vs_full_unaligned_usd={comparison['estimated_total_input_cost_saved_usd']:.8f} "
            f"saved_vs_full_unaligned_pct={comparison['estimated_input_cost_reduction_percent']:.2f}"
        )


if __name__ == "__main__":
    main()
