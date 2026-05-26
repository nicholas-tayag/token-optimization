from __future__ import annotations

import argparse
import json
from pathlib import Path

from agenvantage.experiment import load_scenario, run_experiment
from agenvantage.telemetry import configure_console_tracing, flush_tracing
from agenvantage.tokenizer import TokenCounter


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
    return parser


def main() -> None:
    args = _parser().parse_args()
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

