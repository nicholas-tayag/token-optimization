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
from agenvantage.modality import (
    apply_multimodal_pack,
    estimate_modality_tradeoff,
    summarize_modality_tradeoffs,
    verify_mixed_modality_manifest,
)
from agenvantage.tokenizer import TokenCounter


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = REPO_ROOT / "examples" / "feature_work_validation_cases.json"
DEFAULT_OUTPUT_JSON = REPO_ROOT / "artifacts" / "modality-tradeoff-validation.json"
DEFAULT_OUTPUT_MD = REPO_ROOT / "artifacts" / "modality-tradeoff-validation.md"
DEFAULT_REPOS_ROOT = REPO_ROOT.parent
DEFAULT_ARTIFACT_ROOT = REPO_ROOT / "artifacts" / "modality-context-images"


def _percent_reduction(baseline: int, candidate: int) -> float:
    if baseline <= 0:
        return 0.0
    return round(((baseline - candidate) / baseline) * 100, 2)


def _run_case(
    dataset: Any,
    case: Any,
    repos_root: Path,
    counter: TokenCounter,
    artifact_root: Path,
) -> dict[str, Any]:
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
    _, artifact_report = apply_multimodal_pack(
        packed.prompt,
        manifest,
        counter,
        mode="artifact",
        output_dir=artifact_root / str(case.case_id),
        profile_id="anthropic_standard",
    )
    artifact_plan = artifact_report["multimodal"]
    artifact_manifest_written = bool(artifact_plan.get("artifacts_written"))
    artifact_verification = {
        "ok": False,
        "recoverable_block_count": 0,
        "image_attachment_count": 0,
        "error_count": 0,
        "errors": [],
    }
    if artifact_manifest_written:
        try:
            artifact_verification = verify_mixed_modality_manifest(
                Path(str(artifact_plan["manifest_path"]))
            )
        except (FileNotFoundError, ValueError) as exc:
            artifact_verification = {
                "ok": False,
                "recoverable_block_count": 0,
                "image_attachment_count": 0,
                "error_count": 1,
                "errors": [str(exc)],
            }
    artifact_verification_status = (
        "verified"
        if artifact_manifest_written and artifact_verification["ok"]
        else "failed"
        if artifact_manifest_written
        else "not_written"
    )
    artifact_estimated_tokens = int(artifact_plan["estimated_mixed_prompt_tokens"])
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
        "artifact_mixed_estimated_tokens": artifact_estimated_tokens,
        "artifact_incremental_tokens_saved_after_packing": (
            packed.local_prompt_tokens - artifact_estimated_tokens
        ),
        "artifact_incremental_reduction_percent_after_packing": _percent_reduction(
            packed.local_prompt_tokens,
            artifact_estimated_tokens,
        ),
        "artifact_image_count": len(artifact_plan.get("image_attachments", [])),
        "artifact_recoverable_block_count": len(artifact_plan.get("recoverable_blocks", [])),
        "artifact_factsheet_count": len(artifact_plan.get("factsheets", [])),
        "artifact_factsheet_tokens": int(artifact_plan.get("factsheet_tokens", 0)),
        "artifact_decision_reason": artifact_plan.get("decision_reason"),
        "artifact_manifest_written": artifact_manifest_written,
        "artifact_manifest_verified": bool(
            artifact_manifest_written and artifact_verification["ok"]
        ),
        "artifact_manifest_verification_status": artifact_verification_status,
        "artifact_bundle_sha256": artifact_verification.get("artifact_bundle_sha256"),
        "artifact_bundle_verified": bool(
            artifact_manifest_written and artifact_verification.get("artifact_bundle_verified")
        ),
        "artifact_verification_error_count": int(artifact_verification["error_count"]),
        "artifact_verification_errors": artifact_verification["errors"],
        "artifact_manifest_path": artifact_plan.get("manifest_path"),
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
    artifact_incremental_saved = [
        float(case["artifact_incremental_tokens_saved_after_packing"]) for case in cases
    ]
    end_reduction = [
        float(case["end_to_end_safe_candidate_reduction_percent"]) for case in cases
    ]
    artifact_image_cases = sum(1 for case in cases if case["artifact_image_count"] > 0)
    artifact_manifest_cases = [case for case in cases if case["artifact_manifest_written"]]
    artifact_verified_cases = sum(
        1 for case in artifact_manifest_cases if case["artifact_manifest_verified"]
    )
    artifact_bundle_verified_cases = sum(
        1 for case in artifact_manifest_cases if case["artifact_bundle_verified"]
    )
    return {
        "case_count": len(cases),
        "repositories": sorted({case["repository"] for case in cases}),
        "scope": "local_pxpipe_inspired_artifact_pipeline_not_live_provider_usage",
        "full_scan_tradeoffs": summarize_modality_tradeoffs(full_tradeoffs),
        "packed_tradeoffs": summarize_modality_tradeoffs(packed_tradeoffs),
        "median_retrieval_tokens_saved": _median(retrieval_saved),
        "median_incremental_modality_tokens_saved_after_packing": _median(incremental_saved),
        "median_artifact_incremental_tokens_saved_after_packing": _median(
            artifact_incremental_saved
        ),
        "median_end_to_end_safe_candidate_reduction_percent": _median(end_reduction),
        "total_retrieval_tokens_saved": int(sum(retrieval_saved)),
        "total_incremental_modality_tokens_saved_after_packing": int(sum(incremental_saved)),
        "total_artifact_incremental_tokens_saved_after_packing": int(
            sum(artifact_incremental_saved)
        ),
        "artifact_image_case_rate": round(artifact_image_cases / len(cases), 4) if cases else 0.0,
        "total_artifact_image_count": sum(int(case["artifact_image_count"]) for case in cases),
        "total_artifact_recoverable_blocks": sum(
            int(case["artifact_recoverable_block_count"]) for case in cases
        ),
        "total_artifact_factsheets": sum(
            int(case["artifact_factsheet_count"]) for case in cases
        ),
        "artifact_manifest_written_case_count": len(artifact_manifest_cases),
        "artifact_manifest_verified_case_count": artifact_verified_cases,
        "artifact_manifest_verified_case_rate": (
            round(artifact_verified_cases / len(artifact_manifest_cases), 4)
            if artifact_manifest_cases
            else 0.0
        ),
        "artifact_bundle_verified_case_count": artifact_bundle_verified_cases,
        "artifact_bundle_verified_case_rate": (
            round(artifact_bundle_verified_cases / len(artifact_manifest_cases), 4)
            if artifact_manifest_cases
            else 0.0
        ),
        "artifact_manifest_verification_error_count": sum(
            int(case["artifact_verification_error_count"]) for case in cases
        ),
        "notes": [
            "Retrieval reduction is shipped AgenVantage behavior.",
            "Artifact-mode modality reduction is a local text+PNG handoff estimate, not billed provider usage.",
            "Exact edit/test/config/support chunks stay text; imaged blocks are recoverable.",
            "Generated artifact manifests are verified for recoverable-source hashes, factsheet hashes, image hashes, and PNG signatures.",
        ],
    }


