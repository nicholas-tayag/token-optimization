from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

from agenvantage.presets import get_preset
from agenvantage.session import build_session_task, create_feature_session
from agenvantage.tokenizer import TokenCounter


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = REPO_ROOT / "examples" / "feature_work_validation_cases.json"
DEFAULT_OUTPUT_JSON = REPO_ROOT / "artifacts" / "session-cache-validation.json"
DEFAULT_OUTPUT_MD = REPO_ROOT / "artifacts" / "session-cache-validation.md"
DEFAULT_REPOS_ROOT = REPO_ROOT.parent
DEFAULT_ACCEPTANCE = {
    "minimum_cache_eligible_rate": 1.0,
    "minimum_median_reusable_prefix_percent": 95.0,
    "maximum_median_dynamic_packet_tokens": 120,
    "minimum_median_warm_reduction_percent_vs_full_scan": 95.0,
}


def _followup_task(case: dict[str, Any]) -> str:
    explicit = str(case.get("session_followup_task", "")).strip()
    if explicit:
        return explicit
    return (
        "Using the same feature-session context, plan the smallest safe follow-up "
        f"implementation step and tests for: {case['task']}"
    )


def _percent_reduction(baseline: int, candidate: int) -> float:
    if baseline <= 0:
        return 0.0
    return round(((baseline - candidate) / baseline) * 100, 2)


def _run_case(case: dict[str, Any], repos_root: Path, counter: TokenCounter) -> dict[str, Any]:
    repo_name = str(case["repository"])
    repo_path = repos_root / repo_name
    if not repo_path.is_dir():
        raise FileNotFoundError(f"Required benchmark repository is missing: {repo_path}")

    preset = get_preset("feature")
    session = create_feature_session(
        [repo_path],
        str(case["task"]),
        int(case.get("budget", 6000)),
        counter,
        top_k=int(case.get("top_k", 28)),
        instructions=preset.instructions,
        session_id=f"benchmark-{case['case_id']}",
    )
    followup_task = _followup_task(case)
    task = build_session_task(session, followup_task, counter)
    prompt_accounting = session.get("prompt_token_accounting", {})
    full_scan_prompt_tokens = int(prompt_accounting.get("full_scan_prompt_tokens", 0))
    dynamic_packet_tokens = int(task["cache"]["dynamic_packet_tokens"])
    return {
        "case_id": case["case_id"],
        "repository": repo_name,
        "initial_task": case["task"],
        "followup_task": followup_task,
        "full_scan_prompt_tokens": full_scan_prompt_tokens,
        "packed_initial_prompt_tokens": int(session["cache"]["initial_prompt_tokens"]),
        "stable_prefix_tokens": int(task["cache"]["stable_prefix_tokens"]),
        "dynamic_packet_tokens": dynamic_packet_tokens,
        "followup_prompt_tokens": int(task["cache"]["prompt_tokens"]),
        "cache_eligible": bool(task["cache"]["cache_eligible"]),
        "reusable_prefix_percent": float(task["cache"]["reusable_prefix_percent"]),
        "stable_prefix_sha256": str(task["cache"]["stable_prefix_sha256"]),
        "estimated_warm_uncached_tokens": int(
            task["cache"]["estimated_uncached_tokens_after_cache_hit"]
        ),
        "estimated_warm_reusable_tokens": int(
            task["cache"]["estimated_reusable_tokens_after_cache_hit"]
        ),
        "estimated_warm_tokens_saved_vs_full_scan": (
            full_scan_prompt_tokens - dynamic_packet_tokens
        ),
        "estimated_warm_reduction_percent_vs_full_scan": _percent_reduction(
            full_scan_prompt_tokens,
            dynamic_packet_tokens,
        ),
        "selected_chunk_count": len(session.get("selected_chunks", [])),
        "selected_secret_redaction_count": int(
            (session.get("safety") or {}).get("selected_secret_redaction_count", 0)
        ),
        "change_surface": session.get("change_surface", {}),
    }


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def _summarize(cases: list[dict[str, Any]], acceptance: dict[str, Any]) -> dict[str, Any]:
    cache_eligible_rate = _mean([1.0 if case["cache_eligible"] else 0.0 for case in cases])
    reusable_prefix = [float(case["reusable_prefix_percent"]) for case in cases]
    dynamic_tokens = [float(case["dynamic_packet_tokens"]) for case in cases]
    warm_reductions = [
        float(case["estimated_warm_reduction_percent_vs_full_scan"]) for case in cases
    ]
    full_scan_tokens = [float(case["full_scan_prompt_tokens"]) for case in cases]
    stable_prefix_tokens = [float(case["stable_prefix_tokens"]) for case in cases]
    warm_tokens_saved = [
        int(case["estimated_warm_tokens_saved_vs_full_scan"]) for case in cases
    ]
    summary = {
        "case_count": len(cases),
        "repositories": sorted({case["repository"] for case in cases}),
        "cache_eligible_rate": round(cache_eligible_rate, 4),
        "median_stable_prefix_tokens": round(_median(stable_prefix_tokens), 2),
        "median_dynamic_packet_tokens": round(_median(dynamic_tokens), 2),
        "median_full_scan_prompt_tokens": round(_median(full_scan_tokens), 2),
        "median_reusable_prefix_percent": round(_median(reusable_prefix), 2),
        "median_estimated_warm_reduction_percent_vs_full_scan": round(
            _median(warm_reductions),
            2,
        ),
        "median_estimated_warm_tokens_saved_vs_full_scan": round(
            _median([float(value) for value in warm_tokens_saved]),
            2,
        ),
        "total_estimated_warm_tokens_saved_vs_full_scan": sum(warm_tokens_saved),
    }
    summary["acceptance"] = {
        "minimums": acceptance,
        "passes": {
            "cache_eligible_rate": summary["cache_eligible_rate"]
            >= float(acceptance["minimum_cache_eligible_rate"]),
            "median_reusable_prefix_percent": summary["median_reusable_prefix_percent"]
            >= float(acceptance["minimum_median_reusable_prefix_percent"]),
            "median_dynamic_packet_tokens": summary["median_dynamic_packet_tokens"]
            <= float(acceptance["maximum_median_dynamic_packet_tokens"]),
            "median_estimated_warm_reduction_percent_vs_full_scan": summary[
                "median_estimated_warm_reduction_percent_vs_full_scan"
            ]
            >= float(acceptance["minimum_median_warm_reduction_percent_vs_full_scan"]),
        },
    }
    summary["acceptance"]["overall_pass"] = all(summary["acceptance"]["passes"].values())
    return summary


