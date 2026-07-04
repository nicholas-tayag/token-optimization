from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

from agenvantage.feature_provider_validation import (
    assemble_feature_prompt_variants,
    load_feature_provider_dataset,
)
from agenvantage.modality import estimate_modality_tradeoff, summarize_modality_tradeoffs
from agenvantage.tokenizer import TokenCounter


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = REPO_ROOT / "examples" / "feature_work_validation_cases.json"
DEFAULT_OUTPUT_JSON = REPO_ROOT / "artifacts" / "modality-tradeoff-validation.json"
DEFAULT_OUTPUT_MD = REPO_ROOT / "artifacts" / "modality-tradeoff-validation.md"
DEFAULT_REPOS_ROOT = REPO_ROOT.parent


def _percent_reduction(baseline: int, candidate: int) -> float:
    if baseline <= 0:
        return 0.0
    return round(((baseline - candidate) / baseline) * 100, 2)


def _run_case(dataset: Any, case: Any, repos_root: Path, counter: TokenCounter) -> dict[str, Any]:
    variants, manifest = assemble_feature_prompt_variants(dataset, case, repos_root, counter)
    full = variants["full_unaligned"]
    packed = variants["budgeted_unaligned"]
    full_tradeoff = estimate_modality_tradeoff(
        full.prompt,
        full.local_prompt_tokens,
        path=f"{case.repository}:full_scan_prompt",
        kind="full_scan_prompt",
    )
    packed_tradeoff = estimate_modality_tradeoff(
        packed.prompt,
        packed.local_prompt_tokens,
        path=f"{case.repository}:agenvantage_packed_prompt",
        kind="agenvantage_packed_prompt",
    )
    retrieval_tokens_saved = full.local_prompt_tokens - packed.local_prompt_tokens
    safe_multimodal_candidate_tokens = (
        packed_tradeoff["estimated_image_tokens"]
        if packed_tradeoff["should_image"]
        else packed.local_prompt_tokens
    )
    incremental_modality_tokens_saved = (
        packed.local_prompt_tokens - safe_multimodal_candidate_tokens
    )
    return {
        "case_id": case.case_id,
        "repository": case.repository,
        "task": case.task,
        "full_scan_text_tokens": full.local_prompt_tokens,
        "packed_text_tokens": packed.local_prompt_tokens,
        "retrieval_tokens_saved": retrieval_tokens_saved,
        "retrieval_reduction_percent": _percent_reduction(
            full.local_prompt_tokens,
            packed.local_prompt_tokens,
        ),
        "packed_safe_multimodal_candidate_tokens": safe_multimodal_candidate_tokens,
        "incremental_modality_tokens_saved_after_packing": incremental_modality_tokens_saved,
        "incremental_modality_reduction_percent_after_packing": _percent_reduction(
            packed.local_prompt_tokens,
            safe_multimodal_candidate_tokens,
        ),
        "end_to_end_safe_candidate_tokens": safe_multimodal_candidate_tokens,
        "end_to_end_safe_candidate_reduction_percent": _percent_reduction(
            full.local_prompt_tokens,
            safe_multimodal_candidate_tokens,
        ),
        "full_scan_tradeoff": full_tradeoff,
        "packed_tradeoff": packed_tradeoff,
        "selected_paths": manifest["selected_paths"],
    }


def _median(values: list[float]) -> float:
    return round(statistics.median(values), 2) if values else 0.0


def _summarize(cases: list[dict[str, Any]]) -> dict[str, Any]:
    full_tradeoffs = [case["full_scan_tradeoff"] for case in cases]
    packed_tradeoffs = [case["packed_tradeoff"] for case in cases]
    retrieval_saved = [float(case["retrieval_tokens_saved"]) for case in cases]
    incremental_saved = [
        float(case["incremental_modality_tokens_saved_after_packing"]) for case in cases
    ]
    end_reduction = [
        float(case["end_to_end_safe_candidate_reduction_percent"]) for case in cases
    ]
    return {
        "case_count": len(cases),
        "repositories": sorted({case["repository"] for case in cases}),
        "scope": "theoretical_pxpipe_inspired_modality_gate_not_live_image_transport",
        "full_scan_tradeoffs": summarize_modality_tradeoffs(full_tradeoffs),
        "packed_tradeoffs": summarize_modality_tradeoffs(packed_tradeoffs),
        "median_retrieval_tokens_saved": _median(retrieval_saved),
        "median_incremental_modality_tokens_saved_after_packing": _median(incremental_saved),
        "median_end_to_end_safe_candidate_reduction_percent": _median(end_reduction),
        "total_retrieval_tokens_saved": int(sum(retrieval_saved)),
        "total_incremental_modality_tokens_saved_after_packing": int(sum(incremental_saved)),
        "notes": [
            "Retrieval reduction is shipped AgenVantage behavior.",
            "Modality reduction is a theoretical estimate inspired by pxpipe's image-token packing.",
            "Rows with verbatim-risk labels are conservatively kept as text.",
        ],
    }


