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
from agenvantage.feature_provider_validation import (
    feature_provider_fixture_readiness_report,
    load_feature_provider_dataset,
    run_feature_provider_validation,
    summarize_saved_feature_provider_validation_report,
)
from agenvantage.modality import (
    PROVIDER_PROFILES,
    apply_multimodal_pack,
    recoverable_blocks_from_manifest,
    resolve_recoverable_text_path,
    verify_mixed_modality_manifest,
    verify_recoverable_block,
)
from agenvantage.observability import (
    default_observability_db,
    init_observability_store,
    list_traces,
    load_trace,
    record_pack_trace,
    record_trace_artifact,
    record_trace_span,
    write_observability_dashboard,
)
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
from agenvantage.session import (
    build_session_task,
    create_feature_session,
    default_session_path,
    load_session_artifact,
    save_session_artifact,
)
from agenvantage.telemetry import configure_console_tracing, flush_tracing
from agenvantage.tokenizer import TokenCounter

_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
_DASHBOARD_PATH = _PACKAGE_ROOT / "viz" / "index.html"
_DEFAULT_FIXTURE = _PACKAGE_ROOT / "examples" / "synthetic_oncall_context.json"
_DEFAULT_PROVIDER_FIXTURE = _PACKAGE_ROOT / "examples" / "provider_validation_cases.json"
_DEFAULT_FEATURE_PROVIDER_FIXTURE = _PACKAGE_ROOT / "examples" / "feature_work_validation_cases.json"
_DEFAULT_REPOS_ROOT = _PACKAGE_ROOT.parent
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

    validate_feature_provider = subparsers.add_parser(
        "validate-feature-provider",
        help="Run or summarize provider-backed validation for feature-work context packing.",
        description=(
            "Compare full-scan, AgenVantage-packed, and cache-aligned feature-work "
            "prompts with provider usage, request cost, latency, and deterministic "
            "answer-plan grading."
        ),
    )
    validate_feature_provider.add_argument(
        "--fixture",
        type=Path,
        default=_DEFAULT_FEATURE_PROVIDER_FIXTURE,
        help=(
            "Feature-work validation fixture JSON "
            f"(default: {_DEFAULT_FEATURE_PROVIDER_FIXTURE.relative_to(_PACKAGE_ROOT)})."
        ),
    )
    validate_feature_provider.add_argument(
        "--repos-root",
        type=Path,
        default=_DEFAULT_REPOS_ROOT,
        help=f"Directory containing benchmark repositories (default: {_DEFAULT_REPOS_ROOT}).",
    )
    validate_feature_provider.add_argument(
        "--pricing",
        type=Path,
        help="Versioned pricing snapshot JSON used to compute request cost.",
    )
    validate_feature_provider.add_argument(
        "--records",
        type=Path,
        help="Optional JSON output path for raw request records and summary.",
    )
    validate_feature_provider.add_argument(
        "--replay",
        type=Path,
        help="Summarize a previously saved feature-provider validation JSON report.",
    )
    validate_feature_provider.add_argument(
        "--normalize",
        type=Path,
        help=(
            "Normalize raw request records or OTLP-style span exports into a "
            "provider-validation summary without calling a provider."
        ),
    )
    validate_feature_provider.add_argument(
        "--dry-run",
        action="store_true",
        help="Build prompt variants and token metrics locally without making provider calls.",
    )
    validate_feature_provider.add_argument(
        "--summary",
        action="store_true",
        help="Print a human-readable summary instead of raw JSON.",
    )
    validate_feature_provider.add_argument(
        "--trace-console",
        action="store_true",
        help="Print OpenTelemetry feature-provider spans to the console.",
    )
    validate_feature_provider.add_argument(
        "--model",
        default="gpt-4o-mini",
        help="Provider model identifier for live validation.",
    )
    validate_feature_provider.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="Number of times to run each case/policy combination.",
    )
    validate_feature_provider.add_argument(
        "--max-cases",
        type=int,
        default=None,
        help="Optional limit on the number of feature cases to run.",
    )
    validate_feature_provider.add_argument(
        "--api-key-env",
        default="OPENAI_API_KEY",
        help="Environment variable holding the OpenAI API key.",
    )
    validate_feature_provider.add_argument(
        "--base-url",
        default="https://api.openai.com/v1",
        help="Responses API base URL.",
    )
    validate_feature_provider.add_argument(
        "--environment-scope",
        default=None,
        help=(
            "Optional evidence scope override such as local_feature_work or production. "
            "Use only when the saved artifact came from that environment."
        ),
    )
    validate_feature_provider.add_argument(
        "--reconcile-costs",
        type=Path,
        help=(
            "Optional OpenAI Costs API export used to reconcile request-level "
            "estimated costs against organization-level recorded costs."
        ),
    )
    validate_feature_provider.add_argument(
        "--otel-export",
        type=Path,
        help="Optional OTLP-style JSON export path for the feature-provider records.",
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
        "--multimodal",
        choices=("off", "estimate", "artifact"),
        default="off",
        help=(
            "Optional mixed-modality context mode: off keeps text-only output, "
            "estimate reports modality math, artifact writes local PNG context pages."
        ),
    )
    pack.add_argument(
        "--modality-profile",
        choices=("auto", *tuple(sorted(PROVIDER_PROFILES))),
        default="auto",
        help="Provider image-token profile used for multimodal estimates (default: auto from --model).",
    )
    pack.add_argument(
        "--modality-output-dir",
        type=Path,
        help="Directory for multimodal artifacts (default: artifacts/context-images/<pack-id>).",
    )
    pack.add_argument(
        "--copy",
        action="store_true",
        help="Copy the Markdown context package to the system clipboard.",
    )

    observe = subparsers.add_parser(
        "observe",
        help="Record local AI-agent task traces and context metrics.",
        description=(
            "Beginner-friendly observability commands for local AI coding-agent work. "
            "A trace is one task; spans are the steps inside it."
        ),
    )
    observe_subparsers = observe.add_subparsers(dest="observe_command", required=True)

    observe_init = observe_subparsers.add_parser(
        "init",
        help="Create the local AgenVantage observability database.",
    )
    observe_init.add_argument(
        "--repo",
        type=Path,
        default=Path("."),
        help="Repository root for .agenvantage/observability.db (default: current directory).",
    )
    observe_init.add_argument("--db", type=Path, help="Explicit observability database path.")

    observe_pack = observe_subparsers.add_parser(
        "pack",
        help="Pack context and record a local observability trace.",
    )
    observe_pack.add_argument(
        "--repo",
        type=Path,
        action="append",
        help="Repository to inspect (default: current directory). Repeat for multiple repos.",
    )
    observe_pack.add_argument("--task", required=True, help="Feature task to prepare context for.")
    observe_pack.add_argument(
        "--preset",
        choices=preset_names(),
        default=None,
        help="Task recipe controlling instructions and provenance (default: feature).",
    )
    observe_pack.add_argument("--budget", type=int, default=None)
    observe_pack.add_argument("--model", default=None, help="Tokenizer model identifier.")
    observe_pack.add_argument("--top-k", type=int, default=None)
    observe_pack.add_argument("--include-diff", action="store_true")
    observe_pack.add_argument("--include-log", action="store_true")
    observe_pack.add_argument("--include-glob", action="append", default=[])
    observe_pack.add_argument("--exclude-glob", action="append", default=[])
    observe_pack.add_argument("--output", type=Path, help="Optional Markdown context output.")
    observe_pack.add_argument("--manifest", type=Path, help="Optional JSON manifest output.")
    observe_pack.add_argument("--db", type=Path, help="Explicit observability database path.")
    observe_pack.add_argument(
        "--multimodal",
        choices=("off", "estimate", "artifact"),
        default="off",
        help="Optional mixed-modality context mode.",
    )
    observe_pack.add_argument(
        "--modality-profile",
        choices=("auto", *tuple(sorted(PROVIDER_PROFILES))),
        default="auto",
    )
    observe_pack.add_argument("--modality-output-dir", type=Path)

    traces = subparsers.add_parser(
        "traces",
        help="Inspect local AgenVantage observability traces.",
    )
    traces_subparsers = traces.add_subparsers(dest="traces_command", required=True)
    traces_list = traces_subparsers.add_parser("list", help="List recent local traces.")
    traces_list.add_argument("--repo", type=Path, default=Path("."))
    traces_list.add_argument("--db", type=Path, help="Explicit observability database path.")
    traces_list.add_argument("--limit", type=int, default=20)
    traces_show = traces_subparsers.add_parser("show", help="Show one local trace.")
    traces_show.add_argument("trace_id")
    traces_show.add_argument("--repo", type=Path, default=Path("."))
    traces_show.add_argument("--db", type=Path, help="Explicit observability database path.")
    traces_show.add_argument("--json", dest="as_json", action="store_true")

    dashboard = subparsers.add_parser(
        "dashboard",
        help="Generate and open the local AgenVantage observability dashboard.",
        description=(
            "Render local trace metrics as a Datadog-style HTML dashboard with token "
            "savings, selected files, warnings, and spans."
        ),
    )
    dashboard.add_argument(
        "--repo",
        type=Path,
        default=Path("."),
        help="Repository root for .agenvantage/observability.db (default: current directory).",
    )
    dashboard.add_argument("--db", type=Path, help="Explicit observability database path.")
    dashboard.add_argument(
        "--output",
        type=Path,
        help="HTML output path (default: .agenvantage/observability-dashboard.html).",
    )
    dashboard.add_argument("--limit", type=int, default=100, help="Maximum traces to render.")
    dashboard.add_argument(
        "--no-browser",
        action="store_true",
        help="Print the dashboard URI instead of opening a browser tab.",
    )

    experiments = subparsers.add_parser(
        "experiments",
        help="Compare local context optimization strategies for agent tasks.",
    )
    experiments_subparsers = experiments.add_subparsers(
        dest="experiments_command",
        required=True,
    )
    experiments_compare = experiments_subparsers.add_parser(
        "compare",
        help="Compare full-scan, packed, cache-aligned, and mixed-artifact prompt variants.",
    )
    experiments_compare.add_argument(
        "--repo",
        type=Path,
        action="append",
        help="Repository to inspect (default: current directory). Repeat for multiple repos.",
    )
    experiments_compare.add_argument("--task", required=True, help="Feature task to compare.")
    experiments_compare.add_argument("--trace-id", help="Attach results to an existing trace.")
    experiments_compare.add_argument("--budget", type=int, default=None)
    experiments_compare.add_argument("--model", default=None, help="Tokenizer model identifier.")
    experiments_compare.add_argument("--top-k", type=int, default=None)
    experiments_compare.add_argument("--include-diff", action="store_true")
    experiments_compare.add_argument("--include-log", action="store_true")
    experiments_compare.add_argument("--include-glob", action="append", default=[])
    experiments_compare.add_argument("--exclude-glob", action="append", default=[])
    experiments_compare.add_argument("--db", type=Path, help="Explicit observability database path.")
    experiments_compare.add_argument(
        "--input-price-per-million",
        type=float,
        default=None,
        help="Optional input-token price used for local estimated cost comparisons.",
    )
    experiments_compare.add_argument(
        "--cached-input-price-ratio",
        type=float,
        default=0.1,
        help="Warm-cache input price ratio used for cache-aligned estimates (default: 0.1).",
    )
    experiments_compare.add_argument(
        "--output",
        type=Path,
        help="Optional JSON report output path.",
    )
    experiments_compare.add_argument(
        "--markdown-output",
        type=Path,
        help="Optional Markdown proof output path.",
    )
    experiments_compare.add_argument(
        "--summary",
        action="store_true",
        help="Print a readable Markdown summary instead of JSON.",
    )

    session = subparsers.add_parser(
        "session",
        help="Create and reuse cache-aligned feature-work context sessions.",
        description=(
            "Split feature-work context into a stable reusable prefix and small "
            "dynamic task packets for repeated agent prompts."
        ),
    )
    session_subparsers = session.add_subparsers(dest="session_command", required=True)

    session_init = session_subparsers.add_parser(
        "init",
        help="Create a cache-aligned feature session artifact from local repository context.",
    )
    session_init.add_argument(
        "--repo",
        type=Path,
        action="append",
        help="Repository to inspect (default: current directory). Repeat for multiple repos.",
    )
    session_init.add_argument("--task", required=True, help="Initial feature task.")
    session_init.add_argument(
        "--preset",
        choices=preset_names(),
        default=None,
        help="Task recipe for the initial context package (default: feature).",
    )
    session_init.add_argument("--budget", type=int, default=None)
    session_init.add_argument("--model", default=None, help="Tokenizer model identifier.")
    session_init.add_argument("--top-k", type=int, default=None)
    session_init.add_argument("--include-diff", action="store_true")
    session_init.add_argument("--include-log", action="store_true")
    session_init.add_argument("--include-glob", action="append", default=[])
    session_init.add_argument("--exclude-glob", action="append", default=[])
    session_init.add_argument("--session-id", help="Optional stable session identifier.")
    session_init.add_argument(
        "--output",
        type=Path,
        help="Session JSON output path (default: <repo>/.agenvantage/sessions/<id>.json).",
    )
    session_init.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Print the full session artifact as JSON.",
    )

    session_task = session_subparsers.add_parser(
        "task",
        help="Build a dynamic task packet from an existing feature session.",
    )
    session_task.add_argument("--session", type=Path, required=True, help="Session JSON path.")
    session_task.add_argument("--task", required=True, help="Follow-up task for the session.")
    session_task.add_argument("--model", default=None, help="Tokenizer model identifier.")
    session_task.add_argument("--output", type=Path, help="Optional Markdown prompt output.")
    session_task.add_argument("--manifest", type=Path, help="Optional JSON task manifest output.")
    session_task.add_argument(
        "--stdout",
        action="store_true",
        help="Print only the cache-aligned prompt Markdown.",
    )
    session_task.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Print the full task artifact as JSON.",
    )
    session_task.add_argument(
        "--copy",
        action="store_true",
        help="Copy the cache-aligned prompt to the system clipboard.",
    )

    rehydrate = subparsers.add_parser(
        "rehydrate",
        help="Recover exact source text from a mixed-modality artifact manifest.",
        description=(
            "List or print recoverable rec_... blocks written by "
            "agenvantage pack --multimodal artifact."
        ),
    )
    rehydrate.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="Mixed-modality artifact manifest.json path.",
    )
    rehydrate.add_argument(
        "--id",
        dest="recoverable_id",
        help="Recoverable block id to print, for example rec_ab12cd34ef56.",
    )
    rehydrate.add_argument(
        "--list",
        action="store_true",
        help="List recoverable block ids instead of printing block text.",
    )
    rehydrate.add_argument(
        "--verify",
        action="store_true",
        help="Verify image attachments and recoverable source hashes in the manifest.",
    )
    rehydrate.add_argument("--output", type=Path, help="Optional output path for recovered text.")
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
    prompt_accounting = report.get("prompt_token_accounting", {})
    safety = report.get("safety", {})
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
    if prompt_accounting:
        lines.extend(
            [
                (
                    "Prompt tokens: "
                    f"user={prompt_accounting['original_user_prompt_tokens']} "
                    f"full-scan={prompt_accounting['full_scan_prompt_tokens']} "
                    f"packed={prompt_accounting['packed_prompt_tokens']}"
                ),
                (
                    "Prompt savings: "
                    f"{prompt_accounting['prompt_tokens_saved_vs_full_scan']} tokens "
                    f"({prompt_accounting['prompt_reduction_percent_vs_full_scan']}%)"
                ),
            ]
        )
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
    if safety.get("selected_secret_redaction_count"):
        lines.append(
            "Safety: "
            f"redacted {safety['selected_secret_redaction_count']} secret-looking value(s)"
        )
    multimodal = report.get("multimodal") or {}
    if multimodal.get("enabled"):
        lines.append(
            "Multimodal: "
            f"{multimodal.get('mode')} "
            f"decision={multimodal.get('decision_reason')} "
            f"images={len(multimodal.get('image_attachments', []))} "
            f"estimated_mixed={multimodal.get('estimated_mixed_prompt_tokens')} tokens"
        )
        if multimodal.get("estimated_tokens_saved_vs_packed_text", 0) > 0:
            lines.append(
                "Multimodal savings: "
                f"{multimodal['estimated_tokens_saved_vs_packed_text']} tokens "
                f"({multimodal['estimated_reduction_percent_vs_packed_text']}%) "
                "vs packed text"
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
        "prompt_token_accounting": report.get("prompt_token_accounting", {}),
        "modality_plan": report.get("multimodal", {}),
        "image_attachments": (report.get("multimodal") or {}).get("image_attachments", []),
        "factsheets": (report.get("multimodal") or {}).get("factsheets", []),
        "recoverable_blocks": (report.get("multimodal") or {}).get("recoverable_blocks", []),
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


def _format_feature_provider_fixture_summary(report: dict[str, Any]) -> str:
    summary = report.get("summary", {})
    policy_summary = summary.get("policies", {})
    lines = [
        "AgenVantage feature-provider fixture",
        "",
        f"Dataset: {report['dataset_id']}",
        f"Cases: {report['case_count']}",
        f"Environment scope: {report['environment_scope']}",
        (
            "Median prompt reduction: "
            f"{summary.get('median_agenvantage_prompt_reduction_percent', 0.0)}% "
            "for AgenVantage packed vs full scan"
        ),
        (
            "Median prompts: "
            f"full-scan={summary.get('median_full_scan_prompt_tokens', 0.0)} "
            f"agenvantage={summary.get('median_agenvantage_packed_prompt_tokens', 0.0)}"
        ),
    ]
    cost = summary.get("estimated_input_cost")
    if isinstance(cost, dict):
        lines.extend(
            [
                (
                    "Estimated input cost: "
                    f"full-scan cold=${cost.get('median_full_unaligned_cold_input_cost_usd', 0.0):.8f} "
                    f"packed warm=${cost.get('median_budgeted_cache_aligned_warm_input_cost_usd', 0.0):.8f}"
                ),
                (
                    "Estimated warm input savings: "
                    f"{cost.get('median_budgeted_cache_aligned_warm_input_cost_reduction_percent', 0.0)}% "
                    "median vs full-scan cold input"
                ),
            ]
        )
    lines.extend(
        [
        (
            "Cache-ready packed cases: "
            f"{summary.get('cache_aligned_eligible_cases', 0)}/{report['case_count']} "
            f"(minimum stable prefix {report['minimum_cacheable_prefix_tokens']})"
        ),
        "",
        "Policy readiness:",
        ]
    )
    for policy_id in (
        "full_unaligned",
        "full_cache_aligned",
        "budgeted_unaligned",
        "budgeted_cache_aligned",
    ):
        policy = policy_summary.get(policy_id)
        if not isinstance(policy, dict):
            continue
        lines.append(
            "  "
            f"{policy_id:<22} "
            f"median_tokens={policy.get('median_local_prompt_tokens', 0.0):<9} "
            f"stable={policy.get('median_stable_prefix_tokens', 0.0):<8} "
            f"reduction={policy.get('median_prompt_reduction_percent_vs_full_scan', 0.0)}% "
            f"cache_ready={policy.get('cache_eligible_case_count', 0)}/{report['case_count']}"
        )
    return "\n".join(lines)


def _format_feature_provider_validation_summary(report: dict[str, Any]) -> str:
    lines = [
        "AgenVantage feature-provider validation",
        "",
        f"Dataset: {report.get('dataset_id') or 'replay'}",
        f"Records: {report.get('record_count', 0)}",
    ]
    prompt = report.get("prompt_readiness")
    if isinstance(prompt, dict):
        lines.extend(
            [
                (
                    "Prompt reduction: "
                    f"{prompt.get('median_agenvantage_prompt_reduction_percent', 0.0)}% "
                    "median AgenVantage packed vs full scan"
                ),
                (
                    "Median prompts: "
                    f"full-scan={prompt.get('median_full_scan_prompt_tokens', 0.0)} "
                    f"agenvantage={prompt.get('median_agenvantage_packed_prompt_tokens', 0.0)}"
                ),
            ]
        )
        cost = prompt.get("estimated_input_cost")
        if isinstance(cost, dict):
            lines.extend(
                [
                    (
                        "Estimated input cost: "
                        f"full-scan cold=${cost.get('median_full_unaligned_cold_input_cost_usd', 0.0):.8f} "
                        f"packed warm=${cost.get('median_budgeted_cache_aligned_warm_input_cost_usd', 0.0):.8f}"
                    ),
                    (
                        "Estimated warm input savings: "
                        f"{cost.get('median_budgeted_cache_aligned_warm_input_cost_reduction_percent', 0.0)}% "
                        "median vs full-scan cold input"
                    ),
                ]
            )
    lines.extend(["", _format_provider_validation_summary(report)])
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


def _resolve_pack_settings(
    args: argparse.Namespace,
    *,
    default_preset: str = DEFAULT_PRESET,
) -> dict[str, Any]:
    repos = args.repo if args.repo else [Path(".")]
    config: PackConfig = load_pack_config(list(repos) + [Path(".")])

    budget = args.budget if args.budget is not None else config.budget
    if budget is None:
        budget = _DEFAULT_PACK_BUDGET
    model = args.model or config.model or _DEFAULT_PACK_MODEL
    top_k = args.top_k if args.top_k is not None else config.top_k
    if top_k is None:
        top_k = _DEFAULT_PACK_TOP_K
    preset_name = args.preset or config.preset or default_preset
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


def _build_pack_artifacts(
    args: argparse.Namespace,
    *,
    default_preset: str = DEFAULT_PRESET,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    settings = _resolve_pack_settings(args, default_preset=default_preset)
    preset = settings["preset"]
    repos = settings["repos"]
    counter = TokenCounter(settings["model"])

    if len(repos) == 1:
        markdown, report = build_context_package(
            repos[0],
            args.task,
            settings["budget"],
            counter,
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
            counter,
            settings["top_k"],
            instructions=preset.instructions,
            include_diff=settings["include_diff"],
            include_log=settings["include_log"],
            include_globs=settings["include_globs"],
            exclude_globs=settings["exclude_globs"],
            workflow=settings["preset_name"] if settings["preset_name"] == "feature" else "generic",
        )

    report["preset"] = settings["preset_name"]
    markdown, report = apply_multimodal_pack(
        markdown,
        report,
        counter,
        mode=getattr(args, "multimodal", "off"),
        output_dir=getattr(args, "modality_output_dir", None),
        profile_id=getattr(args, "modality_profile", "auto"),
        model=settings["model"],
    )
    return markdown, report, settings


def _run_pack(args: argparse.Namespace) -> None:
    markdown, report, settings = _build_pack_artifacts(args)
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
    multimodal = report.get("multimodal") or {}
    if args.multimodal == "artifact" and multimodal.get("image_attachments"):
        notices.append(f"Multimodal artifacts written to {multimodal.get('artifact_root')}")

    machine_readable = args.stdout or args.as_json or args.handoff_json
    if notices:
        if machine_readable:
            for notice in notices:
                print(notice, file=sys.stderr)
        else:
            print()
            for notice in notices:
                print(notice)


def _observability_db_from_args(args: argparse.Namespace, repo: Path | None = None) -> Path:
    if getattr(args, "db", None) is not None:
        return Path(args.db)
    return default_observability_db(repo or getattr(args, "repo", None) or Path("."))


def _format_trace_created(trace: Any, db_path: Path, report: dict[str, Any]) -> str:
    change_surface = report.get("change_surface") or {}
    edit_targets = [
        str(item.get("path"))
        for item in change_surface.get("edit_targets", [])
        if isinstance(item, dict) and item.get("path")
    ]
    test_targets = [
        str(item.get("path"))
        for item in change_surface.get("test_targets", [])
        if isinstance(item, dict) and item.get("path")
    ]
    missing = [str(item) for item in change_surface.get("missing_signals", [])]
    lines = [
        "AgenVantage observed context pack",
        "",
        f"Trace: {trace.trace_id}",
        f"Task:  {trace.task}",
        f"DB:    {db_path.resolve()}",
        "",
        "Context:",
        f"  Full scan: {trace.full_scan_prompt_tokens:,} tokens",
        f"  Packed:    {trace.packed_prompt_tokens:,} tokens",
        f"  Saved:     {trace.tokens_saved:,} tokens ({trace.reduction_percent}%)",
        "",
    ]
    if edit_targets:
        lines.append(f"Likely edit files: {', '.join(edit_targets)}")
    if test_targets:
        lines.append(f"Tests to inspect: {', '.join(test_targets)}")
    if missing:
        lines.append("Warnings:")
        lines.extend(f"  - {item}" for item in missing)
    lines.extend(
        [
            "",
            "Why this matters:",
            "  The agent performs better when it sees the right files, but whole-repo prompts burn tokens.",
            "  AgenVantage recorded this local trace so you can inspect context and cost over time.",
            "",
            f"Next: agenvantage traces show {trace.trace_id}",
        ]
    )
    return "\n".join(lines).rstrip()


def _format_trace_list(traces: list[Any], db_path: Path) -> str:
    if not traces:
        return (
            "No AgenVantage traces found.\n\n"
            f"DB: {db_path.resolve()}\n"
            'Try: agenvantage observe pack --task "Add tests for upload limits"'
        )
    lines = ["AgenVantage traces", "", f"DB: {db_path.resolve()}", ""]
    for trace in traces:
        lines.append(
            f"{trace.trace_id}  {trace.created_at}  "
            f"saved={trace.tokens_saved:,} ({trace.reduction_percent}%)  "
            f"files={trace.selected_file_count}  {trace.task}"
        )
    return "\n".join(lines)


def _format_trace_detail(trace: dict[str, Any]) -> str:
    metadata = trace.get("metadata") or {}
    selected_files = metadata.get("selected_files") or []
    missing = metadata.get("missing_signals") or []
    lines = [
        "AgenVantage trace",
        "",
        f"Trace: {trace['trace_id']}",
        f"Task:  {trace['task']}",
        f"Repo:  {trace['repo_path']}",
        f"Status: {trace['status']}",
        "",
        "Token accounting:",
        f"  Full scan: {int(trace['full_scan_prompt_tokens']):,}",
        f"  Packed:    {int(trace['packed_prompt_tokens']):,}",
        f"  Saved:     {int(trace['tokens_saved']):,} ({trace['reduction_percent']}%)",
        "",
        "Selected files:",
    ]
    if selected_files:
        lines.extend(f"  - {path}" for path in selected_files)
    else:
        lines.append("  none")
    if missing:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"  - {item}" for item in missing)
    lines.append("")
    lines.append("Spans:")
    spans = trace.get("spans") or []
    if spans:
        for span in spans:
            lines.append(
                f"  - {span['kind']}: {float(span['duration_ms']):.2f} ms, "
                f"input_tokens={span['input_tokens']}"
            )
    else:
        lines.append("  none")
    artifacts = trace.get("artifacts") or []
    if artifacts:
        lines.append("")
        lines.append("Artifacts:")
        for artifact in artifacts:
            path = artifact.get("path") or "stored in SQLite"
            lines.append(f"  - {artifact['kind']}: {path}")
    return "\n".join(lines)


def _run_observe(args: argparse.Namespace) -> None:
    if args.observe_command == "init":
        db_path = _observability_db_from_args(args, args.repo)
        init_observability_store(db_path)
        print(
            "\n".join(
                [
                    "AgenVantage observability initialized.",
                    "",
                    "A trace = one developer task.",
                    "A span = one step inside that task, like scanning files or packing context.",
                    "A metric = a number, like tokens saved or estimated cost.",
                    "",
                    f"Local database: {db_path.resolve()}",
                    'Try: agenvantage observe pack --task "Add tests for upload limits"',
                ]
            )
        )
        return
    if args.observe_command == "pack":
        markdown, report, settings = _build_pack_artifacts(args, default_preset="feature")
        write_package_outputs(markdown, report, args.output, args.manifest)
        repo = Path(settings["repos"][0])
        db_path = _observability_db_from_args(args, repo)
        trace = record_pack_trace(
            db_path,
            markdown=markdown,
            report=report,
            repo_path=repo,
            workflow=str(settings["preset_name"]),
            artifact_paths={"markdown": args.output, "manifest": args.manifest},
        )
        print(_format_trace_created(trace, db_path, report))
        return
    raise SystemExit(f"Unknown observe command: {args.observe_command}")


def _run_traces(args: argparse.Namespace) -> None:
    db_path = _observability_db_from_args(args, args.repo)
    if args.traces_command == "list":
        print(_format_trace_list(list_traces(db_path, limit=args.limit), db_path))
        return
    if args.traces_command == "show":
        try:
            trace = load_trace(db_path, args.trace_id)
        except KeyError as exc:
            raise SystemExit(f"Trace not found: {args.trace_id}") from exc
        if args.as_json:
            print(json.dumps(trace, indent=2))
        else:
            print(_format_trace_detail(trace))
        return
    raise SystemExit(f"Unknown traces command: {args.traces_command}")


def _run_observability_dashboard(args: argparse.Namespace) -> None:
    repo = Path(args.repo).resolve()
    db_path = _observability_db_from_args(args, repo)
    output = Path(args.output) if args.output is not None else repo / ".agenvantage" / "observability-dashboard.html"
    dashboard_path = write_observability_dashboard(db_path, output, limit=args.limit)
    dashboard_uri = dashboard_path.resolve().as_uri()
    print(f"AgenVantage observability dashboard written to {dashboard_path.resolve()}")
    if args.no_browser:
        print(dashboard_uri)
    else:
        webbrowser.open(dashboard_uri)


def _estimated_input_cost(tokens: float, price_per_million: float | None) -> float | None:
    if price_per_million is None:
        return None
    return round((tokens / 1_000_000) * price_per_million, 8)


def _experiment_variant(
    *,
    variant_id: str,
    label: str,
    prompt_tokens: int,
    baseline_tokens: int,
    input_price_per_million: float | None,
    cache_eligible_tokens: int = 0,
    cached_input_price_ratio: float = 0.1,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    tokens_saved = baseline_tokens - prompt_tokens
    reduction = round((tokens_saved / baseline_tokens * 100) if baseline_tokens else 0.0, 2)
    cache_eligible_tokens = max(min(cache_eligible_tokens, prompt_tokens), 0)
    non_cached_tokens = prompt_tokens - cache_eligible_tokens
    warm_cache_weighted_tokens = non_cached_tokens + (cache_eligible_tokens * cached_input_price_ratio)
    warm_tokens_saved = baseline_tokens - warm_cache_weighted_tokens
    return {
        "variant_id": variant_id,
        "label": label,
        "prompt_tokens": prompt_tokens,
        "tokens_saved_vs_full_scan": tokens_saved,
        "reduction_percent_vs_full_scan": reduction,
        "cache_eligible_input_tokens": cache_eligible_tokens,
        "warm_cache_weighted_input_tokens": round(warm_cache_weighted_tokens, 2),
        "warm_cache_weighted_tokens_saved_vs_full_scan": round(warm_tokens_saved, 2),
        "estimated_input_cost_usd": _estimated_input_cost(prompt_tokens, input_price_per_million),
        "estimated_warm_cache_input_cost_usd": _estimated_input_cost(
            warm_cache_weighted_tokens,
            input_price_per_million,
        ),
        "notes": notes or [],
    }


def _format_experiment_comparison_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AgenVantage Context Experiment",
        "",
        f"Task: {report['task']}",
        f"Trace: {report['trace_id']}",
        f"Model: {report['model']}",
        "",
        "| Variant | Prompt tokens | Saved vs full scan | Reduction | Cache-eligible tokens | Warm-cache weighted tokens |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for variant in report["variants"]:
        lines.append(
            "| "
            f"{variant['label']} | "
            f"{int(variant['prompt_tokens']):,} | "
            f"{int(variant['tokens_saved_vs_full_scan']):,} | "
            f"{variant['reduction_percent_vs_full_scan']}% | "
            f"{int(variant['cache_eligible_input_tokens']):,} | "
            f"{variant['warm_cache_weighted_input_tokens']:,.2f} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- Full scan estimates the prompt size if every eligible repository chunk were included.",
            "- Packed text is the first-request AgenVantage context package.",
            "- Cache-aligned uses the same packed prompt size on the first request, then estimates warm-cache weighting for stable context.",
            "- Mixed artifact uses the local mixed-modality estimator; it is not provider-billed proof.",
            "",
            "## Claim Boundary",
            "",
            "These are local planning metrics. They prove prompt-token reduction and cache layout readiness, not live provider billing, latency, or broad answer-quality retention.",
        ]
    )
    return "\n".join(lines) + "\n"


def _build_experiment_comparison(args: argparse.Namespace) -> tuple[dict[str, Any], str, dict[str, Any]]:
    pack_args = argparse.Namespace(**vars(args))
    pack_args.preset = "feature"
    pack_args.multimodal = "off"
    pack_args.modality_profile = "auto"
    pack_args.modality_output_dir = None
    markdown, packed_report, settings = _build_pack_artifacts(pack_args, default_preset="feature")

    mixed_args = argparse.Namespace(**vars(args))
    mixed_args.preset = "feature"
    mixed_args.multimodal = "estimate"
    mixed_args.modality_profile = "auto"
    mixed_args.modality_output_dir = None
    _, mixed_report, _ = _build_pack_artifacts(mixed_args, default_preset="feature")

    accounting = packed_report.get("prompt_token_accounting") or {}
    baseline_tokens = int(accounting.get("full_scan_prompt_tokens") or packed_report.get("candidate_context_tokens") or 0)
    packed_tokens = int(accounting.get("packed_prompt_tokens") or packed_report.get("selected_context_tokens") or 0)
    original_user_prompt_tokens = int(accounting.get("original_user_prompt_tokens") or 0)
    stable_cache_tokens = max(packed_tokens - original_user_prompt_tokens, 0)
    mixed = mixed_report.get("multimodal") or {}
    mixed_tokens = int(mixed.get("estimated_mixed_prompt_tokens") or packed_tokens)
    input_price = args.input_price_per_million
    cached_ratio = args.cached_input_price_ratio
    variants = [
        _experiment_variant(
            variant_id="full_scan",
            label="Full scan text",
            prompt_tokens=baseline_tokens,
            baseline_tokens=baseline_tokens,
            input_price_per_million=input_price,
            notes=["Local full-corpus counterfactual from eligible repository context."],
        ),
        _experiment_variant(
            variant_id="packed_text",
            label="Packed text",
            prompt_tokens=packed_tokens,
            baseline_tokens=baseline_tokens,
            input_price_per_million=input_price,
            notes=["First-request AgenVantage selected context."],
        ),
        _experiment_variant(
            variant_id="packed_cache_aligned",
            label="Packed cache-aligned",
            prompt_tokens=packed_tokens,
            baseline_tokens=baseline_tokens,
            input_price_per_million=input_price,
            cache_eligible_tokens=stable_cache_tokens,
            cached_input_price_ratio=cached_ratio,
            notes=["First request has the same input tokens as packed text; warm-cache weighting is estimated."],
        ),
        _experiment_variant(
            variant_id="packed_mixed_artifact",
            label="Packed mixed artifact",
            prompt_tokens=mixed_tokens,
            baseline_tokens=baseline_tokens,
            input_price_per_million=input_price,
            notes=[str(item) for item in mixed.get("measurement_notes", [])]
            or ["Local mixed-modality estimate; no provider call was made."],
        ),
    ]
    report = {
        "project": "AgenVantage",
        "workflow": "experiments.compare",
        "task": args.task,
        "model": settings["model"],
        "repos": [str(Path(repo).resolve()) for repo in settings["repos"]],
        "budget": settings["budget"],
        "trace_id": args.trace_id or "",
        "input_price_per_million": input_price,
        "cached_input_price_ratio": cached_ratio,
        "baseline_variant": "full_scan",
        "variants": variants,
        "selected_files": sorted(
            {
                str(chunk.get("path"))
                for chunk in packed_report.get("selected_chunks", [])
                if isinstance(chunk, dict) and chunk.get("path")
            }
        ),
        "change_surface": packed_report.get("change_surface", {}),
        "claim_boundary": [
            "Local token counts are measured with the configured tokenizer.",
            "Estimated costs use caller-provided input price when available.",
            "Warm-cache numbers model cache pricing and do not prove provider-billed savings.",
            "Mixed artifact numbers use local provider-profile estimates and do not prove live image-token billing.",
        ],
        "packed_report": packed_report,
        "mixed_modality": mixed,
    }
    return report, markdown, settings


def _run_experiments(args: argparse.Namespace) -> None:
    if args.experiments_command != "compare":
        raise SystemExit(f"Unknown experiments command: {args.experiments_command}")

    report, markdown, settings = _build_experiment_comparison(args)
    repo = Path(settings["repos"][0])
    db_path = _observability_db_from_args(args, repo)
    if args.trace_id:
        load_trace(db_path, args.trace_id)
        trace_id = args.trace_id
    else:
        trace = record_pack_trace(
            db_path,
            markdown=markdown,
            report=report["packed_report"],
            repo_path=repo,
            workflow="experiments.compare",
        )
        trace_id = trace.trace_id
    report["trace_id"] = trace_id

    markdown_report = _format_experiment_comparison_markdown(report)
    if args.output is not None:
        _write_report(report, args.output)
    if args.markdown_output is not None:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(markdown_report, encoding="utf-8")

    record_trace_artifact(
        db_path,
        trace_id,
        kind="experiment_comparison",
        content=json.dumps(report, indent=2),
        path=args.output,
        metadata={"workflow": "experiments.compare", "format": "json"},
    )
    record_trace_artifact(
        db_path,
        trace_id,
        kind="experiment_comparison_markdown",
        content=markdown_report,
        path=args.markdown_output,
        metadata={"workflow": "experiments.compare", "format": "markdown"},
    )
    packed_variant = next(item for item in report["variants"] if item["variant_id"] == "packed_text")
    record_trace_span(
        db_path,
        trace_id,
        name="Experiment comparison",
        kind="experiment.compare",
        input_tokens=int(packed_variant["prompt_tokens"]),
        metadata={
            "variant_count": len(report["variants"]),
            "best_reduction_percent": max(
                float(item["reduction_percent_vs_full_scan"]) for item in report["variants"]
            ),
        },
    )

    if args.summary:
        print(markdown_report)
    else:
        print(json.dumps(report, indent=2))
    print(f"Experiment comparison attached to trace {trace_id}", file=sys.stderr)


def _format_session_init_summary(session: dict[str, Any], output: Path) -> str:
    cache = session.get("cache", {})
    prompt_accounting = session.get("prompt_token_accounting", {})
    lines = [
        "AgenVantage feature session",
        "",
        f"Session: {session['session_id']}",
        f"Output:  {output.resolve()}",
        f"Repos:   {len(session.get('repo_paths', []))}",
        f"Stable prefix: {cache.get('stable_prefix_tokens', 0)} tokens",
        f"Initial dynamic packet: {cache.get('initial_dynamic_packet_tokens', 0)} tokens",
        f"Initial prompt: {cache.get('initial_prompt_tokens', 0)} tokens",
        (
            "Cache eligible: "
            f"{cache.get('cache_eligible')} "
            f"(minimum {cache.get('minimum_cacheable_prefix_tokens', 0)} stable-prefix tokens)"
        ),
        f"Reusable prefix share: {cache.get('initial_reusable_prefix_percent', 0.0)}%",
    ]
    if prompt_accounting:
        lines.append(
            "Packed vs full scan: "
            f"{prompt_accounting.get('packed_prompt_tokens', 0)} / "
            f"{prompt_accounting.get('full_scan_prompt_tokens', 0)} tokens "
            f"({prompt_accounting.get('prompt_reduction_percent_vs_full_scan', 0.0)}% saved)"
        )
    safety = session.get("safety", {})
    if safety.get("selected_secret_redaction_count"):
        lines.append(
            "Safety: "
            f"redacted {safety['selected_secret_redaction_count']} secret-looking value(s)"
        )
    return "\n".join(lines)


def _format_session_task_summary(task_artifact: dict[str, Any]) -> str:
    cache = task_artifact["cache"]
    return "\n".join(
        [
            "AgenVantage feature session task",
            "",
            f"Session: {task_artifact.get('session_id')}",
            f"Stable prefix: {cache['stable_prefix_tokens']} tokens",
            f"Dynamic packet: {cache['dynamic_packet_tokens']} tokens",
            f"Prompt: {cache['prompt_tokens']} tokens",
            (
                "Cache eligible: "
                f"{cache['cache_eligible']} "
                f"(minimum {cache['minimum_cacheable_prefix_tokens']} stable-prefix tokens)"
            ),
            f"Reusable prefix share: {cache['reusable_prefix_percent']}%",
            (
                "Estimated warm-call uncached input: "
                f"{cache['estimated_uncached_tokens_after_cache_hit']} tokens "
                "(provider cache hit still must be verified live)"
            ),
        ]
    )


def _run_session(args: argparse.Namespace) -> None:
    if args.session_command == "init":
        settings = _resolve_pack_settings(args, default_preset="feature")
        preset = settings["preset"]
        repos = settings["repos"]
        counter = TokenCounter(settings["model"])
        session = create_feature_session(
            repos,
            args.task,
            settings["budget"],
            counter,
            top_k=settings["top_k"],
            instructions=preset.instructions,
            include_diff=settings["include_diff"],
            include_log=settings["include_log"],
            include_globs=settings["include_globs"],
            exclude_globs=settings["exclude_globs"],
            session_id=args.session_id,
        )
        output = args.output or default_session_path(repos[0], str(session["session_id"]))
        save_session_artifact(session, output)
        if args.as_json:
            print(json.dumps(session, indent=2))
            print(f"Session written to {output.resolve()}", file=sys.stderr)
        else:
            print(_format_session_init_summary(session, output))
        return

    if args.session_command == "task":
        session = load_session_artifact(args.session)
        model = args.model or (session.get("settings") or {}).get("model") or _DEFAULT_PACK_MODEL
        task_artifact = build_session_task(session, args.task, TokenCounter(model))
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(task_artifact["prompt_markdown"], encoding="utf-8")
        if args.manifest is not None:
            _write_report(task_artifact, args.manifest)

        if args.stdout:
            print(task_artifact["prompt_markdown"])
        elif args.as_json:
            print(json.dumps(task_artifact, indent=2))
        else:
            print(_format_session_task_summary(task_artifact))

        notices: list[str] = []
        if args.copy:
            if _copy_to_clipboard(task_artifact["prompt_markdown"]):
                notices.append("Session prompt copied to clipboard.")
            else:
                notices.append("Could not access a clipboard tool; use --stdout or --output instead.")
        if args.output is not None:
            notices.append(f"Session prompt written to {args.output.resolve()}")
        if args.manifest is not None:
            notices.append(f"Session task manifest written to {args.manifest.resolve()}")
        if notices:
            if args.stdout or args.as_json:
                for notice in notices:
                    print(notice, file=sys.stderr)
            else:
                print()
                for notice in notices:
                    print(notice)
        return

    raise SystemExit(f"Unknown session command: {args.session_command}")


def _format_recoverable_block_list(blocks: list[dict[str, Any]]) -> str:
    if not blocks:
        return "No recoverable blocks were recorded in this manifest."
    lines = ["Recoverable blocks:"]
    for block in blocks:
        line = f"  {block.get('id', '<missing-id>')}"
        path = block.get("path")
        if path:
            line += f"  {path}"
            start = block.get("start_line")
            end = block.get("end_line")
            if start and end:
                line += f"#L{start}-L{end}"
        lines.append(line)
    return "\n".join(lines)


def _format_artifact_verification(summary: dict[str, Any]) -> str:
    status = "passed" if summary["ok"] else "failed"
    lines = [
        f"Artifact verification {status}",
        f"Recoverable blocks: {summary['recoverable_block_count']}",
        f"Image attachments: {summary['image_attachment_count']}",
        f"Factsheets: {summary.get('factsheet_count', 0)}",
        f"Errors: {summary['error_count']}",
    ]
    if summary.get("artifact_bundle_expected_sha256"):
        bundle_status = "verified" if summary.get("artifact_bundle_verified") else "failed"
        lines.append(f"Artifact bundle: {bundle_status}")
    for error in summary["errors"]:
        lines.append(f"- {error}")
    return "\n".join(lines)


def _run_rehydrate(args: argparse.Namespace) -> None:
    try:
        blocks = recoverable_blocks_from_manifest(args.manifest)
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    if args.verify:
        try:
            summary = verify_mixed_modality_manifest(args.manifest)
        except (FileNotFoundError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc
        print(_format_artifact_verification(summary))
        if not summary["ok"]:
            raise SystemExit(1)
        if not args.recoverable_id and not args.list:
            return
    if args.list:
        print(_format_recoverable_block_list(blocks))
        return
    if not args.recoverable_id:
        raise SystemExit("Rehydrate requires --id unless --list is used.")
    block = next((item for item in blocks if item.get("id") == args.recoverable_id), None)
    if block is None:
        available = ", ".join(str(item.get("id")) for item in blocks[:8] if item.get("id"))
        hint = f" Available ids: {available}" if available else ""
        raise SystemExit(f"Recoverable block not found: {args.recoverable_id}.{hint}")
    try:
        text_path = resolve_recoverable_text_path(args.manifest, block)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if not text_path.is_file():
        raise SystemExit(f"Recoverable source file not found: {text_path}")
    errors = verify_recoverable_block(args.manifest, block)
    if errors:
        raise SystemExit(errors[0])
    text = text_path.read_text(encoding="utf-8")
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"Recovered source written to {args.output.resolve()}")
    else:
        print(text, end="" if text.endswith("\n") else "\n")


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


def _run_feature_provider_validation(args: argparse.Namespace) -> dict[str, Any]:
    if args.trace_console:
        configure_console_tracing()

    counter = TokenCounter(args.model)
    pricing = load_pricing_snapshot(args.pricing) if args.pricing is not None else None

    if args.replay is not None:
        if not args.replay.is_file():
            raise SystemExit(f"Replay report not found: {args.replay}")
        replay_report = json.loads(args.replay.read_text(encoding="utf-8"))
        report = summarize_saved_feature_provider_validation_report(
            replay_report,
            pricing=pricing,
            environment_scope=args.environment_scope,
        )
    elif args.normalize is not None:
        if not args.normalize.is_file():
            raise SystemExit(f"Normalization input not found: {args.normalize}")
        raw_payload = json.loads(args.normalize.read_text(encoding="utf-8"))
        report = summarize_normalized_provider_validation_payload(
            raw_payload,
            pricing=pricing,
            environment_scope=args.environment_scope,
        )
        report["workflow"] = "feature_provider_validation"
    else:
        if not args.fixture.is_file():
            raise SystemExit(f"Feature-provider fixture not found: {args.fixture}")
        dataset = load_feature_provider_dataset(args.fixture)
        if args.dry_run:
            report = feature_provider_fixture_readiness_report(
                dataset,
                args.repos_root,
                counter,
                max_cases=args.max_cases,
                pricing=pricing,
            )
        else:
            if pricing is None:
                raise SystemExit("Live feature-provider validation requires --pricing.")
            api_key = os.getenv(args.api_key_env)
            if not api_key:
                raise SystemExit(
                    f"Live feature-provider validation requires {args.api_key_env} to be set."
                )
            report = run_feature_provider_validation(
                dataset,
                args.repos_root,
                counter,
                OpenAIResponsesTransport(api_key=api_key, base_url=args.base_url),
                args.model,
                pricing,
                repeats=args.repeats,
                max_cases=args.max_cases,
                environment_scope=args.environment_scope,
            )

    if args.reconcile_costs is not None:
        if not args.reconcile_costs.is_file():
            raise SystemExit(f"Costs reconciliation input not found: {args.reconcile_costs}")
        costs_payload = json.loads(args.reconcile_costs.read_text(encoding="utf-8"))
        report["cost_reconciliation"] = reconcile_provider_costs(report, costs_payload)

    if args.summary:
        if args.dry_run and args.replay is None and args.normalize is None:
            print(_format_feature_provider_fixture_summary(report))
        else:
            print(_format_feature_provider_validation_summary(report))
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
    if args.command == "observe":
        _run_observe(args)
        return
    if args.command == "traces":
        _run_traces(args)
        return
    if args.command == "dashboard":
        _run_observability_dashboard(args)
        return
    if args.command == "experiments":
        _run_experiments(args)
        return
    if args.command == "session":
        _run_session(args)
        return
    if args.command == "rehydrate":
        _run_rehydrate(args)
        return
    if args.command == "validate-provider":
        _run_provider_validation(args)
        return
    if args.command == "validate-feature-provider":
        _run_feature_provider_validation(args)
        return

    _run_experiment(
        args.fixture,
        args.budget,
        args.model,
        trace_console=args.trace_console,
        output=args.output,
        summary=args.summary,
    )
