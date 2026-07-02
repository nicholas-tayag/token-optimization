from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Any

from agenvantage import __version__
from agenvantage.config import PackConfig, load_pack_config
from agenvantage.env import load_dotenv
from agenvantage.experiment import load_scenario, run_experiment
from agenvantage.provider_validation import (
    OpenAIResponsesTransport,
    fixture_readiness_report,
    load_pricing_snapshot,
    load_provider_validation_dataset,
    provider_validation_report_to_otel_export,
    reconcile_provider_costs,
    run_provider_validation,
    summarize_normalized_provider_validation_payload,
    summarize_saved_provider_validation_report,
    summarize_provider_validation_records,
)
from agenvantage.presets import DEFAULT_PRESET, get_preset, preset_names
from agenvantage.repo_context import (
    build_context_package,
    build_multi_repo_context_package,
    write_package_outputs,
)
from agenvantage.telemetry import configure_console_tracing, flush_tracing
from agenvantage.tokenizer import TokenCounter

_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
_DASHBOARD_PATH = _PACKAGE_ROOT / "viz" / "index.html"
_DEFAULT_FIXTURE = _PACKAGE_ROOT / "examples" / "synthetic_oncall_context.json"
_DEFAULT_PROVIDER_FIXTURE = _PACKAGE_ROOT / "examples" / "provider_validation_cases.json"
_DEFAULT_BUDGET = 360
_DEFAULT_DEMO_OUTPUT = _PACKAGE_ROOT / "artifacts" / "oncall-report.json"
_DEFAULT_PACK_BUDGET = 6000
_DEFAULT_PACK_MODEL = "gpt-4o-mini"
_DEFAULT_PACK_TOP_K = 20


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agenvantage",
        description="Build token-budgeted context packages and compare assembly policies.",
        epilog=(
            "Examples:\n"
            "  agenvantage demo\n"
            "  agenvantage pack --task \"Explain the rate limiter\" --copy\n"
            "  agenvantage pack --preset debug --task \"Why do checkout tests fail?\"\n"
            "  agenvantage run --summary"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    demo = subparsers.add_parser(
        "demo",
        help="Run the built-in on-call demo and open the policy explorer.",
        description="Run the synthetic on-call scenario, save a report, and open the dashboard.",
    )
    demo.add_argument(
        "--fixture",
        type=Path,
        default=_DEFAULT_FIXTURE,
        help=f"Scenario JSON file (default: {_DEFAULT_FIXTURE.relative_to(_PACKAGE_ROOT)}).",
    )
    demo.add_argument(
        "--budget",
        type=int,
        default=_DEFAULT_BUDGET,
        help=f"Budgeted policy token cap (default: {_DEFAULT_BUDGET}).",
    )
    demo.add_argument(
        "--model",
        default="gpt-4o-mini",
        help="Tokenizer model identifier.",
    )
    demo.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_DEMO_OUTPUT,
        help=f"JSON report path (default: {_DEFAULT_DEMO_OUTPUT.relative_to(_PACKAGE_ROOT)}).",
    )
    demo.add_argument(
        "--trace-console",
        action="store_true",
        help="Print OpenTelemetry assembly spans to the console.",
    )
    demo.add_argument(
        "--no-browser",
        action="store_true",
        help="Print the dashboard path instead of opening a browser tab.",
    )

    run = subparsers.add_parser("run", help="Compare context assembly policies.")
    run.add_argument(
        "--fixture",
        type=Path,
        default=_DEFAULT_FIXTURE,
        help=f"Scenario JSON file (default: {_DEFAULT_FIXTURE.relative_to(_PACKAGE_ROOT)}).",
    )
    run.add_argument(
        "--budget",
        type=int,
        default=_DEFAULT_BUDGET,
        help=f"Budgeted policy token cap (default: {_DEFAULT_BUDGET}).",
    )
    run.add_argument("--model", default="gpt-4o-mini", help="Tokenizer model identifier.")
    run.add_argument("--output", type=Path, help="Optional JSON report path.")
    run.add_argument(
        "--trace-console",
        action="store_true",
        help="Print OpenTelemetry assembly spans to the console.",
    )
    run.add_argument(
        "--summary",
        action="store_true",
        help="Print a human-readable summary instead of raw JSON.",
    )

    validate_provider = subparsers.add_parser(
        "validate-provider",
        help="Run or summarize provider-backed validation for cost, latency, and quality claims.",
        description=(
            "Measure provider usage and deterministic answer quality across four policy "
            "conditions, or summarize previously recorded results."
        ),
    )
    validate_provider.add_argument(
        "--fixture",
        type=Path,
        default=_DEFAULT_PROVIDER_FIXTURE,
        help=(
            f"Provider-validation dataset JSON "
            f"(default: {_DEFAULT_PROVIDER_FIXTURE.relative_to(_PACKAGE_ROOT)})."
        ),
    )
    validate_provider.add_argument(
        "--pricing",
        type=Path,
        help="Versioned pricing snapshot JSON used to compute request cost.",
    )
    validate_provider.add_argument(
        "--records",
        type=Path,
        help="Optional JSON output path for raw request records and summary.",
    )
    validate_provider.add_argument(
        "--replay",
        type=Path,
        help="Summarize a previously saved validation JSON report instead of calling a provider.",
    )
    validate_provider.add_argument(
        "--normalize",
        type=Path,
        help=(
            "Normalize raw request records or OTLP-style span exports into a "
            "provider-validation summary without calling a provider."
        ),
    )
    validate_provider.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate fixture readiness locally without making provider calls.",
    )
    validate_provider.add_argument(
        "--summary",
        action="store_true",
        help="Print a human-readable summary instead of raw JSON.",
    )
    validate_provider.add_argument(
        "--trace-console",
        action="store_true",
        help="Print OpenTelemetry provider-validation spans to the console.",
    )
    validate_provider.add_argument(
        "--model",
        default="gpt-4o-mini",
        help="Provider model identifier for live validation.",
    )
    validate_provider.add_argument(
        "--budget",
        type=int,
        default=None,
        help="Override the dataset budget for budgeted policy runs.",
    )
    validate_provider.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="Number of times to run each case/policy combination.",
    )
    validate_provider.add_argument(
        "--max-cases",
        type=int,
        default=None,
        help="Optional limit on the number of dataset cases to run.",
    )
    validate_provider.add_argument(
        "--api-key-env",
        default="OPENAI_API_KEY",
        help="Environment variable holding the OpenAI API key.",
    )
    validate_provider.add_argument(
        "--base-url",
        default="https://api.openai.com/v1",
        help="Responses API base URL.",
    )
    validate_provider.add_argument(
        "--environment-scope",
        default=None,
        help=(
            "Optional evidence scope override such as synthetic_local or production. "
            "Use only when the saved artifact came from that environment."
        ),
    )
    validate_provider.add_argument(
        "--reconcile-costs",
        type=Path,
        help=(
            "Optional OpenAI Costs API export used to reconcile request-level "
            "estimated costs against organization-level recorded costs."
        ),
    )
    validate_provider.add_argument(
        "--otel-export",
        type=Path,
        help=(
            "Optional OTLP-style JSON export path for the provider-validation "
            "records, useful for replay and observability workflows."
        ),
    )

    view = subparsers.add_parser("view", help="Open the policy explorer dashboard in a browser.")
    view.add_argument(
        "--report",
        type=Path,
        help="Optional JSON report to open (use the file picker in the dashboard if omitted).",
    )
    view.add_argument(
        "--no-browser",
        action="store_true",
        help="Print the dashboard path instead of opening a browser tab.",
    )
    pack = subparsers.add_parser(
        "pack",
        help="Build a token-budgeted context package from a local repository.",
        description=(
            "Scan one or more local repositories, rank relevant code under a token "
            "budget, and produce a ready-to-paste context package."
        ),
    )
    pack.add_argument(
        "--repo",
        type=Path,
        action="append",
        help="Repository to inspect (default: current directory). Repeat for multiple repos.",
    )
    pack.add_argument("--task", required=True, help="Coding task or question to prepare context for.")
    pack.add_argument(
        "--preset",
        choices=preset_names(),
        default=None,
        help=f"Task recipe controlling instructions and provenance (default: {DEFAULT_PRESET}).",
    )
    pack.add_argument(
        "--budget",
        type=int,
        default=None,
        help=f"Maximum packaged input tokens (default: {_DEFAULT_PACK_BUDGET}).",
    )
    pack.add_argument("--model", default=None, help="Tokenizer model identifier.")
    pack.add_argument(
        "--top-k",
        type=int,
        default=None,
        help="Maximum number of ranked candidate chunks considered for selection.",
    )
    pack.add_argument(
        "--include-diff",
        action="store_true",
        help="Include staged and working-tree git diff provenance (added to the preset default).",
    )
    pack.add_argument(
        "--include-log",
        action="store_true",
        help="Include recent git commit-log provenance (added to the preset default).",
    )
    pack.add_argument(
        "--include-glob",
        action="append",
        default=[],
        help="Restrict eligible repository files to paths matching this glob. Repeat as needed.",
    )
    pack.add_argument(
        "--exclude-glob",
        action="append",
        default=[],
        help="Exclude eligible repository files whose paths match this glob. Repeat as needed.",
    )
    pack.add_argument("--output", type=Path, help="Optional Markdown context-package output.")
    pack.add_argument("--manifest", type=Path, help="Optional JSON decision-manifest output.")
    pack.add_argument(
        "--stdout",
        action="store_true",
        help="Print only the Markdown context package (pipe-friendly).",
    )
    pack.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Print the full JSON decision manifest instead of a summary.",
    )
    pack.add_argument(
        "--handoff-json",
        action="store_true",
        help="Print a structured agent handoff payload for IDE or agent workflows.",
    )
    pack.add_argument(
        "--copy",
        action="store_true",
        help="Copy the Markdown context package to the system clipboard.",
    )
    return parser


