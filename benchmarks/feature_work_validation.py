from __future__ import annotations

import argparse
import json
import re
import statistics
import time
from pathlib import Path
from typing import Any

from agenvantage.repo_context import build_context_package
from agenvantage.tokenizer import TokenCounter


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = REPO_ROOT / "examples" / "feature_work_validation_cases.json"
DEFAULT_OUTPUT_JSON = REPO_ROOT / "artifacts" / "feature-work-validation.json"
DEFAULT_OUTPUT_MD = REPO_ROOT / "artifacts" / "feature-work-validation.md"
DEFAULT_REPOS_ROOT = REPO_ROOT.parent


def _ordered_unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def _normalize_for_match(text: str) -> str:
    split = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text).replace("_", " ")
    return re.sub(r"[^a-z0-9]+", " ", split.lower()).strip()


def _paths_from_surface(change_surface: dict[str, Any] | None, key: str) -> list[str]:
    if not change_surface:
        return []
    return _ordered_unique(
        [str(item.get("path", "")) for item in change_surface.get(key, []) if item.get("path")]
    )


def _selected_paths(report: dict[str, Any]) -> list[str]:
    return _ordered_unique([chunk["path"] for chunk in report["selected_chunks"]])


def _selected_file_texts(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for chunk in report["selected_chunks"]:
        absolute_path = Path(chunk["repo_path"]) / chunk["relative_path"]
        try:
            lines = absolute_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        snippet = "\n".join(lines[chunk["start_line"] - 1 : chunk["end_line"]])
        current = selected.setdefault(chunk["path"], {"text_parts": [], "chunk_ids": []})
        current["text_parts"].append(snippet)
        current["chunk_ids"].append(chunk["id"])
    for payload in selected.values():
        payload["text"] = "\n".join(payload.pop("text_parts"))
        payload["normalized_text"] = _normalize_for_match(payload["text"])
    return selected


def _recall(expected: list[str], actual: list[str]) -> float:
    if not expected:
        return 1.0
    actual_set = set(actual)
    return len([path for path in expected if path in actual_set]) / len(expected)


def _evaluate_observations(
    case: dict[str, Any],
    report: dict[str, Any],
) -> tuple[list[str], list[str], dict[str, list[str]]]:
    # Score the exact rendered chunk snapshots that were handed off. Reading
    # source files again would allow post-pack edits or unselected lines to
    # inflate the apparent evidence coverage.
    selected_texts: dict[str, dict[str, Any]] = {}
    for chunk in report["selected_chunks"]:
        payload = selected_texts.setdefault(
            chunk["path"], {"text_parts": [], "chunk_ids": []}
        )
        payload["text_parts"].append(str(chunk.get("text", "")))
        payload["chunk_ids"].append(chunk["id"])
    for payload in selected_texts.values():
        payload["text"] = "\n".join(payload.pop("text_parts"))
        payload["normalized_text"] = _normalize_for_match(payload["text"])
    observed: list[str] = []
    missing: list[str] = []
    citations: dict[str, list[str]] = {}

    for observation in case.get("required_observations", []):
        label = str(observation["label"])
        matched_paths = [
            path
            for path, payload in selected_texts.items()
            if any(
                all(_normalize_for_match(term) in payload["normalized_text"] for term in pattern)
                for pattern in observation.get("any_of", [])
            )
        ]
        if matched_paths:
            observed.append(label)
            citations[label] = matched_paths
        else:
            missing.append(label)

    return observed, missing, citations


def _context_plan_readiness(
    *,
    selected_edit_recall: float,
    selected_test_recall: float,
    selected_config_recall: float,
    observation_recall: float,
    expected_test_targets: list[str],
    expected_config_targets: list[str],
    missing_signals: list[str],
    guard_ready: bool,
    sufficiency_status: str,
    handoff_ready: bool,
) -> bool:
    has_test_signal = selected_test_recall == 1.0 or (
        not expected_test_targets
        and any("test target" in signal.lower() for signal in missing_signals)
    )
    has_config_signal = selected_config_recall == 1.0 or not expected_config_targets
    return (
        selected_edit_recall == 1.0
        and has_test_signal
        and has_config_signal
        and guard_ready
        and observation_recall >= 0.8
        and sufficiency_status == "sufficient"
        and handoff_ready
    )


def _run_case(case: dict[str, Any], repos_root: Path, counter: TokenCounter) -> dict[str, Any]:
    repo_name = str(case["repository"])
    repo_path = repos_root / repo_name
    if not repo_path.is_dir():
        raise FileNotFoundError(f"Required benchmark repository is missing: {repo_path}")

    started = time.perf_counter()
    _, report = build_context_package(
        repo_path,
        str(case["task"]),
        int(case.get("budget", 6000)),
        counter,
        top_k=int(case.get("top_k", 28)),
        workflow="feature",
    )
    pack_runtime_ms = round((time.perf_counter() - started) * 1000, 2)

    change_surface = report.get("change_surface") or {}
    selected_paths = _selected_paths(report)
    surface_edit_targets = _paths_from_surface(change_surface, "edit_targets")
    surface_test_targets = _paths_from_surface(change_surface, "test_targets")
    surface_config_targets = _paths_from_surface(change_surface, "config_targets")
    surface_supporting_targets = _paths_from_surface(change_surface, "supporting_targets")
    missing_signals = [str(item) for item in change_surface.get("missing_signals", [])]

    expected_edit_targets = list(case.get("expected_edit_targets", []))
    expected_test_targets = list(case.get("expected_test_targets", []))
    expected_config_targets = list(case.get("expected_config_targets", []))
    observed_observations, missing_observations, observation_citations = _evaluate_observations(
        case, report
    )
    observation_count = len(case.get("required_observations", []))

    edit_recall = _recall(expected_edit_targets, surface_edit_targets)
    test_recall = _recall(expected_test_targets, surface_test_targets)
    config_recall = _recall(expected_config_targets, surface_config_targets)
    selected_edit_recall = _recall(expected_edit_targets, selected_paths)
    selected_test_recall = _recall(expected_test_targets, selected_paths)
    selected_config_recall = _recall(expected_config_targets, selected_paths)
    observation_recall = (
        len(observed_observations) / observation_count if observation_count else 1.0
    )
    context_plan_readiness = _context_plan_readiness(
        selected_edit_recall=selected_edit_recall,
        selected_test_recall=selected_test_recall,
        selected_config_recall=selected_config_recall,
        observation_recall=observation_recall,
        expected_test_targets=expected_test_targets,
        expected_config_targets=expected_config_targets,
        missing_signals=missing_signals,
        guard_ready=bool(report.get("validation_requirements", {}).get("ready", True)),
        sufficiency_status=str(
            (report.get("context_sufficiency") or {}).get("status", "sufficient")
        ),
        handoff_ready=bool(report.get("handoff_ready", True)),
    )

    return {
        "case_id": case["case_id"],
        "repository": repo_name,
        "task": case["task"],
        "budget": int(case.get("budget", 6000)),
        "top_k": int(case.get("top_k", 28)),
        "expected_edit_targets": expected_edit_targets,
        "expected_test_targets": expected_test_targets,
        "expected_config_targets": expected_config_targets,
        "surface_edit_targets": surface_edit_targets,
        "surface_test_targets": surface_test_targets,
        "surface_config_targets": surface_config_targets,
        "surface_supporting_targets": surface_supporting_targets,
        "selected_unique_paths": selected_paths,
        "selected_chunk_count": len(report["selected_chunks"]),
        "selected_context_tokens": report["selected_context_tokens"],
        "candidate_context_tokens": report["candidate_context_tokens"],
        "prompt_token_accounting": report["prompt_token_accounting"],
        "original_user_prompt_tokens": report["prompt_token_accounting"][
            "original_user_prompt_tokens"
        ],
        "full_scan_prompt_tokens": report["prompt_token_accounting"][
            "full_scan_prompt_tokens"
        ],
        "packed_prompt_tokens": report["prompt_token_accounting"]["packed_prompt_tokens"],
        "prompt_tokens_saved_vs_full_scan": report["prompt_token_accounting"][
            "prompt_tokens_saved_vs_full_scan"
        ],
        "pack_runtime_ms": pack_runtime_ms,
        "token_reduction_percent": report["local_reduction_percent_vs_candidate_context"],
        "edit_target_recall": round(edit_recall, 4),
        "test_target_recall": round(test_recall, 4),
        "config_target_recall": round(config_recall, 4),
        "selected_edit_target_recall": round(selected_edit_recall, 4),
        "selected_test_target_recall": round(selected_test_recall, 4),
        "selected_config_target_recall": round(selected_config_recall, 4),
        "required_observation_recall": round(observation_recall, 4),
        "observed_observations": observed_observations,
        "missing_required_observations": missing_observations,
        "observation_citations": observation_citations,
        "missing_signals": missing_signals,
        "has_missing_signal_warning": bool(missing_signals),
        "context_sufficiency": report.get("context_sufficiency"),
        "handoff_ready": report.get("handoff_ready"),
        "validation_requirements": report.get("validation_requirements"),
        "context_plan_readiness": context_plan_readiness,
    }


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _summarize(cases: list[dict[str, Any]], acceptance: dict[str, Any]) -> dict[str, Any]:
    token_reductions = [case["token_reduction_percent"] for case in cases]
    prompt_tokens_saved = [case["prompt_tokens_saved_vs_full_scan"] for case in cases]
    packed_prompt_tokens = [case["packed_prompt_tokens"] for case in cases]
    full_scan_prompt_tokens = [case["full_scan_prompt_tokens"] for case in cases]
    pack_runtimes = [case["pack_runtime_ms"] for case in cases]
    summary = {
        "case_count": len(cases),
        "repositories": sorted({case["repository"] for case in cases}),
        "edit_target_recall": round(_mean([case["edit_target_recall"] for case in cases]), 4),
        "test_target_recall": round(_mean([case["test_target_recall"] for case in cases]), 4),
        "selected_edit_target_recall": round(
            _mean([case["selected_edit_target_recall"] for case in cases]), 4
        ),
        "selected_test_target_recall": round(
            _mean([case["selected_test_target_recall"] for case in cases]), 4
        ),
        "required_observation_recall": round(
            _mean([case["required_observation_recall"] for case in cases]), 4
        ),
        "context_plan_readiness_rate": round(
            _mean([1.0 if case["context_plan_readiness"] else 0.0 for case in cases]), 4
        ),
        "median_token_reduction_percent": round(
            statistics.median(token_reductions) if token_reductions else 0.0, 2
        ),
        "measurement_method": "additive_independently_tokenized_blocks",
        "median_full_scan_prompt_tokens": round(
            statistics.median(full_scan_prompt_tokens) if full_scan_prompt_tokens else 0.0,
            2,
        ),
        "median_packed_prompt_tokens": round(
            statistics.median(packed_prompt_tokens) if packed_prompt_tokens else 0.0,
            2,
        ),
        "median_prompt_tokens_saved_vs_full_scan": round(
            statistics.median(prompt_tokens_saved) if prompt_tokens_saved else 0.0,
            2,
        ),
        "total_prompt_tokens_saved_vs_full_scan": sum(prompt_tokens_saved),
        "median_tokens_omitted_vs_additive_eligible_corpus": round(
            statistics.median(prompt_tokens_saved) if prompt_tokens_saved else 0.0,
            2,
        ),
        "total_tokens_omitted_vs_additive_eligible_corpus": sum(prompt_tokens_saved),
        "mean_selected_chunk_count": round(
            _mean([case["selected_chunk_count"] for case in cases]), 2
        ),
        "median_pack_runtime_ms": round(
            statistics.median(pack_runtimes) if pack_runtimes else 0.0,
            2,
        ),
        "mean_pack_runtime_ms": round(_mean(pack_runtimes), 2),
        "total_pack_runtime_ms": round(sum(pack_runtimes), 2),
        "missing_signal_warning_rate": round(
            _mean([1.0 if case["has_missing_signal_warning"] else 0.0 for case in cases]), 4
        ),
    }
    summary["acceptance"] = {
        "minimums": acceptance,
        "passes": {
            "edit_target_recall": summary["edit_target_recall"]
            >= float(acceptance["minimum_edit_target_recall"]),
            "test_target_recall": summary["test_target_recall"]
            >= float(acceptance["minimum_test_target_recall"]),
            "required_observation_recall": summary["required_observation_recall"]
            >= float(acceptance["minimum_required_observation_recall"]),
            "context_plan_readiness_rate": summary["context_plan_readiness_rate"]
            >= float(acceptance["minimum_context_plan_readiness_rate"]),
            "median_token_reduction_percent": summary["median_token_reduction_percent"]
            >= float(acceptance["minimum_median_token_reduction_percent"]),
        },
    }
    summary["acceptance"]["overall_pass"] = all(summary["acceptance"]["passes"].values())
    return summary


def _render_markdown(summary: dict[str, Any], cases: list[dict[str, Any]]) -> str:
    lines = [
        "# Feature-Work Context Validation",
        "",
        "This report validates whether `agenvantage pack --preset feature` exposes enough local repository context to start feature implementation on the first request.",
        "",
        "## Summary",
        "",
        f"- Cases: `{summary['case_count']}`",
        f"- Repositories: `{', '.join(summary['repositories'])}`",
        f"- Edit-target recall: `{summary['edit_target_recall']}`",
        f"- Test-target recall: `{summary['test_target_recall']}`",
        f"- Required-observation recall: `{summary['required_observation_recall']}`",
        f"- Context-plan readiness rate: `{summary['context_plan_readiness_rate']}`",
        f"- Median token reduction: `{summary['median_token_reduction_percent']}%`",
        f"- Median full-scan prompt: `{summary['median_full_scan_prompt_tokens']}` tokens",
        f"- Median packed prompt: `{summary['median_packed_prompt_tokens']}` tokens",
        f"- Median prompt tokens saved: `{summary['median_prompt_tokens_saved_vs_full_scan']}`",
        f"- Total prompt tokens saved: `{summary['total_prompt_tokens_saved_vs_full_scan']}`",
        f"- Mean selected chunks: `{summary['mean_selected_chunk_count']}`",
        f"- Median pack runtime: `{summary['median_pack_runtime_ms']}` ms",
        f"- Total pack runtime: `{summary['total_pack_runtime_ms']}` ms",
        f"- Missing-signal warning rate: `{summary['missing_signal_warning_rate']}`",
        f"- Acceptance pass: `{summary['acceptance']['overall_pass']}`",
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
                f"- Edit-target recall: `{case['edit_target_recall']}`",
                f"- Test-target recall: `{case['test_target_recall']}`",
                f"- Observation recall: `{case['required_observation_recall']}`",
                f"- Context-plan readiness: `{case['context_plan_readiness']}`",
                f"- Token reduction: `{case['token_reduction_percent']}%`",
                f"- Prompt tokens: user `{case['original_user_prompt_tokens']}`, full-scan `{case['full_scan_prompt_tokens']}`, packed `{case['packed_prompt_tokens']}`",
                f"- Prompt tokens saved: `{case['prompt_tokens_saved_vs_full_scan']}`",
                f"- Pack runtime: `{case['pack_runtime_ms']}` ms",
                f"- Surface edit targets: `{case['surface_edit_targets']}`",
                f"- Surface test targets: `{case['surface_test_targets']}`",
                f"- Selected files: `{case['selected_unique_paths'][:8]}`",
                f"- Missing observations: `{case['missing_required_observations']}`",
                f"- Missing signals: `{case['missing_signals']}`",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def run_feature_work_validation(
    fixture: Path = DEFAULT_FIXTURE,
    repos_root: Path = DEFAULT_REPOS_ROOT,
    output_json: Path | None = DEFAULT_OUTPUT_JSON,
    output_md: Path | None = DEFAULT_OUTPUT_MD,
) -> dict[str, Any]:
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    counter = TokenCounter()
    cases = [_run_case(case, repos_root, counter) for case in payload["cases"]]
    summary = _summarize(cases, payload["acceptance"])
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
        description="Validate feature-work context packing against annotated local repo tasks."
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

    report = run_feature_work_validation(
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
