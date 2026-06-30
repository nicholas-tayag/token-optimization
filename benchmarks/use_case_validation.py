from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from agenvantage.experiment import load_scenario, run_experiment
from agenvantage.repo_context import build_context_package, build_multi_repo_context_package
from agenvantage.tokenizer import TokenCounter


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_JSON = REPO_ROOT / "artifacts" / "use-case-validation.json"
DEFAULT_OUTPUT_MD = REPO_ROOT / "artifacts" / "use-case-validation.md"


@dataclass(frozen=True)
class ValidationCase:
    case_id: str
    category: str
    task: str
    repos: tuple[Path, ...]
    budget: int
    expected_paths: tuple[str, ...]
    description: str
    requires_change_provenance: bool = False
    top_k: int = 20


def _ordered_unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def _repo_labels(repos: tuple[Path, ...]) -> list[str]:
    seen: dict[str, int] = {}
    labels: list[str] = []
    for repo in repos:
        label = repo.name or "repo"
        seen[label] = seen.get(label, 0) + 1
        labels.append(label if seen[label] == 1 else f"{label}-{seen[label]}")
    return labels


def _first_hit_rank(selected_paths: list[str], expected_paths: tuple[str, ...]) -> int | None:
    expected = set(expected_paths)
    for index, path in enumerate(selected_paths, start=1):
        if path in expected:
            return index
    return None


def _verdict(
    expected_path_recall: float, repo_recall: float, requires_change_provenance: bool
) -> str:
    if requires_change_provenance:
        return "partial"
    if expected_path_recall == 1.0 and repo_recall == 1.0:
        return "pass"
    if expected_path_recall >= 0.5 and repo_recall == 1.0:
        return "partial"
    return "fail"


def _build_report(
    case: ValidationCase, counter: TokenCounter, budget: int, top_k: int
) -> dict[str, Any]:
    if len(case.repos) == 1:
        _, report = build_context_package(
            case.repos[0], case.task, budget, counter, top_k=top_k
        )
    else:
        _, report = build_multi_repo_context_package(
            case.repos, case.task, budget, counter, top_k=top_k
        )
    return report


def _report_selected_paths(report: dict[str, Any]) -> list[str]:
    return _ordered_unique([chunk["path"] for chunk in report["selected_chunks"]])


def _report_selected_repos(report: dict[str, Any]) -> list[str]:
    return sorted(report.get("selected_repo_labels", []))


def _meets_grounding(case: ValidationCase, report: dict[str, Any]) -> bool:
    selected_paths = set(_report_selected_paths(report))
    selected_repos = set(_report_selected_repos(report))
    expected_repos = set(_repo_labels(case.repos))
    return all(path in selected_paths for path in case.expected_paths) and expected_repos <= selected_repos


def _find_minimum_budget_for_grounding(
    case: ValidationCase, counter: TokenCounter
) -> tuple[int | None, dict[str, Any] | None]:
    if case.requires_change_provenance:
        return None, None

    try:
        current_report = _build_report(case, counter, case.budget, case.top_k)
    except ValueError:
        return None, None

    if not _meets_grounding(case, current_report):
        return None, None

    low = 1
    high = case.budget
    best_budget = case.budget
    best_report = current_report

    while low <= high:
        mid = (low + high) // 2
        try:
            report = _build_report(case, counter, mid, case.top_k)
        except ValueError:
            low = mid + 1
            continue
        if _meets_grounding(case, report):
            best_budget = mid
            best_report = report
            high = mid - 1
        else:
            low = mid + 1

    return best_budget, best_report


def _find_top_k_for_grounding(case: ValidationCase, counter: TokenCounter) -> int | None:
    if case.requires_change_provenance:
        return None
    for candidate_top_k in (case.top_k, 30, 40, 60, 80, 120):
        try:
            report = _build_report(case, counter, case.budget, candidate_top_k)
        except ValueError:
            continue
        if _meets_grounding(case, report):
            return candidate_top_k
    return None