def _render_markdown(summary: dict[str, Any], cases: list[dict[str, Any]]) -> str:
    full = summary["full_scan_tradeoffs"]
    packed = summary["packed_tradeoffs"]
    lines = [
        "# Modality Tradeoff Validation",
        "",
        "This report validates the local pxpipe-style artifact pipeline after AgenVantage selects repository context.",
        "",
        "It writes local PNG context pages when the safety and profitability gates pass. These numbers are still not provider-billed savings.",
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
        f"- Median artifact incremental tokens saved after packing: `{summary['median_artifact_incremental_tokens_saved_after_packing']}`",
        f"- Total artifact incremental tokens saved after packing: `{summary['total_artifact_incremental_tokens_saved_after_packing']}`",
        f"- Artifact image case rate: `{summary['artifact_image_case_rate']}`",
        f"- Total artifact images: `{summary['total_artifact_image_count']}`",
        f"- Total recoverable blocks: `{summary['total_artifact_recoverable_blocks']}`",
        f"- Total factsheets: `{summary['total_artifact_factsheets']}`",
        f"- Artifact manifests written: `{summary['artifact_manifest_written_case_count']}`",
        f"- Artifact manifests verified: `{summary['artifact_manifest_verified_case_count']}`",
        f"- Artifact manifest verification rate: `{summary['artifact_manifest_verified_case_rate']}`",
        f"- Artifact bundles verified: `{summary['artifact_bundle_verified_case_count']}`",
        f"- Artifact bundle verification rate: `{summary['artifact_bundle_verified_case_rate']}`",
        f"- Artifact manifest verification errors: `{summary['artifact_manifest_verification_error_count']}`",
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
                f"- Artifact mixed estimated tokens: `{case['artifact_mixed_estimated_tokens']}`",
                f"- Artifact incremental savings: `{case['artifact_incremental_tokens_saved_after_packing']}`",
                f"- Artifact decision: `{case['artifact_decision_reason']}`",
                f"- Artifact images: `{case['artifact_image_count']}`",
                f"- Recoverable blocks: `{case['artifact_recoverable_block_count']}`",
                f"- Factsheets: `{case['artifact_factsheet_count']}`",
                f"- Artifact manifest verification: `{case['artifact_manifest_verification_status']}`",
                f"- Artifact bundle verified: `{case['artifact_bundle_verified']}`",
                f"- Artifact verification errors: `{case['artifact_verification_error_count']}`",
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
    artifact_root: Path = DEFAULT_ARTIFACT_ROOT,
) -> dict[str, Any]:
    dataset = load_feature_provider_dataset(fixture)
    counter = TokenCounter()
    cases = [
        _run_case(dataset, case, repos_root, counter, artifact_root)
        for case in dataset.cases
    ]
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
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    report = run_modality_tradeoff_validation(
        fixture=args.fixture,
        repos_root=args.repos_root,
        output_json=args.output_json,
        output_md=args.output_md,
        artifact_root=args.artifact_root,
    )
    if args.summary:
        print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
