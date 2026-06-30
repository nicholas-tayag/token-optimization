from __future__ import annotations

import argparse
import json
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agenvantage.experiment import load_scenario, run_experiment
from agenvantage.repo_context import build_context_package, build_multi_repo_context_package
from agenvantage.tokenizer import TokenCounter


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_JSON = REPO_ROOT / "artifacts" / "use-case-validation.json"
DEFAULT_OUTPUT_MD = REPO_ROOT / "artifacts" / "use-case-validation.md"


@dataclass(frozen=True)
class RequiredObservation:
    label: str
    any_of: tuple[tuple[str, ...], ...]


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
    required_observations: tuple[RequiredObservation, ...] = ()


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
    expected_path_recall: float,
    repo_recall: float,
    required_observation_recall: float,
    requires_change_provenance: bool,
    supports_change_provenance: bool,
) -> str:
    if requires_change_provenance:
        if (
            expected_path_recall == 1.0
            and repo_recall == 1.0
            and required_observation_recall == 1.0
            and supports_change_provenance
        ):
            return "pass"
        if (
            expected_path_recall >= 0.5
            and repo_recall == 1.0
            and required_observation_recall >= 0.5
        ):
            return "partial"
        return "fail"
    if expected_path_recall == 1.0 and repo_recall == 1.0 and required_observation_recall == 1.0:
        return "pass"
    if expected_path_recall >= 0.5 and repo_recall == 1.0 and required_observation_recall >= 0.5:
        return "partial"
    return "fail"


def _build_report(
    case: ValidationCase, counter: TokenCounter, budget: int, top_k: int
) -> dict[str, Any]:
    if len(case.repos) == 1:
        _, report = build_context_package(
            case.repos[0],
            case.task,
            budget,
            counter,
            top_k=top_k,
            include_diff=case.requires_change_provenance,
            include_log=case.requires_change_provenance,
        )
    else:
        _, report = build_multi_repo_context_package(
            case.repos,
            case.task,
            budget,
            counter,
            top_k=top_k,
            include_diff=case.requires_change_provenance,
            include_log=case.requires_change_provenance,
        )
    return report


def _report_selected_paths(report: dict[str, Any]) -> list[str]:
    return _ordered_unique([chunk["path"] for chunk in report["selected_chunks"]])


def _report_selected_repos(report: dict[str, Any]) -> list[str]:
    return sorted(report.get("selected_repo_labels", []))


def _normalize_for_match(text: str) -> str:
    split = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text).replace("_", " ")
    return re.sub(r"[^a-z0-9]+", " ", split.lower()).strip()


