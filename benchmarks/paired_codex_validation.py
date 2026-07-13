from __future__ import annotations

import argparse
import json
from pathlib import Path

from agenvantage.paired_codex_validation import run_paired_codex_validation


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = REPO_ROOT / "examples" / "paired_codex_validation_cases.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "artifacts" / "paired-codex-validation"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate AgenVantage token savings with paired Codex control-vs-treatment runs."
        )
    )
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument(
        "--repos-root",
        type=Path,
        default=REPO_ROOT,
        help="Root used to resolve case repo_path values (default: repository root).",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--mode",
        choices=("local_only", "live"),
        default="local_only",
        help="local_only measures prompt compression; live runs paired Codex trajectories.",
    )
    parser.add_argument("--codex-executable", help="Optional Codex CLI path (defaults to codex or npx).")
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--max-cases", type=int)
    parser.add_argument(
        "--control-jsonl",
        type=Path,
        help="Import a saved control Codex --json JSONL log instead of running live control.",
    )
    parser.add_argument(
        "--treatment-jsonl",
        type=Path,
        help="Import a saved treatment Codex --json JSONL log instead of running live treatment.",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print only the aggregate/local prompt summary JSON.",
    )
    args = parser.parse_args()

    report = run_paired_codex_validation(
        fixture=args.fixture,
        repos_root=args.repos_root,
        output_dir=args.output_dir,
        mode=args.mode,
        codex_executable=args.codex_executable,
        timeout_seconds=args.timeout_seconds,
        max_cases=args.max_cases,
        control_jsonl=args.control_jsonl,
        treatment_jsonl=args.treatment_jsonl,
    )
    if args.summary:
        payload = {
            "mode": report["mode"],
            "output_paths": report["output_paths"],
            "cases": [
                {
                    "case_id": case["case_id"],
                    "local_prompt": case["local_prompt"],
                    "live_codex": case.get("live_codex"),
                    "treatment_delta_percent": case.get("treatment_delta_percent"),
                }
                for case in report["cases"]
            ],
            "aggregate": report.get("aggregate"),
        }
        print(json.dumps(payload, indent=2))
        return
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
