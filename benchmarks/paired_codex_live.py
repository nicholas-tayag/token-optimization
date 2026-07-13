from __future__ import annotations

import argparse
import json
from pathlib import Path

from agenvantage.paired_codex_validation import run_paired_codex_live_capture


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "artifacts" / "paired-codex-live"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run paired Codex control/treatment capture and write JSONL artifacts."
    )
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--task", required=True)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--codex-executable")
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument(
        "--reset-repo",
        action="store_true",
        help="Reset the repository between control and treatment runs.",
    )
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()

    report = run_paired_codex_live_capture(
        repo=args.repo,
        task=args.task,
        output_dir=args.output_dir,
        codex_executable=args.codex_executable,
        timeout_seconds=args.timeout_seconds,
        reset_repo=args.reset_repo,
    )
    if args.summary:
        payload = {
            "mode": report.get("mode"),
            "capture_paths": report.get("capture_paths"),
            "aggregate": report.get("aggregate"),
            "treatment_delta_percent": (
                report.get("cases", [{}])[0].get("treatment_delta_percent")
                if report.get("cases")
                else None
            ),
        }
        print(json.dumps(payload, indent=2))
        return
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