def _format_experiment_summary(report: dict[str, Any]) -> str:
    lines = [
        "AgenVantage experiment",
        "",
        f"Scenario: {report['scenario_id']}",
        report["scenario_description"],
        f"Tokenizer: {report['tokenizer']['model']} ({report['tokenizer']['encoding']})",
        "",
        "Policy comparison:",
    ]
    for policy in report["policies"]:
        name = policy["policy"]
        tokens = policy["input_tokens"]
        stable = policy["stable_prefix_tokens"]
        saved = policy["tokens_saved_vs_full"]
        if saved:
            detail = f"saved {saved} tokens ({policy['token_reduction_percent_vs_full']:.1f}%)"
            excluded = policy["excluded_components"]
            if excluded:
                dropped = ", ".join(item["id"] for item in excluded)
                detail += f"; excluded: {dropped}"
        elif name == "cache_aligned":
            full = next(item for item in report["policies"] if item["policy"] == "full")
            delta = stable - full["stable_prefix_tokens"]
            detail = f"stable prefix +{delta} tokens vs full"
        else:
            detail = "baseline"
        lines.append(f"  {name:<14} {tokens:>4} tokens  {detail}")
        if policy.get("budget") is not None:
            lines[-1] += f"  (budget {policy['budget']})"
    return "\n".join(lines)


