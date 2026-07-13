from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any

from agenvantage.repo_context import build_context_package
from agenvantage.tokenizer import TokenCounter


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = REPO_ROOT / "examples" / "graphify_ablation_cases.json"
DEFAULT_REPOS_ROOT = REPO_ROOT / "external" / "graphify-eval"
DEFAULT_OUTPUT_JSON = REPO_ROOT / "artifacts" / "graphify-ablation.json"
DEFAULT_OUTPUT_MD = REPO_ROOT / "artifacts" / "graphify-ablation.md"


def _tokens(value: str) -> set[str]:
    split = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    return set(re.findall(r"[a-z0-9]+", split.casefold()))


def _recall(expected: list[str], actual: list[str]) -> float:
    if not expected:
        return 1.0
    return len(set(expected) & set(actual)) / len(set(expected))


def _surface_paths(report: dict[str, Any], key: str) -> list[str]:
    surface = report.get("change_surface") or {}
    return [
        str(item["relative_path"])
        for item in surface.get(key, [])
        if item.get("relative_path")
    ]


def _selected_paths(report: dict[str, Any]) -> list[str]:
    return sorted(
        {
            str(chunk["relative_path"])
            for chunk in report.get("selected_chunks", [])
        }
    )


def _evidence_coverage(
    evidence_slots: dict[str, Any],
    report: dict[str, Any],
) -> tuple[list[str], list[str]]:
    covered: list[str] = []
    missing: list[str] = []
    for name, slot in evidence_slots.items():
        start, end = (int(value) for value in slot["line_range"])
        anchors = _tokens(" ".join(str(value) for value in slot["all_anchor_tokens"]))
        matches = [
            chunk
            for chunk in report.get("selected_chunks", [])
            if chunk["relative_path"] == slot["path"]
            and int(chunk["start_line"]) <= end
            and int(chunk["end_line"]) >= start
            and anchors <= _tokens(str(chunk.get("text", "")))
        ]
        (covered if matches else missing).append(str(name))
    return covered, missing


def _missing_expansions(report: dict[str, Any]) -> list[str]:
    surface = report.get("change_surface") or {}
    sufficiency = report.get("context_sufficiency") or {}
    validation = report.get("validation_requirements") or {}
    values = [
        *(str(item) for item in surface.get("missing_signals", [])),
        *(
            f"missing_slot:{item}"
            for item in sufficiency.get("missing_slots", [])
        ),
        *(
            f"recommended:{item}"
            for item in sufficiency.get("recommended_expansions", [])
        ),
        *(
            f"mandatory:{item}"
            for item in validation.get("missing_mandatory_evidence", [])
        ),
    ]
    return list(dict.fromkeys(values))


