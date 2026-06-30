from __future__ import annotations

import argparse
import json
import webbrowser
from pathlib import Path

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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agenvantage")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="Compare context assembly policies.")
    run.add_argument("--fixture", type=Path, required=True, help="Scenario JSON file.")
    run.add_argument("--budget", type=int, required=True, help="Budgeted policy token cap.")
    run.add_argument("--model", default="gpt-4o-mini", help="Tokenizer model identifier.")
    run.add_argument("--output", type=Path, help="Optional JSON report path.")
    run.add_argument(
        "--trace-console",
        action="store_true",
        help="Print OpenTelemetry assembly spans to the console.",
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
    pack.add_argument("--output", type=Path, help="Optional Markdown context-package output.")
    pack.add_argument("--manifest", type=Path, help="Optional JSON decision-manifest output.")
    return parser


def _open_dashboard(report: Path | None, *, open_browser: bool) -> None:
    if not _DASHBOARD_PATH.is_file():
        raise SystemExit(f"Dashboard not found at {_DASHBOARD_PATH}")

    if report is not None:
        dashboard_uri = _DASHBOARD_PATH.resolve().as_uri()
        print(f"Open {_DASHBOARD_PATH}")
        print(f"Then load your report: {report.resolve()}")
        if open_browser:
            webbrowser.open(dashboard_uri)
        return

    if open_browser:
        webbrowser.open(_DASHBOARD_PATH.resolve().as_uri())
    else:
        print(_DASHBOARD_PATH.resolve())


def main() -> None:
    args = _parser().parse_args()
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
            )
        write_package_outputs(markdown, report, args.output, args.manifest)
        print(json.dumps(report, indent=2))
        if args.output:
            print(f"\nContext package written to {args.output.resolve()}")
        if args.manifest:
            print(f"Decision manifest written to {args.manifest.resolve()}")
        return

    if args.trace_console:
        configure_console_tracing()

    scenario = load_scenario(args.fixture)
    report = run_experiment(scenario, TokenCounter(args.model), args.budget)
    serialized = json.dumps(report, indent=2)
    print(serialized)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")

    flush_tracing()