def _render_markdown(summary: dict[str, Any], cases: list[dict[str, Any]]) -> str:
    lines = [
        "# Session Cache Validation",
        "",
        "This report validates the no-spend repeated feature-session layout: a stable reusable prefix plus small dynamic task packets.",
        "",
        "## Summary",
        "",
        f"- Cases: `{summary['case_count']}`",
        f"- Repositories: `{', '.join(summary['repositories'])}`",
        f"- Cache-eligible rate: `{summary['cache_eligible_rate']}`",
        f"- Median stable prefix: `{summary['median_stable_prefix_tokens']}` tokens",
        f"- Median dynamic packet: `{summary['median_dynamic_packet_tokens']}` tokens",
        f"- Median full-scan prompt: `{summary['median_full_scan_prompt_tokens']}` tokens",
        f"- Median reusable prefix: `{summary['median_reusable_prefix_percent']}%`",
        f"- Median estimated warm reduction vs full scan: `{summary['median_estimated_warm_reduction_percent_vs_full_scan']}%`",
        f"- Median estimated warm tokens saved: `{summary['median_estimated_warm_tokens_saved_vs_full_scan']}`",
        f"- Total estimated warm tokens saved: `{summary['total_estimated_warm_tokens_saved_vs_full_scan']}`",
        f"- Acceptance pass: `{summary['acceptance']['overall_pass']}`",
        "",
        "These are cache-layout readiness metrics, not provider-confirmed cache hits or billed savings.",
        "",
        "## Cases",
        "",
    ]
    for case in cases:
        lines.extend(
            [
                f"### {case['case_id']}",
                "",
                f"- Repository: `{case['repository']}`",
                f"- Stable prefix: `{case['stable_prefix_tokens']}` tokens",
                f"- Dynamic packet: `{case['dynamic_packet_tokens']}` tokens",
                f"- Reusable prefix: `{case['reusable_prefix_percent']}%`",
                f"- Cache eligible: `{case['cache_eligible']}`",
                f"- Estimated warm reduction vs full scan: `{case['estimated_warm_reduction_percent_vs_full_scan']}%`",
                f"- Estimated warm tokens saved: `{case['estimated_warm_tokens_saved_vs_full_scan']}`",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def run_session_cache_validation(
    fixture: Path = DEFAULT_FIXTURE,
    repos_root: Path = DEFAULT_REPOS_ROOT,
    output_json: Path | None = DEFAULT_OUTPUT_JSON,
    output_md: Path | None = DEFAULT_OUTPUT_MD,
) -> dict[str, Any]:
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    acceptance = dict(DEFAULT_ACCEPTANCE)
    acceptance.update(payload.get("session_cache_acceptance", {}))
    counter = TokenCounter()
    cases = [_run_case(case, repos_root, counter) for case in payload["cases"]]
    summary = _summarize(cases, acceptance)
    report = {"summary": summary, "cases": cases}

    if output_json is not None:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if output_md is not None:
        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text(_render_markdown(summary, cases), encoding="utf-8")

    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate cache-aware feature-session layout against annotated repo tasks."
    )
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--repos-root", type=Path, default=DEFAULT_REPOS_ROOT)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_OUTPUT_MD)
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print only the summary JSON instead of the full report.",
    )
    args = parser.parse_args()

    report = run_session_cache_validation(
        fixture=args.fixture,
        repos_root=args.repos_root,
        output_json=args.output_json,
        output_md=args.output_md,
    )
    print(json.dumps(report["summary"] if args.summary else report, indent=2))
    print(f"\nJSON report written to {args.output_json.resolve()}")
    print(f"Markdown report written to {args.output_md.resolve()}")


if __name__ == "__main__":
    main()