def _format_pack_summary(report: dict[str, Any], preset_name: str) -> str:
    file_tokens: dict[str, int] = {}
    for chunk in report.get("selected_chunks", []):
        key = chunk.get("path") or chunk.get("relative_path") or "?"
        file_tokens[key] = file_tokens.get(key, 0) + int(chunk.get("tokens", 0))
    top_files = sorted(file_tokens.items(), key=lambda item: item[1], reverse=True)[:8]

    provenance = report.get("provenance", {})
    lines = [
        "AgenVantage context package",
        "",
        f"Task:    {report['task']}",
        f"Preset:  {preset_name}",
        f"Repos:   {report['repo_count']}  ·  scanned files: {report['scanned_files']}",
        f"Budget:  {report['selected_context_tokens']} / {report['budget']} tokens used",
        (
            f"Reduction: {report['local_reduction_percent_vs_candidate_context']}% "
            f"vs {report['candidate_context_tokens']} scanned-corpus tokens "
            f"({report['local_tokens_omitted_vs_candidate_context']} omitted)"
        ),
        f"Chunks:  {len(report.get('selected_chunks', []))} selected of {report['candidate_chunks']} candidates",
    ]
    if provenance.get("enabled"):
        bits = []
        if provenance.get("include_diff"):
            bits.append("diff")
        if provenance.get("include_log"):
            bits.append("log")
        lines.append(
            f"Provenance: {', '.join(bits)} "
            f"({provenance.get('selected_provenance_tokens', 0)} tokens)"
        )
    if report.get("uncovered_query_terms"):
        lines.append(f"Uncovered concepts: {', '.join(report['uncovered_query_terms'])}")
    change_surface = report.get("change_surface") or {}
    if preset_name == "feature":
        edit_targets = [item["path"] for item in change_surface.get("edit_targets", [])]
        test_targets = [item["path"] for item in change_surface.get("test_targets", [])]
        config_targets = [item["path"] for item in change_surface.get("config_targets", [])]
        supporting_targets = [item["path"] for item in change_surface.get("supporting_targets", [])]
        missing_signals = change_surface.get("missing_signals", [])
        if edit_targets:
            lines.append(f"Likely edit files: {', '.join(edit_targets)}")
        if test_targets:
            lines.append(f"Tests to inspect: {', '.join(test_targets)}")
        if config_targets:
            lines.append(f"Config/schema targets: {', '.join(config_targets)}")
        if supporting_targets:
            lines.append(f"Supporting files: {', '.join(supporting_targets)}")
        if missing_signals:
            lines.append(f"Missing signals: {' | '.join(missing_signals)}")
    if top_files:
        lines.append("")
        lines.append("Top selected files:")
        for path, tokens in top_files:
            lines.append(f"  {tokens:>5} tok  {path}")
    return "\n".join(lines)