def _git_head(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()


def _validate_case_annotations(case: dict[str, Any], repo: Path) -> None:
    for name, slot in case.get("evidence_slots", {}).items():
        path = repo / str(slot["path"])
        if not path.is_file():
            raise ValueError(f"{case['case_id']}:{name} source is missing: {path}")
        lines = path.read_text(encoding="utf-8").splitlines()
        start, end = (int(value) for value in slot["line_range"])
        if not 1 <= start <= end <= len(lines):
            raise ValueError(
                f"{case['case_id']}:{name} line range {start}-{end} is invalid."
            )
        anchors = _tokens(
            " ".join(str(value) for value in slot["all_anchor_tokens"])
        )
        region_tokens = _tokens("\n".join(lines[start - 1 : end]))
        if not anchors <= region_tokens:
            missing = sorted(anchors - region_tokens)
            raise ValueError(
                f"{case['case_id']}:{name} anchors are not in the annotated region: {missing}"
            )


def _run_variant(
    *,
    case: dict[str, Any],
    repo: Path,
    graph_path: Path,
    counter: TokenCounter,
    graph_enabled: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    _, report = build_context_package(
        repo,
        str(case["task"]),
        int(case.get("budget", 6_000)),
        counter,
        top_k=int(case.get("top_k", 28)),
        workflow="feature",
        graph_backend="graphify" if graph_enabled else "off",
        graph_json=graph_path if graph_enabled else None,
        graph_hops=int(case.get("graph_hops", 2)),
    )
    runtime_ms = round((time.perf_counter() - started) * 1_000, 2)
    covered, missing = _evidence_coverage(case.get("evidence_slots", {}), report)
    expected_edit = [str(value) for value in case.get("expected_edit_targets", [])]
    expected_test = [str(value) for value in case.get("expected_test_targets", [])]
    surface_edit = _surface_paths(report, "edit_targets")
    surface_test = _surface_paths(report, "test_targets")
    selected = _selected_paths(report)
    graph = report.get("graph") or {}
    graph_runtime_ms = sum(
        float(item.get("runtime_ms", 0.0)) for item in graph.get("repos", [])
    )
    return {
        "variant": "graphify" if graph_enabled else "baseline",
        "selected_paths": selected,
        "surface_edit_targets": surface_edit,
        "surface_test_targets": surface_test,
        "edit_target_recall": round(_recall(expected_edit, surface_edit), 4),
        "test_target_recall": round(_recall(expected_test, surface_test), 4),
        "selected_edit_target_recall": round(_recall(expected_edit, selected), 4),
        "selected_test_target_recall": round(_recall(expected_test, selected), 4),
        "covered_evidence_slots": covered,
        "missing_evidence_slots": missing,
        "evidence_slot_recall": round(
            len(covered) / max(len(case.get("evidence_slots", {})), 1),
            4,
        ),
        "missing_expansions": _missing_expansions(report),
        "missing_expansion_count": len(_missing_expansions(report)),
        "packed_prompt_tokens": int(report["prompt_token_accounting"]["packed_prompt_tokens"]),
        "full_scan_prompt_tokens": int(
            report["prompt_token_accounting"]["full_scan_prompt_tokens"]
        ),
        "pack_runtime_ms": runtime_ms,
        "graph_runtime_ms": round(graph_runtime_ms, 2),
        "graph": graph,
        "handoff_ready": bool(report.get("handoff_ready", True)),
        "sufficiency_status": str(
            (report.get("context_sufficiency") or {}).get("status", "")
        ),
    }


def _aggregate_recall(
    cases: list[dict[str, Any]],
    variant: str,
    field: str,
) -> float:
    expected = 0
    found = 0
    expected_key = (
        "expected_edit_targets" if field == "edit_target_recall" else "expected_test_targets"
    )
    actual_key = (
        "surface_edit_targets" if field == "edit_target_recall" else "surface_test_targets"
    )
    for case in cases:
        expected_values = set(case[expected_key])
        expected += len(expected_values)
        found += len(expected_values & set(case[variant][actual_key]))
    return found / expected if expected else 1.0


def _summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    total_slots = sum(len(case.get("evidence_slots", {})) for case in cases)
    baseline_covered = sum(
        len(case["baseline"]["covered_evidence_slots"]) for case in cases
    )
    graph_covered = sum(
        len(case["graphify"]["covered_evidence_slots"]) for case in cases
    )
    baseline_evidence = baseline_covered / total_slots if total_slots else 1.0
    graph_evidence = graph_covered / total_slots if total_slots else 1.0
    baseline_missing = sum(
        case["baseline"]["missing_expansion_count"] for case in cases
    )
    graph_missing = sum(case["graphify"]["missing_expansion_count"] for case in cases)
    missing_reduction = (
        (baseline_missing - graph_missing) / baseline_missing
        if baseline_missing
        else 0.0
    )
    baseline_edit = _aggregate_recall(cases, "baseline", "edit_target_recall")
    graph_edit = _aggregate_recall(cases, "graphify", "edit_target_recall")
    baseline_test = _aggregate_recall(cases, "baseline", "test_target_recall")
    graph_test = _aggregate_recall(cases, "graphify", "test_target_recall")
    evidence_delta_pp = (graph_evidence - baseline_evidence) * 100
    quality_gate = evidence_delta_pp >= 5.0 or missing_reduction >= 0.25
    no_regression = graph_edit >= baseline_edit and graph_test >= baseline_test
    graph_used_cases = sum(case["graphify"]["graph"].get("used", False) for case in cases)
    return {
        "case_count": len(cases),
        "repository_count": len({case["repository_id"] for case in cases}),
        "graph_used_case_count": graph_used_cases,
        "evidence_slot_count": total_slots,
        "baseline_evidence_recall": round(baseline_evidence, 4),
        "graphify_evidence_recall": round(graph_evidence, 4),
        "evidence_recall_delta_percentage_points": round(evidence_delta_pp, 2),
        "baseline_missing_expansions": baseline_missing,
        "graphify_missing_expansions": graph_missing,
        "missing_expansion_reduction_percent": round(missing_reduction * 100, 2),
        "baseline_edit_target_recall": round(baseline_edit, 4),
        "graphify_edit_target_recall": round(graph_edit, 4),
        "baseline_test_target_recall": round(baseline_test, 4),
        "graphify_test_target_recall": round(graph_test, 4),
        "median_baseline_prompt_tokens": statistics.median(
            case["baseline"]["packed_prompt_tokens"] for case in cases
        ),
        "median_graphify_prompt_tokens": statistics.median(
            case["graphify"]["packed_prompt_tokens"] for case in cases
        ),
        "median_baseline_pack_runtime_ms": statistics.median(
            case["baseline"]["pack_runtime_ms"] for case in cases
        ),
        "median_graphify_pack_runtime_ms": statistics.median(
            case["graphify"]["pack_runtime_ms"] for case in cases
        ),
        "median_graph_backend_runtime_ms": statistics.median(
            case["graphify"]["graph_runtime_ms"] for case in cases
        ),
        "acceptance": {
            "recall_or_expansion_gate": quality_gate,
            "no_edit_test_recall_regression": no_regression,
            "overall_pass": quality_gate and no_regression and graph_used_cases == len(cases),
            "thresholds": {
                "minimum_evidence_recall_delta_percentage_points": 5.0,
                "minimum_missing_expansion_reduction_percent": 25.0,
            },
        },
    }


def _render_markdown(result: dict[str, Any]) -> str:
    summary = result["summary"]
    lines = [
        "# Graphify Retrieval Ablation",
        "",
        f"Cases: {summary['case_count']} across {summary['repository_count']} repositories.",
        f"Overall deterministic gate: {'PASS' if summary['acceptance']['overall_pass'] else 'FAIL'}.",
        "",
        "## Aggregate",
        "",
        f"- Evidence recall: {summary['baseline_evidence_recall']:.2%} baseline → {summary['graphify_evidence_recall']:.2%} Graphify ({summary['evidence_recall_delta_percentage_points']:+.2f} pp).",
        f"- Missing expansions: {summary['baseline_missing_expansions']} baseline → {summary['graphify_missing_expansions']} Graphify ({summary['missing_expansion_reduction_percent']:.2f}% reduction).",
        f"- Edit-target recall: {summary['baseline_edit_target_recall']:.2%} baseline → {summary['graphify_edit_target_recall']:.2%} Graphify.",
        f"- Test-target recall: {summary['baseline_test_target_recall']:.2%} baseline → {summary['graphify_test_target_recall']:.2%} Graphify.",
        f"- Median packed tokens: {summary['median_baseline_prompt_tokens']} baseline → {summary['median_graphify_prompt_tokens']} Graphify.",
        f"- Median pack runtime: {summary['median_baseline_pack_runtime_ms']} ms baseline → {summary['median_graphify_pack_runtime_ms']} ms Graphify.",
        "",
        "## Cases",
        "",
    ]
    for case in result["cases"]:
        lines.extend(
            [
                f"### {case['case_id']}",
                f"- Repository: `{case['repository_id']}` at `{case['commit']}`.",
                f"- Evidence recall: {case['baseline']['evidence_slot_recall']:.2%} → {case['graphify']['evidence_slot_recall']:.2%}.",
                f"- Missing expansions: {case['baseline']['missing_expansion_count']} → {case['graphify']['missing_expansion_count']}.",
                f"- Packed tokens: {case['baseline']['packed_prompt_tokens']} → {case['graphify']['packed_prompt_tokens']}.",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def run_ablation(
    fixture: Path = DEFAULT_FIXTURE,
    repos_root: Path = DEFAULT_REPOS_ROOT,
) -> dict[str, Any]:
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    repositories = payload["repositories"]
    counter = TokenCounter()
    cases: list[dict[str, Any]] = []
    for case in payload["cases"]:
        repository_id = str(case["repository"])
        repository = repositories[repository_id]
        repo = (repos_root / repository["path"]).resolve()
        graph_path = (repos_root / repository["graph"]).resolve()
        expected_commit = str(repository["commit"])
        actual_commit = _git_head(repo)
        if actual_commit != expected_commit:
            raise RuntimeError(
                f"{repository_id} revision mismatch: expected {expected_commit}, got {actual_commit}"
            )
        _validate_case_annotations(case, repo)
        baseline = _run_variant(
            case=case,
            repo=repo,
            graph_path=graph_path,
            counter=counter,
            graph_enabled=False,
        )
        graphify = _run_variant(
            case=case,
            repo=repo,
            graph_path=graph_path,
            counter=counter,
            graph_enabled=True,
        )
        cases.append(
            {
                **case,
                "repository_id": repository_id,
                "commit": expected_commit,
                "graphify_version": payload["graphify"]["version"],
                "baseline": baseline,
                "graphify": graphify,
            }
        )
    return {
        "schema_version": 1,
        "graphify": payload["graphify"],
        "summary": _summary(cases),
        "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run paired baseline-versus-Graphify repository retrieval."
    )
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--repos-root", type=Path, default=DEFAULT_REPOS_ROOT)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_OUTPUT_MD)
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    result = run_ablation(args.fixture, args.repos_root)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.write_text(_render_markdown(result), encoding="utf-8")
    print(json.dumps(result["summary"] if args.summary else result, indent=2))
    print(f"\nJSON report written to {args.output_json.resolve()}")
    print(f"Markdown report written to {args.output_md.resolve()}")


if __name__ == "__main__":
    main()