def _selected_file_texts(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for chunk in report["selected_chunks"]:
        absolute_path = Path(chunk["repo_path"]) / chunk["relative_path"]
        try:
            lines = absolute_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        snippet = "\n".join(lines[chunk["start_line"] - 1 : chunk["end_line"]])
        current = selected.setdefault(
            chunk["path"],
            {
                "path": chunk["path"],
                "repo_label": chunk["repo_label"],
                "text_parts": [],
                "chunk_ids": [],
            },
        )
        current["text_parts"].append(snippet)
        current["chunk_ids"].append(chunk["id"])
    for current in selected.values():
        current["text"] = "\n".join(current.pop("text_parts"))
        current["normalized_text"] = _normalize_for_match(current["text"])
    return selected


def _evaluate_required_observations(
    case: ValidationCase, report: dict[str, Any]
) -> tuple[list[str], list[str], dict[str, list[str]]]:
    selected_file_texts = _selected_file_texts(report)
    observed: list[str] = []
    missing: list[str] = []
    citations: dict[str, list[str]] = {}

    for observation in case.required_observations:
        matched_paths = [
            path
            for path, payload in selected_file_texts.items()
            if any(
                all(_normalize_for_match(term) in payload["normalized_text"] for term in pattern)
                for pattern in observation.any_of
            )
        ]
        if matched_paths:
            observed.append(observation.label)
            citations[observation.label] = matched_paths
        else:
            missing.append(observation.label)

    return observed, missing, citations


def _meets_grounding(case: ValidationCase, report: dict[str, Any]) -> bool:
    selected_paths = set(_report_selected_paths(report))
    selected_repos = set(_report_selected_repos(report))
    expected_repos = set(_repo_labels(case.repos))
    has_expected_grounding = all(path in selected_paths for path in case.expected_paths)
    if not (has_expected_grounding and expected_repos <= selected_repos):
        return False
    if case.requires_change_provenance:
        return bool(report.get("provenance", {}).get("section_count", 0))
    return True


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
    observed_observations, missing_observations, observation_citations = (
        _evaluate_required_observations(case, report)
    )
    required_observation_recall = round(
        (
            len(observed_observations) / len(case.required_observations)
            if case.required_observations
            else 1.0
        ),
        2,
    )
    supports_change_provenance = bool(report.get("provenance", {}).get("section_count", 0))
    verdict = _verdict(
        expected_path_recall,
        repo_recall,
        required_observation_recall,
        case.requires_change_provenance,
        supports_change_provenance,
    )
    minimum_budget_for_grounding, minimum_budget_report = _find_minimum_budget_for_grounding(
        case, counter
    )
    minimum_top_k_for_grounding = _find_top_k_for_grounding(case, counter)
    grounding_sufficient_for_context = (
        expected_path_recall == 1.0
        and repo_recall == 1.0
        and (supports_change_provenance if case.requires_change_provenance else True)
    )
    answer_rubric_sufficient_for_context = (
        grounding_sufficient_for_context and required_observation_recall == 1.0
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
        "required_observation_count": len(case.required_observations),
        "observed_observations": observed_observations,
        "missing_required_observations": missing_observations,
        "required_observation_recall": required_observation_recall,
        "observation_citations": observation_citations,
        "grounding_file_density": round(
            (len(path_hits) / len(selected_paths)) if selected_paths else 0.0, 2
        ),
        "first_expected_path_rank": _first_hit_rank(selected_paths, case.expected_paths),
        "grounding_sufficient_for_context": grounding_sufficient_for_context,
        "answer_rubric_sufficient_for_context": answer_rubric_sufficient_for_context,
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
        "supports_change_provenance": supports_change_provenance,
        "provenance_section_count": report.get("provenance", {}).get("section_count", 0),
        "selected_provenance_tokens": report.get("provenance", {}).get(
            "selected_provenance_tokens", 0
        ),
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
            required_observations=(
                RequiredObservation(
                    label="mesh_gemini_fallback",
                    any_of=(
                        ("gemini api key is not configured", "fallback true"),
                        ("gemini extraction failed", "fallback true"),
                    ),
                ),
                RequiredObservation(
                    label="mesh_live_log_endpoint",
                    any_of=(("api live log", "segments", "speaker"),),
                ),
                RequiredObservation(
                    label="mesh_live_ui_posts_segments",
                    any_of=(("fetch", "api live log", "state live segments"),),
                ),
            ),
        ),
        ValidationCase(
            case_id="explain-signalfoundry-upload-smoke-test",
            category="explain_known_test_case",
            description="Ground an explanation of SignalFoundry upload handling in the implementation and smoke test.",
            task="Explain SignalFoundry local upload handling, FFmpeg cleaning, and the server smoke test coverage.",
            repos=(signalfoundry,),
            budget=3600,
            expected_paths=("tools/server-smoke-test.js", "tools/serve.js", "app.js"),
            required_observations=(
                RequiredObservation(
                    label="signalfoundry_upload_limit_config",
                    any_of=(("max upload bytes", "default max upload bytes"),),
                ),
                RequiredObservation(
                    label="signalfoundry_empty_upload_rejected",
                    any_of=(("upload body is empty",),),
                ),
                RequiredObservation(
                    label="signalfoundry_cleaning_and_export_states",
                    any_of=(
                        ("payload clean", "clean status"),
                        ("cleaned export ready", "cleaning plan"),
                    ),
                ),
            ),
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
            required_observations=(
                RequiredObservation(
                    label="application_tracker_private_directory_defaults",
                    any_of=(("private directory", "tracker state json"),),
                ),
                RequiredObservation(
                    label="application_tracker_atomic_tracker_write",
                    any_of=(("rename temporary this path", "revision"),),
                ),
                RequiredObservation(
                    label="application_tracker_static_and_private_paths_blocked",
                    any_of=(("git response status", "private response status", "api response status"),),
                ),
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
            required_observations=(
                RequiredObservation(
                    label="signalfoundry_empty_upload_smoke_test",
                    any_of=(("empty upload", "upload body is empty"),),
                ),
                RequiredObservation(
                    label="signalfoundry_oversize_upload_smoke_test",
                    any_of=(("max upload bytes", "oversized upload status code", "413"),),
                ),
                RequiredObservation(
                    label="signalfoundry_ui_clean_statuses",
                    any_of=(("clean status", "payload cleaning plan"),),
                ),
            ),
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
            required_observations=(
                RequiredObservation(
                    label="mesh_static_hardening",
                    any_of=(("hidden git", "hidden env", "403"), ("api health", "hidden git", "hidden env")),
                ),
                RequiredObservation(
                    label="signalfoundry_static_hardening",
                    any_of=(("hidden git", "hidden env", "403"), ("health payload", "max upload bytes")),
                ),
                RequiredObservation(
                    label="application_tracker_static_hardening",
                    any_of=(("git response status", "private response status", "api response status"),),
                ),
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
            required_observations=(
                RequiredObservation(
                    label="mesh_live_transcript_flow",
                    any_of=(("api live log", "state live segments"),),
                ),
                RequiredObservation(
                    label="signalfoundry_upload_processing_flow",
                    any_of=(("clean status", "payload clean"), ("server saved upload", "clean status")),
                ),
                RequiredObservation(
                    label="application_tracker_resume_guardrails",
                    any_of=(("may submit false", "review boundary"), ("this plan never submits an application", "review every proposed value")),
                ),
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
            required_observations=(
                RequiredObservation(
                    label="signalfoundry_upload_limit_changed_in_server",
                    any_of=(("max upload bytes", "default max upload bytes"),),
                ),
                RequiredObservation(
                    label="signalfoundry_upload_limit_changed_in_test",
                    any_of=(("max upload bytes", "oversized upload status code", "413"),),
                ),
            ),
        ),
    ]


def _summarize(cases: list[dict[str, Any]], synthetic: dict[str, Any]) -> dict[str, Any]:
    reductions = [case["local_reduction_percent_vs_candidate_context"] for case in cases]
    total_candidate = sum(case["candidate_context_tokens"] for case in cases)
    total_selected = sum(case["selected_context_tokens"] for case in cases)
    total_expected = sum(len(case["expected_paths"]) for case in cases)
    total_hits = sum(len(case["path_hits"]) for case in cases)
    sufficient_cases = sum(1 for case in cases if case["grounding_sufficient_for_context"])
    answer_ready_cases = sum(1 for case in cases if case["answer_rubric_sufficient_for_context"])
    total_required_observations = sum(case["required_observation_count"] for case in cases)
    total_observed_observations = sum(len(case["observed_observations"]) for case in cases)
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
        "average_required_observation_recall": round(
            statistics.mean(case["required_observation_recall"] for case in cases), 2
        ),
        "weighted_required_observation_recall": round(
            (
                total_observed_observations / total_required_observations
                if total_required_observations
                else 1.0
            ),
            2,
        ),
        "answer_rubric_pass_rate": round(answer_ready_cases / len(cases), 2),
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
        "- It does not call a model, grade generated answers from an LLM, or measure real provider cost or latency.",
        "- It can package git diff and recent commit-log provenance, but it does not yet score generated changed-behavior explanations end-to-end.",
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
        f"- Average required-observation recall: `{summary['average_required_observation_recall']}`",
        f"- Weighted required-observation recall: `{summary['weighted_required_observation_recall']}`",
        f"- Answer-rubric pass rate: `{summary['answer_rubric_pass_rate']}`",
        "",
        "AgenVantage solves the narrow pre-inference context-packaging problem for most explanation, review, and cross-repo comparison tasks tested here. The benchmark now also checks whether the selected excerpts contain the required behavioral observations for each task, not just the right file paths. It still does not solve real API-token savings, latency reduction, answer-quality retention, or generated-answer quality end-to-end.",
        "",
        "## Synthetic Experiment Validation",
        "",
        f"- Scenario: `{summary['synthetic_experiment']['scenario_id']}`",
        f"- Cache alignment increased stable-prefix tokens by `{summary['synthetic_experiment']['cache_aligned_stable_prefix_gain']}` while leaving total input tokens unchanged.",
        f"- Budgeting reduced the synthetic fixture from `{summary['synthetic_experiment']['full_input_tokens']}` to `{summary['synthetic_experiment']['budgeted_input_tokens']}` tokens, saving `{summary['synthetic_experiment']['budgeted_tokens_saved_vs_full']}` tokens (`{summary['synthetic_experiment']['budgeted_reduction_percent_vs_full']}%`).",
        "",
        "## Case Results",
        "",
        "| Case | Category | Verdict | Reduction | File Recall | Obs Recall | Min Budget | First Hit |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for case in cases:
        lines.append(
            f"| `{case['case_id']}` | `{case['category']}` | `{case['verdict']}` | `{case['local_reduction_percent_vs_candidate_context']}%` | `{case['expected_path_recall']}` | `{case['required_observation_recall']}` | `{case['minimum_budget_for_grounding'] or '-'}` | `{case['first_expected_path_rank'] or '-'}` |"
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
                f"- Answer-rubric sufficient at tested budget: `{case['answer_rubric_sufficient_for_context']}`",
                f"- Observed rubric items: `{case['observed_observations']}`",
                f"- Missing rubric items: `{case['missing_required_observations']}`",
                f"- Minimum budget for grounding: `{case['minimum_budget_for_grounding']}`",
                f"- Grounding budget headroom: `{case['grounding_budget_headroom_tokens']}`",
                f"- Minimum top-k for grounding at tested budget: `{case['minimum_top_k_for_grounding_at_budget']}`",
                f"- Grounding file density: `{case['grounding_file_density']}`",
                f"- Provenance sections: `{case['provenance_section_count']}`",
                f"- Provenance tokens: `{case['selected_provenance_tokens']}`",
                f"- Top selected files: `{case['selected_unique_paths'][:6]}`",
            ]
        )
        if case["observation_citations"]:
            lines.append(f"- Rubric citations: `{case['observation_citations']}`")
        if case["requires_change_provenance"]:
            if case["supports_change_provenance"]:
                lines.append(
                    "- Provenance enabled: this case included git diff/log context in addition to repository file content."
                )
            else:
                lines.append(
                    "- Limitation: this use case needs git-history or diff context, and no provenance section was available."
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