def _build_handoff_payload(markdown: str, report: dict[str, Any], preset_name: str) -> dict[str, Any]:
    change_surface = report.get("change_surface") or {
        "edit_targets": [],
        "test_targets": [],
        "config_targets": [],
        "supporting_targets": [],
        "missing_signals": [],
    }
    return {
        "workflow": preset_name,
        "task": report["task"],
        "system_prefix": (
            markdown.split("## Task\n\n", 1)[0].rstrip()
            if "## Task\n\n" in markdown
            else markdown
        ),
        "task_suffix": report["task"],
        "change_surface": {
            "edit_targets": [item["path"] for item in change_surface.get("edit_targets", [])],
            "test_targets": [item["path"] for item in change_surface.get("test_targets", [])],
            "config_targets": [item["path"] for item in change_surface.get("config_targets", [])],
            "supporting_targets": [item["path"] for item in change_surface.get("supporting_targets", [])],
            "missing_signals": list(change_surface.get("missing_signals", [])),
        },
        "selected_chunks": report.get("selected_chunks", []),
        "prompt_markdown": markdown,
    }


def _format_provider_validation_summary(report: dict[str, Any]) -> str:
    lines = [
        "AgenVantage provider validation",
        "",
        f"Dataset: {report.get('dataset_id') or 'replay'}",
        f"Records: {report['record_count']}",
        "",
        "Policy metrics:",
    ]
    policies = report.get("policies", {})
    for policy_name in (
        "full_unaligned",
        "full_cache_aligned",
        "budgeted_unaligned",
        "budgeted_cache_aligned",
    ):
        if policy_name not in policies:
            continue
        policy = policies[policy_name]
        lines.append(
            "  "
            f"{policy_name:<22} "
            f"n={policy['request_count']:<3} "
            f"cost=${policy['mean_request_cost_usd']:.6f} "
            f"p50={policy['p50_latency_ms']:.2f}ms "
            f"cache_hit={policy['cache_hit_rate']:.2f} "
            f"correct={policy['correctness_pass_rate']:.2f} "
            f"grounded={policy['grounded_citation_pass_rate']:.2f} "
            f"safe={policy['safety_pass_rate']:.2f}"
        )

    lines.extend(["", "Claim audit:"])
    for claim_name, claim in report.get("claim_audit", {}).items():
        status = "supported" if claim.get("supported") else "not yet supported"
        lines.append(f"  {claim_name:<28} {status}  {claim.get('reason', '')}")

    paired = report.get("paired_case_comparison", {})
    paired_metrics = paired.get("metrics", {})
    if paired and paired_metrics:
        lines.extend(
            [
                "",
                "Paired deltas (candidate - baseline):",
                (
                    "  "
                    f"overlapping_cases={paired.get('overlapping_case_count', 0)} "
                    f"baseline={paired.get('baseline_policy_id')} "
                    f"candidate={paired.get('candidate_policy_id')}"
                ),
            ]
        )
        for metric_name in (
            "request_cost_usd",
            "latency_ms",
            "correctness_pass_rate",
            "grounded_citation_pass_rate",
            "safety_pass_rate",
            "overall_pass_rate",
        ):
            metric = paired_metrics.get(metric_name)
            if not isinstance(metric, dict):
                continue
            interval = metric.get("confidence_interval") or {}
            if interval:
                ci_text = (
                    f"[{interval.get('lower')}, {interval.get('upper')}] "
                    f"@ {interval.get('confidence')}"
                )
            else:
                ci_text = "n/a"
            lines.append(
                "  "
                f"{metric_name:<28} mean_delta={metric.get('mean_delta', 0.0)} "
                f"ci={ci_text}"
            )

    readiness = report.get("evidence_readiness", {})
    if readiness:
        completeness = readiness.get("record_completeness", {})
        lines.extend(
            [
                "",
                "Evidence readiness:",
                (
                    "  "
                    f"scope={readiness.get('observed_environment_scope', 'unknown')} "
                    f"production_ready={readiness.get('production_scope_ready')}"
                ),
                (
                    "  "
                    f"latency_samples baseline={readiness.get('baseline_latency_sample_count', 0)}/"
                    f"{readiness.get('required_latency_samples_per_policy', 0)} "
                    f"candidate={readiness.get('candidate_latency_sample_count', 0)}/"
                    f"{readiness.get('required_latency_samples_per_policy', 0)} "
                    f"ready={readiness.get('latency_sample_requirement_met')}"
                ),
                (
                    "  "
                    f"broad_cases={readiness.get('candidate_distinct_cases', 0)}/"
                    f"{readiness.get('required_distinct_cases', 0)} "
                    f"failure_types={readiness.get('candidate_distinct_failure_types', 0)}/"
                    f"{readiness.get('required_failure_types', 0)} "
                    f"paired_cases={readiness.get('baseline_candidate_case_overlap_count', 0)} "
                    f"ready={readiness.get('broad_case_requirement_met') and readiness.get('failure_type_requirement_met') and readiness.get('case_pairing_requirement_met')}"
                ),
                (
                    "  "
                    f"grade_coverage={completeness.get('complete_grade_record_count', 0)}/"
                    f"{completeness.get('record_count', 0)} "
                    f"records_complete={completeness.get('complete_record_count', 0)}/"
                    f"{completeness.get('record_count', 0)}"
                ),
            ]
        )
        missing_fields = [
            f"{field.replace('missing_', '')}={count}"
            for field, count in completeness.items()
            if field.startswith("missing_") and count
        ]
        if missing_fields:
            lines.append(f"  missing_fields: {', '.join(missing_fields)}")

    reconciliation = report.get("cost_reconciliation", {})
    if reconciliation:
        lines.extend(
            [
                "",
                "Cost reconciliation:",
                (
                    "  "
                    f"estimated=${reconciliation.get('estimated_request_level_total_cost_usd', 0.0):.6f} "
                    f"recorded=${reconciliation.get('recorded_organization_total_cost_usd', 0.0):.6f} "
                    f"diff=${reconciliation.get('difference_usd', 0.0):.6f}"
                ),
                (
                    "  "
                    f"ratio_vs_estimate={reconciliation.get('difference_ratio_vs_estimate')} "
                    f"time_window_overlap={reconciliation.get('time_window_overlap')} "
                    f"buckets={reconciliation.get('bucket_count')}"
                ),
            ]
        )
        if reconciliation.get("project_ids"):
            lines.append(f"  project_ids: {', '.join(reconciliation['project_ids'])}")

    return "\n".join(lines)