def _run_case(case: ValidationCase, counter: TokenCounter) -> dict[str, Any]:
    report = _build_report(case, counter, case.budget, case.top_k)

    selected_chunks = report["selected_chunks"]
    selected_paths = _report_selected_paths(report)
    selected_repos = _report_selected_repos(report)
    expected_repo_labels = sorted(_repo_labels(case.repos))
    path_hits = [path for path in case.expected_paths if path in selected_paths]
    repo_hits = [label for label in expected_repo_labels if label in selected_repos]
    expected_path_recall = round(
        (len(path_hits) / len(case.expected_paths)) if case.expected_paths else 1.0, 2
    )
    repo_recall = round(
        (len(repo_hits) / len(expected_repo_labels)) if expected_repo_labels else 1.0, 2
    )
    verdict = _verdict(
        expected_path_recall, repo_recall, case.requires_change_provenance
    )
    minimum_budget_for_grounding, minimum_budget_report = _find_minimum_budget_for_grounding(
        case, counter
    )
    minimum_top_k_for_grounding = _find_top_k_for_grounding(case, counter)
    grounding_sufficient_for_context = (
        expected_path_recall == 1.0
        and repo_recall == 1.0
        and not case.requires_change_provenance
    )

    return {
        "case_id": case.case_id,
        "category": case.category,
        "description": case.description,
        "task": case.task,
        "budget": case.budget,
        "top_k": case.top_k,
        "repos": [str(repo) for repo in case.repos],
        "expected_repo_labels": expected_repo_labels,
        "expected_paths": list(case.expected_paths),
        "candidate_context_tokens": report["candidate_context_tokens"],
        "selected_context_tokens": report["selected_context_tokens"],
        "local_tokens_omitted_vs_candidate_context": report[
            "local_tokens_omitted_vs_candidate_context"
        ],
        "local_reduction_percent_vs_candidate_context": report[
            "local_reduction_percent_vs_candidate_context"
        ],
        "selected_repo_labels": selected_repos,
        "selected_unique_paths": selected_paths,
        "selected_chunk_count": len(selected_chunks),
        "selected_unique_file_count": len(selected_paths),
        "path_hits": path_hits,
        "repo_hits": repo_hits,
        "expected_path_recall": expected_path_recall,
        "repo_recall": repo_recall,
        "grounding_file_density": round(
            (len(path_hits) / len(selected_paths)) if selected_paths else 0.0, 2
        ),
        "first_expected_path_rank": _first_hit_rank(selected_paths, case.expected_paths),
        "grounding_sufficient_for_context": grounding_sufficient_for_context,
        "minimum_budget_for_grounding": minimum_budget_for_grounding,
        "minimum_selected_tokens_for_grounding": minimum_budget_report["selected_context_tokens"]
        if minimum_budget_report
        else None,
        "grounding_budget_headroom_tokens": (
            case.budget - minimum_budget_for_grounding
            if minimum_budget_for_grounding is not None
            else None
        ),
        "minimum_top_k_for_grounding_at_budget": minimum_top_k_for_grounding,
        "requires_change_provenance": case.requires_change_provenance,
        "supports_change_provenance": False,
        "verdict": verdict,
    }


def _run_synthetic_experiment(counter: TokenCounter) -> dict[str, Any]:
    scenario = load_scenario(REPO_ROOT / "examples" / "synthetic_oncall_context.json")
    report = run_experiment(scenario, counter, budget=360)
    policies = {row["policy"]: row for row in report["policies"]}
    full = policies["full"]
    cache_aligned = policies["cache_aligned"]
    budgeted = policies["budgeted"]
    return {
        "scenario_id": report["scenario_id"],
        "full_input_tokens": full["input_tokens"],
        "cache_aligned_input_tokens": cache_aligned["input_tokens"],
        "cache_aligned_stable_prefix_gain": cache_aligned["stable_prefix_tokens"]
        - full["stable_prefix_tokens"],
        "budgeted_input_tokens": budgeted["input_tokens"],
        "budgeted_tokens_saved_vs_full": budgeted["tokens_saved_vs_full"],
        "budgeted_reduction_percent_vs_full": budgeted[
            "token_reduction_percent_vs_full"
        ],
        "cache_alignment_works": cache_aligned["stable_prefix_tokens"]
        > full["stable_prefix_tokens"],
        "budgeting_works": budgeted["input_tokens"] < full["input_tokens"],
    }


