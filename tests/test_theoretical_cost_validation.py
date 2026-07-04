from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_theoretical_cost_validation_writes_report(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    output = tmp_path / "theoretical-input-cost-validation.json"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/theoretical_cost_validation.py",
            "--pricing",
            "artifacts/openai-pricing.json",
            "--output",
            str(output),
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Wrote" in completed.stdout
    report = json.loads(output.read_text(encoding="utf-8"))

    assert report["report_type"] == "theoretical_input_cost_validation"
    assert report["case_count"] == 30
    assert report["policy_totals"]["budgeted_cache_aligned"]["input_tokens"] < report["policy_totals"]["full_unaligned"]["input_tokens"]
    assert (
        report["comparisons_vs_full_unaligned"]["budgeted_cache_aligned"][
            "estimated_total_input_cost_saved_usd"
        ]
        > 0
    )
    assert (
        report["comparisons_vs_full_unaligned"]["full_cache_aligned"][
            "estimated_total_input_cost_saved_usd"
        ]
        > 0
    )