def _render_markdown(summary: dict[str, Any], cases: list[dict[str, Any]]) -> str:
    full = summary["full_scan_tradeoffs"]
    packed = summary["packed_tradeoffs"]
    lines = [
        "# Modality Tradeoff Validation",
        "",
        "This report estimates whether pxpipe-style image packing could add value after AgenVantage selects repository context.",
        "",
        "It is theoretical: AgenVantage does not send image-packed prompts in this benchmark, and these numbers are not provider-billed savings.",
        "",
        "## Summary",
        "",
        f"- Cases: `{summary['case_count']}`",
        f"- Repositories: `{', '.join(summary['repositories'])}`",
        f"- Full-scan median text tokens: `{full['median_text_tokens']}`",
        f"- Full-scan median estimated image tokens: `{full['median_estimated_image_tokens']}`",
        f"- Full-scan median theoretical modality reduction: `{full['median_estimated_reduction_percent']}%`",
        f"- Full-scan image-candidate rate: `{full['image_candidate_rate']}`",
        f"- Packed median text tokens: `{packed['median_text_tokens']}`",
        f"- Packed median estimated image tokens: `{packed['median_estimated_image_tokens']}`",
        f"- Packed median theoretical modality reduction: `{packed['median_estimated_reduction_percent']}%`",
        f"- Packed image-candidate rate: `{packed['image_candidate_rate']}`",
        f"- Packed risk keep-text rate: `{packed['risk_keep_text_rate']}`",
        f"- Median retrieval tokens saved: `{summary['median_retrieval_tokens_saved']}`",
        f"- Median incremental modality tokens saved after packing: `{summary['median_incremental_modality_tokens_saved_after_packing']}`",
        f"- Median end-to-end safe candidate reduction: `{summary['median_end_to_end_safe_candidate_reduction_percent']}%`",
        "",
        "## Cases",
        "",
    ]
    for case in cases:
        packed_tradeoff = case["packed_tradeoff"]
        lines.extend(
            [
                f"### {case['case_id']}",
                "",
                f"- Repository: `{case['repository']}`",
                f"- Retrieval reduction: `{case['retrieval_reduction_percent']}%`",
                f"- Packed text tokens: `{case['packed_text_tokens']}`",
                f"- Packed estimated image tokens: `{packed_tradeoff['estimated_image_tokens']}`",
                f"- Packed modality decision: `{packed_tradeoff['decision_reason']}`",
                f"- Incremental modality reduction after packing: `{case['incremental_modality_reduction_percent_after_packing']}%`",
                f"- Risk labels: `{', '.join(packed_tradeoff['risk_labels']) or 'none'}`",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def run_modality_tradeoff_validation(
    fixture: Path = DEFAULT_FIXTURE,
    repos_root: Path = DEFAULT_REPOS_ROOT,
    output_json: Path | None = DEFAULT_OUTPUT_JSON,
    output_md: Path | None = DEFAULT_OUTPUT_MD,
) -> dict[str, Any]:
    dataset = load_feature_provider_dataset(fixture)
    counter = TokenCounter()
    cases = [_run_case(dataset, case, repos_root, counter) for case in dataset.cases]
    summary = _summarize(cases)
    report = {"summary": summary, "cases": cases}
    if output_json is not None:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if output_md is not None:
        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text(_render_markdown(summary, cases), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--repos-root", type=Path, default=DEFAULT_REPOS_ROOT)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_OUTPUT_MD)
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    report = run_modality_tradeoff_validation(
        fixture=args.fixture,
        repos_root=args.repos_root,
        output_json=args.output_json,
        output_md=args.output_md,
    )
    if args.summary:
        print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