def _format_provider_fixture_summary(report: dict[str, Any]) -> str:
    lines = [
        "AgenVantage provider-validation fixture",
        "",
        f"Dataset: {report['dataset_id']}",
        f"Cases: {report['case_count']}",
        f"Budget: {report['budget']}",
        (
            "Cache-ready stable prefix: "
            f"{report['average_cache_aligned_stable_prefix_tokens']} tokens "
            f"(minimum target {report['minimum_cacheable_prefix_tokens']})"
        ),
        (
            "Average budgeted reduction vs full: "
            f"{report['average_budgeted_reduction_percent_vs_full']}%"
        ),
        "",
        "Case readiness:",
    ]
    for case in report["cases"]:
        lines.append(
            "  "
            f"{case['case_id']:<34} "
            f"stable={case['cache_aligned_stable_prefix_tokens']:<7} "
            f"budgeted={case['budgeted_cache_aligned_tokens']:<5} "
            f"cache_ready={case['cache_eligible']}"
        )
    return "\n".join(lines)


def _copy_to_clipboard(text: str) -> bool:
    if sys.platform == "darwin":
        commands = [["pbcopy"]]
    elif sys.platform.startswith("win"):
        commands = [["clip"]]
    else:
        commands = [["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]

    for command in commands:
        if shutil.which(command[0]) is None:
            continue
        try:
            subprocess.run(command, input=text, text=True, check=True)
            return True
        except (subprocess.SubprocessError, OSError):
            continue
    return False


def _write_report(report: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def _open_dashboard(report: Path | None, *, open_browser: bool) -> None:
    if not _DASHBOARD_PATH.is_file():
        raise SystemExit(f"Dashboard not found at {_DASHBOARD_PATH}")

    if report is not None:
        dashboard_uri = _DASHBOARD_PATH.resolve().as_uri()
        print(f"Dashboard: {_DASHBOARD_PATH.resolve()}")
        print(f"Report:    {report.resolve()}")
        print("Load the report in the dashboard file picker to explore the results.")
        if open_browser:
            webbrowser.open(dashboard_uri)
        return

    if open_browser:
        webbrowser.open(_DASHBOARD_PATH.resolve().as_uri())
    else:
        print(_DASHBOARD_PATH.resolve())


def _run_experiment(
    fixture: Path,
    budget: int,
    model: str,
    *,
    trace_console: bool,
    output: Path | None,
    summary: bool,
) -> dict[str, Any]:
    if not fixture.is_file():
        raise SystemExit(f"Scenario fixture not found: {fixture}")

    if trace_console:
        configure_console_tracing()

    scenario = load_scenario(fixture)
    report = run_experiment(scenario, TokenCounter(model), budget)

    if summary:
        print(_format_experiment_summary(report))
    else:
        print(json.dumps(report, indent=2))

    if output is not None:
        _write_report(report, output)
        print(f"\nReport written to {output.resolve()}")

    flush_tracing()
    return report


def _resolve_pack_settings(args: argparse.Namespace) -> dict[str, Any]:
    repos = args.repo if args.repo else [Path(".")]
    config: PackConfig = load_pack_config(list(repos) + [Path(".")])

    budget = args.budget if args.budget is not None else config.budget
    if budget is None:
        budget = _DEFAULT_PACK_BUDGET
    model = args.model or config.model or _DEFAULT_PACK_MODEL
    top_k = args.top_k if args.top_k is not None else config.top_k
    if top_k is None:
        top_k = _DEFAULT_PACK_TOP_K
    preset_name = args.preset or config.preset or DEFAULT_PRESET
    preset = get_preset(preset_name)

    include_globs = tuple(config.include_glob) + tuple(args.include_glob)
    exclude_globs = tuple(config.exclude_glob) + tuple(args.exclude_glob)

    return {
        "repos": repos,
        "budget": budget,
        "model": model,
        "top_k": top_k,
        "preset_name": preset_name,
        "preset": preset,
        "include_globs": include_globs,
        "exclude_globs": exclude_globs,
        "include_diff": args.include_diff or preset.include_diff,
        "include_log": args.include_log or preset.include_log,
        "config_source": config.source,
    }


def _run_pack(args: argparse.Namespace) -> None:
    settings = _resolve_pack_settings(args)
    preset = settings["preset"]
    repos = settings["repos"]

    if len(repos) == 1:
        markdown, report = build_context_package(
            repos[0],
            args.task,
            settings["budget"],
            TokenCounter(settings["model"]),
            settings["top_k"],
            instructions=preset.instructions,
            include_diff=settings["include_diff"],
            include_log=settings["include_log"],
            include_globs=settings["include_globs"],
            exclude_globs=settings["exclude_globs"],
            workflow=settings["preset_name"] if settings["preset_name"] == "feature" else "generic",
        )
    else:
        markdown, report = build_multi_repo_context_package(
            repos,
            args.task,
            settings["budget"],
            TokenCounter(settings["model"]),
            settings["top_k"],
            instructions=preset.instructions,
            include_diff=settings["include_diff"],
            include_log=settings["include_log"],
            include_globs=settings["include_globs"],
            exclude_globs=settings["exclude_globs"],
            workflow=settings["preset_name"] if settings["preset_name"] == "feature" else "generic",
        )

    report["preset"] = settings["preset_name"]
    write_package_outputs(markdown, report, args.output, args.manifest)

    if args.stdout:
        print(markdown)
    elif args.handoff_json:
        print(json.dumps(_build_handoff_payload(markdown, report, settings["preset_name"]), indent=2))
    elif args.as_json:
        print(json.dumps(report, indent=2))
    else:
        print(_format_pack_summary(report, settings["preset_name"]))

    notices: list[str] = []
    if settings["config_source"] is not None:
        notices.append(f"Applied config from {settings['config_source']}")
    if args.copy:
        if _copy_to_clipboard(markdown):
            notices.append("Context package copied to clipboard.")
        else:
            notices.append("Could not access a clipboard tool; use --stdout or --output instead.")
    if args.output:
        notices.append(f"Context package written to {args.output.resolve()}")
    if args.manifest:
        notices.append(f"Decision manifest written to {args.manifest.resolve()}")

    machine_readable = args.stdout or args.as_json or args.handoff_json
    if notices:
        if machine_readable:
            for notice in notices:
                print(notice, file=sys.stderr)
        else:
            print()
            for notice in notices:
                print(notice)


def _run_provider_validation(args: argparse.Namespace) -> dict[str, Any]:
    if args.trace_console:
        configure_console_tracing()

    counter = TokenCounter(args.model)
    dataset = (
        load_provider_validation_dataset(args.fixture)
        if args.fixture.is_file()
        else None
    )

    if args.replay is not None:
        if not args.replay.is_file():
            raise SystemExit(f"Replay report not found: {args.replay}")
        replay_report = json.loads(args.replay.read_text(encoding="utf-8"))
        report = summarize_saved_provider_validation_report(
            replay_report,
            dataset=dataset,
            pricing=(
                load_pricing_snapshot(args.pricing)
                if args.pricing is not None
                else None
            ),
            environment_scope=args.environment_scope,
        )
    elif args.normalize is not None:
        if not args.normalize.is_file():
            raise SystemExit(f"Normalization input not found: {args.normalize}")
        raw_payload = json.loads(args.normalize.read_text(encoding="utf-8"))
        report = summarize_normalized_provider_validation_payload(
            raw_payload,
            dataset=dataset,
            pricing=(
                load_pricing_snapshot(args.pricing)
                if args.pricing is not None
                else None
            ),
            environment_scope=args.environment_scope,
        )
    else:
        if not args.fixture.is_file():
            raise SystemExit(f"Provider-validation fixture not found: {args.fixture}")
        assert dataset is not None
        if args.dry_run:
            report = fixture_readiness_report(dataset, counter, args.budget)
        else:
            pricing_path = args.pricing
            if pricing_path is None:
                raise SystemExit("Live provider validation requires --pricing.")
            api_key = os.getenv(args.api_key_env)
            if not api_key:
                raise SystemExit(
                    f"Live provider validation requires {args.api_key_env} to be set."
                )
            pricing = load_pricing_snapshot(pricing_path)
            report = run_provider_validation(
                dataset,
                counter,
                OpenAIResponsesTransport(api_key=api_key, base_url=args.base_url),
                args.model,
                pricing,
                budget=args.budget,
                repeats=args.repeats,
                max_cases=args.max_cases,
            )

    if args.reconcile_costs is not None:
        if not args.reconcile_costs.is_file():
            raise SystemExit(f"Costs reconciliation input not found: {args.reconcile_costs}")
        costs_payload = json.loads(args.reconcile_costs.read_text(encoding="utf-8"))
        report["cost_reconciliation"] = reconcile_provider_costs(report, costs_payload)

    if args.summary:
        if args.dry_run and args.replay is None:
            print(_format_provider_fixture_summary(report))
        else:
            print(_format_provider_validation_summary(report))
    else:
        print(json.dumps(report, indent=2))

    if args.records is not None:
        _write_report(report, args.records)
        print(f"\nReport written to {args.records.resolve()}")
    if args.otel_export is not None:
        _write_report(provider_validation_report_to_otel_export(report), args.otel_export)
        print(f"OTLP-style export written to {args.otel_export.resolve()}")

    flush_tracing()
    return report


def main() -> None:
    load_dotenv()
    args = _parser().parse_args()
    if args.command == "demo":
        _run_experiment(
            args.fixture,
            args.budget,
            args.model,
            trace_console=args.trace_console,
            output=args.output,
            summary=True,
        )
        print()
        _open_dashboard(args.output, open_browser=not args.no_browser)
        return
    if args.command == "view":
        _open_dashboard(args.report, open_browser=not args.no_browser)
        return
    if args.command == "pack":
        _run_pack(args)
        return
    if args.command == "validate-provider":
        _run_provider_validation(args)
        return

    _run_experiment(
        args.fixture,
        args.budget,
        args.model,
        trace_console=args.trace_console,
        output=args.output,
        summary=args.summary,
    )
