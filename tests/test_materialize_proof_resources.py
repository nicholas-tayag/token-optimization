from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_materialize_proof_resources_dry_run_writes_index(tmp_path: Path) -> None:
    dest = tmp_path / "proof-resources"
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/materialize_proof_resources.py",
            "--dry-run",
            "--github-only",
            "--claim",
            "solved_agent_context_overload_end_to_end",
            "--dest",
            str(dest),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Selected" in completed.stdout
    assert "dry-run" in completed.stdout

    index = json.loads((dest / "selected-resources.json").read_text(encoding="utf-8"))
    assert index["resources"]
    assert all(item["type"] == "github_repo" for item in index["resources"])
    assert (dest / "README.md").is_file()
