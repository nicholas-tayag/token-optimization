from __future__ import annotations

import argparse
import json
import re
import statistics
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
    selected_texts = _selected_file_texts(report)
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


def _answer_plan_passes(
    *,
    edit_recall: float,
    test_recall: float,
    observation_recall: float,
    expected_test_targets: list[str],
    missing_signals: list[str],
) -> bool:
    has_test_signal = test_recall == 1.0 or (
        not expected_test_targets
        and any("test target" in signal.lower() for signal in missing_signals)
    )
    return edit_recall == 1.0 and has_test_signal and observation_recall >= 0.8


def _run_case(case: dict[str, Any], repos_root: Path, counter: TokenCounter) -> dict[str, Any]:
    repo_name = str(case["repository"])
    repo_path = repos_root / repo_name
    if not repo_path.is_dir():
        raise FileNotFoundError(f"Required benchmark repository is missing: {repo_path}")

    _, report = build_context_package(
        repo_path,
        str(case["task"]),
        int(case.get("budget", 6000)),
        counter,
        top_k=int(case.get("top_k", 28)),
        workflow="feature",
    )

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
    observation_recall = (
        len(observed_observations) / observation_count if observation_count else 1.0
    )
    answer_plan_pass = _answer_plan_passes(
        edit_recall=edit_recall,
        test_recall=test_recall,
        observation_recall=observation_recall,
        expected_test_targets=expected_test_targets,
        missing_signals=missing_signals,
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
        "token_reduction_percent": report["local_reduction_percent_vs_candidate_context"],
        "edit_target_recall": round(edit_recall, 4),
        "test_target_recall": round(test_recall, 4),
        "config_target_recall": round(config_recall, 4),
        "selected_edit_target_recall": round(selected_edit_recall, 4),
        "selected_test_target_recall": round(selected_test_recall, 4),
        "required_observation_recall": round(observation_recall, 4),
        "observed_observations": observed_observations,
        "missing_required_observations": missing_observations,
        "observation_citations": observation_citations,
        "missing_signals": missing_signals,
        "has_missing_signal_warning": bool(missing_signals),
        "answer_plan_pass": answer_plan_pass,
    }


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _summarize(cases: list[dict[str, Any]], acceptance: dict[str, Any]) -> dict[str, Any]:
    token_reductions = [case["token_reduction_percent"] for case in cases]
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
        "answer_plan_pass_rate": round(
            _mean([1.0 if case["answer_plan_pass"] else 0.0 for case in cases]), 4
        ),
        "median_token_reduction_percent": round(
            statistics.median(token_reductions) if token_reductions else 0.0, 2
        ),
        "mean_selected_chunk_count": round(
            _mean([case["selected_chunk_count"] for case in cases]), 2
        ),
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
            "answer_plan_pass_rate": summary["answer_plan_pass_rate"]
            >= float(acceptance["minimum_answer_plan_pass_rate"]),
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
        f"- Answer-plan pass rate: `{summary['answer_plan_pass_rate']}`",
        f"- Median token reduction: `{summary['median_token_reduction_percent']}%`",
        f"- Mean selected chunks: `{summary['mean_selected_chunk_count']}`",
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
                f"- Answer-plan pass: `{case['answer_plan_pass']}`",
                f"- Token reduction: `{case['token_reduction_percent']}%`",
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
