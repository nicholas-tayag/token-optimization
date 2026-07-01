from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_claim_status_script_reports_resume_claim_boundaries(tmp_path: Path) -> None:
    output_json = tmp_path / "claim-status.json"
    output_md = tmp_path / "claim-status.md"

    completed = subprocess.run(
        [
            sys.executable,
            "benchmarks/claim_status.py",
            "--output-json",
            str(output_json),
            "--output-md",
            str(output_md),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "JSON report written to" in completed.stdout
    report = json.loads(output_json.read_text(encoding="utf-8"))
    markdown = output_md.read_text(encoding="utf-8")

    assert report["narrow_supported_claim"]["supported"] is True
    assert (
        report["resume_claims"]["proved_latency_improvements_in_production"]["supported"]
        is False
    )
    assert "AgenVantage Claim Status" in markdown
    assert "solved_agent_context_overload_end_to_end" in markdown