def _build_cases() -> list[ValidationCase]:
    mesh = Path("/Users/nicky/GithubRepos/mesh")
    signalfoundry = Path("/Users/nicky/GithubRepos/signalfoundry")
    application_tracker = Path("/Users/nicky/GithubRepos/application-tracker")
    return [
        ValidationCase(
            case_id="locate-mesh-extraction-flow",
            category="locate_implementation",
            description="Find the implementation paths for transcript extraction and live logging in Mesh.",
            task="Explain Mesh transcript extraction, Gemini fallback behavior, and live logging endpoints.",
            repos=(mesh,),
            budget=3200,
            expected_paths=("lib/extraction.js", "scripts/app.js", "server.js"),
        ),
        ValidationCase(
            case_id="explain-signalfoundry-upload-smoke-test",
            category="explain_known_test_case",
            description="Ground an explanation of SignalFoundry upload handling in the implementation and smoke test.",
            task="Explain SignalFoundry local upload handling, FFmpeg cleaning, and the server smoke test coverage.",
            repos=(signalfoundry,),
            budget=3600,
            expected_paths=("tools/server-smoke-test.js", "tools/serve.js", "app.js"),
        ),
        ValidationCase(
            case_id="validate-application-tracker-private-storage",
            category="locate_implementation",
            description="Find the server and storage code that back private tracker persistence and static path protection.",
            task="Explain how Application Tracker stores private tracker data and how the server-security test verifies static path protection.",
            repos=(application_tracker,),
            budget=3200,
            expected_paths=(
                "tests/server-security.test.mjs",
                "server.mjs",
                "lib/tracker-store.mjs",
            ),
        ),
        ValidationCase(
            case_id="recommend-signalfoundry-edge-tests",
            category="recommend_edge_case_tests",
            description="Gather the files needed to recommend upload edge-case tests in SignalFoundry.",
            task="Recommend edge-case tests for SignalFoundry upload handling, empty bodies, oversize payloads, and local cleaning/export states.",
            repos=(signalfoundry,),
            budget=3200,
            expected_paths=("tools/server-smoke-test.js", "tools/serve.js", "app.js"),
        ),
        ValidationCase(
            case_id="compare-cross-repo-server-hardening",
            category="compare_multiple_repositories",
            description="Compare server hardening and data protection across the three app repos.",
            task="Compare the local server hardening and private data protections across Mesh, SignalFoundry, and Application Tracker. Cover hidden static asset blocking, health endpoints, upload or persistence API protections, and the relevant smoke or security tests.",
            repos=(mesh, signalfoundry, application_tracker),
            budget=5000,
            expected_paths=(
                "mesh/scripts/server-smoke-test.js",
                "signalfoundry/tools/server-smoke-test.js",
                "application-tracker/tests/server-security.test.mjs",
                "application-tracker/server.mjs",
            ),
        ),
        ValidationCase(
            case_id="compare-cross-repo-input-flows",
            category="compare_multiple_repositories",
            description="Compare the main user-input processing flows across the three app repos.",
            task="Compare Mesh transcript extraction and live logging, SignalFoundry clip upload cleaning with FFmpeg preview/export states, and Application Tracker resume upload plus autofill planning.",
            repos=(mesh, signalfoundry, application_tracker),
            budget=5000,
            expected_paths=(
                "mesh/scripts/app.js",
                "signalfoundry/app.js",
                "application-tracker/lib/form-autofill.mjs",
                "application-tracker/lib/resume-agent.mjs",
            ),
        ),
        ValidationCase(
            case_id="changed-behavior-signalfoundry-upload-limit",
            category="identify_changed_behavior",
            description="Test whether the repository can support changed-behavior analysis for the upload limit change.",
            task="Identify the changed behavior around configurable upload size limits and show what code and tests prove it.",
            repos=(signalfoundry,),
            budget=3200,
            expected_paths=("README.md", "tools/serve.js", "tools/server-smoke-test.js"),
            requires_change_provenance=True,
        ),
    ]


def _summarize(cases: list[dict[str, Any]], synthetic: dict[str, Any]) -> dict[str, Any]:
    reductions = [case["local_reduction_percent_vs_candidate_context"] for case in cases]
    total_candidate = sum(case["candidate_context_tokens"] for case in cases)
    total_selected = sum(case["selected_context_tokens"] for case in cases)
    total_expected = sum(len(case["expected_paths"]) for case in cases)
    total_hits = sum(len(case["path_hits"]) for case in cases)
    sufficient_cases = sum(1 for case in cases if case["grounding_sufficient_for_context"])
    verdict_counts: dict[str, int] = {}
    for case in cases:
        verdict_counts[case["verdict"]] = verdict_counts.get(case["verdict"], 0) + 1

    return {
        "case_count": len(cases),
        "verdict_counts": verdict_counts,
        "average_reduction_percent": round(statistics.mean(reductions), 2),
        "median_reduction_percent": round(statistics.median(reductions), 2),
        "weighted_reduction_percent": round(
            ((total_candidate - total_selected) / total_candidate * 100)
            if total_candidate
            else 0.0,
            2,
        ),
        "average_expected_path_recall": round(
            statistics.mean(case["expected_path_recall"] for case in cases), 2
        ),
        "weighted_expected_path_recall": round(
            (total_hits / total_expected) if total_expected else 1.0,
            2,
        ),
        "grounding_sufficiency_pass_rate": round(sufficient_cases / len(cases), 2),
        "synthetic_experiment": synthetic,
    }


