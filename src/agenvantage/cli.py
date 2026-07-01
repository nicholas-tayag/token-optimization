from __future__ import annotations

import argparse
import json
import webbrowser
from pathlib import Path
from typing import Any

from agenvantage import __version__
from agenvantage.experiment import load_scenario, run_experiment
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agenvantage",
        description="Build token-budgeted context packages and compare assembly policies.",
        epilog=(
            "Examples:\n"
            "  agenvantage demo\n"
            "  agenvantage run --budget 360\n"
            "  agenvantage pack --repo . --task \"Explain the CLI\" --budget 1800\n"
            "  agenvantage view --report artifacts/oncall-report.json"
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
        "pack", help="Build a token-budgeted context package from a local repository."
    )
    pack.add_argument(
        "--repo",
        type=Path,
        action="append",
        required=True,
        help="Repository to inspect. Repeat to package multiple repositories together.",
    )
    pack.add_argument("--task", required=True, help="Coding task or question to prepare context for.")
    pack.add_argument("--budget", type=int, required=True, help="Maximum packaged input tokens.")
    pack.add_argument("--model", default="gpt-4o-mini", help="Tokenizer model identifier.")
    pack.add_argument(
        "--top-k",
        type=int,
        default=20,
        help="Maximum number of ranked candidate chunks considered for selection.",
    )
    pack.add_argument(
        "--include-diff",
        action="store_true",
        help="Include staged and working-tree git diff provenance in the packaged context.",
    )
    pack.add_argument(
        "--include-log",
        action="store_true",
        help="Include recent git commit-log provenance in the packaged context.",
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
        help="Exclude eligible repository files whose paths matching this glob. Repeat as needed.",
    )
    pack.add_argument("--output", type=Path, help="Optional Markdown context-package output.")
    pack.add_argument("--manifest", type=Path, help="Optional JSON decision-manifest output.")
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
        if len(args.repo) == 1:
            markdown, report = build_context_package(
                args.repo[0],
                args.task,
                args.budget,
                TokenCounter(args.model),
                args.top_k,
                include_diff=args.include_diff,
                include_log=args.include_log,
                include_globs=tuple(args.include_glob),
                exclude_globs=tuple(args.exclude_glob),
            )
        else:
            markdown, report = build_multi_repo_context_package(
                args.repo,
                args.task,
                args.budget,
                TokenCounter(args.model),
                args.top_k,
                include_diff=args.include_diff,
                include_log=args.include_log,
                include_globs=tuple(args.include_glob),
                exclude_globs=tuple(args.exclude_glob),
            )
        write_package_outputs(markdown, report, args.output, args.manifest)
        print(json.dumps(report, indent=2))
        if args.output:
            print(f"\nContext package written to {args.output.resolve()}")
        if args.manifest:
            print(f"Decision manifest written to {args.manifest.resolve()}")
        return

    _run_experiment(
        args.fixture,
        args.budget,
        args.model,
        trace_console=args.trace_console,
        output=args.output,
        summary=args.summary,
    )
