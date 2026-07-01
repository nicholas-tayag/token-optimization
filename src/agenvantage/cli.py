from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Any

from agenvantage import __version__
from agenvantage.config import PackConfig, load_pack_config
from agenvantage.experiment import load_scenario, run_experiment
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
    if top_files:
        lines.append("")
        lines.append("Top selected files:")
        for path, tokens in top_files:
            lines.append(f"  {tokens:>5} tok  {path}")
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
        )

    report["preset"] = settings["preset_name"]
    write_package_outputs(markdown, report, args.output, args.manifest)

    if args.stdout:
        print(markdown)
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

    machine_readable = args.stdout or args.as_json
    if notices:
        if machine_readable:
            for notice in notices:
                print(notice, file=sys.stderr)
        else:
            print()
            for notice in notices:
                print(notice)


def main() -> None:
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

    _run_experiment(
        args.fixture,
        args.budget,
        args.model,
        trace_console=args.trace_console,
        output=args.output,
        summary=args.summary,
    )