def _render_markdown(summary: dict[str, Any], cases: list[dict[str, Any]]) -> str:
    lines = [
        "# AgenVantage Use-Case Validation",
        "",
        "## What The Repository Actually Does",
        "",
        "- Builds token-budgeted Markdown context packages and JSON manifests from local repositories.",
        "- Scans tracked plus untracked, non-ignored worktree files and excludes dependency directories and `.env` content.",
        "- Ranks line-addressable chunks with deterministic task-term matching and a light diversity penalty.",
        "- Supports single-repository and multi-repository packaging.",
        "- Runs a separate synthetic context-policy experiment (`full`, `cache_aligned`, `budgeted`).",
        "",
        "## What It Does Not Yet Do",
        "",
        "- It does not call a model, grade answer quality, or measure real provider cost or latency.",
        "- It does not include git diff or commit-log context in `pack`, so true changed-behavior provenance is not solved yet.",
        "- It does not use embeddings, BM25, or semantic reranking; relevance is deterministic term matching.",
        "",
        "## Overall Result",
        "",
        f"- Cases run: `{summary['case_count']}`",
        f"- Verdicts: `{summary['verdict_counts']}`",
        f"- Average candidate-context reduction: `{summary['average_reduction_percent']}%`",
        f"- Weighted candidate-context reduction: `{summary['weighted_reduction_percent']}%`",
        f"- Average expected-file recall: `{summary['average_expected_path_recall']}`",
        f"- Weighted expected-file recall: `{summary['weighted_expected_path_recall']}`",
        f"- Grounding sufficiency pass rate: `{summary['grounding_sufficiency_pass_rate']}`",
        "",
        "AgenVantage solves the narrow pre-inference context-packaging problem for most explanation, review, and cross-repo comparison tasks tested here. It does not yet solve real API-token savings, latency reduction, answer-quality retention, or changed-behavior provenance end-to-end.",
        "",
        "## Synthetic Experiment Validation",
        "",
        f"- Scenario: `{summary['synthetic_experiment']['scenario_id']}`",
        f"- Cache alignment increased stable-prefix tokens by `{summary['synthetic_experiment']['cache_aligned_stable_prefix_gain']}` while leaving total input tokens unchanged.",
        f"- Budgeting reduced the synthetic fixture from `{summary['synthetic_experiment']['full_input_tokens']}` to `{summary['synthetic_experiment']['budgeted_input_tokens']}` tokens, saving `{summary['synthetic_experiment']['budgeted_tokens_saved_vs_full']}` tokens (`{summary['synthetic_experiment']['budgeted_reduction_percent_vs_full']}%`).",
        "",
        "## Case Results",
        "",
        "| Case | Category | Verdict | Reduction | File Recall | Min Budget | Min Top-K | First Hit |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for case in cases:
        lines.append(
            f"| `{case['case_id']}` | `{case['category']}` | `{case['verdict']}` | `{case['local_reduction_percent_vs_candidate_context']}%` | `{case['expected_path_recall']}` | `{case['minimum_budget_for_grounding'] or '-'}` | `{case['minimum_top_k_for_grounding_at_budget'] or '-'}` | `{case['first_expected_path_rank'] or '-'}` |"
        )

    lines.extend(["", "## Detailed Notes", ""])
    for case in cases:
        lines.extend(
            [
                f"### `{case['case_id']}`",
                "",
                f"- Description: {case['description']}",
                f"- Candidate -> selected tokens: `{case['candidate_context_tokens']}` -> `{case['selected_context_tokens']}`",
                f"- Expected file hits: `{case['path_hits']}`",
                f"- Selected repos: `{case['selected_repo_labels']}`",
                f"- Grounding sufficient at tested budget: `{case['grounding_sufficient_for_context']}`",
                f"- Minimum budget for grounding: `{case['minimum_budget_for_grounding']}`",
                f"- Grounding budget headroom: `{case['grounding_budget_headroom_tokens']}`",
                f"- Minimum top-k for grounding at tested budget: `{case['minimum_top_k_for_grounding_at_budget']}`",
                f"- Grounding file density: `{case['grounding_file_density']}`",
                f"- Top selected files: `{case['selected_unique_paths'][:6]}`",
            ]
        )
        if case["requires_change_provenance"]:
            lines.append(
                "- Limitation: this use case needs git-history or diff context, and `pack` currently only packages repository file content."
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate AgenVantage against real local repository use cases."
    )
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_OUTPUT_MD)
    args = parser.parse_args()

    counter = TokenCounter()
    cases = [_run_case(case, counter) for case in _build_cases()]
    synthetic = _run_synthetic_experiment(counter)
    summary = _summarize(cases, synthetic)
    report = {"summary": summary, "cases": cases}

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    args.output_md.write_text(_render_markdown(summary, cases), encoding="utf-8")

    print(json.dumps(report, indent=2))
    print(f"\nJSON report written to {args.output_json.resolve()}")
    print(f"Markdown report written to {args.output_md.resolve()}")


if __name__ == "__main__":
    main()
