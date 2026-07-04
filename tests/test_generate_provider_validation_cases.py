from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_generate_provider_validation_cases_writes_broad_fixture(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    output = tmp_path / "provider_validation_cases.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; "
                "import scripts.generate_provider_validation_cases as g; "
                f"g.OUTPUT_PATH = Path(r'{output}'); "
                "g.main()"
            ),
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Wrote 30 cases" in completed.stdout
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert len(payload["cases"]) == 30
    assert len({case["failure_type"] for case in payload["cases"]}) == 6
