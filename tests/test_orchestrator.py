from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from agenvantage.orchestrator import (
    build_development_plan,
    dispatch_worker_run,
    load_development_partition,
    prepare_worker_handoff,
    review_worker_handoff,
)


def test_build_development_plan_marks_ready_wave_one_packages() -> None:
    partition = load_development_partition()
    plan = build_development_plan(partition, repo=Path("."))
    wave_one = next(item for item in plan["waves"] if item["wave_id"] == "W1")
    ready = [item for item in wave_one["packages"] if item["status"] == "ready"]
    assert {item["package_id"] for item in ready} >= {"W1.1", "W1.2", "W1.3"}


def test_prepare_worker_handoff_writes_task_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")
    package = {
        "package_id": "W-test",
        "title": "Explain main()",
        "task_prompt": "Explain main() and identify likely edit files.",
        "allowed_paths": ["main.py"],
    }
    worker_task = prepare_worker_handoff(repo, package, budget=1200)

    assert worker_task["packed_prompt_tokens"] > 0
    assert Path(worker_task["handoff_markdown_path"]).is_file()
    assert Path(worker_task["manifest_path"]).is_file()


def test_review_worker_handoff_rejects_out_of_scope_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "foo.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    package = {
        "package_id": "W-test",
        "title": "Test package",
        "task_prompt": "Do work",
        "allowed_paths": ["src/foo.py"],
    }
    prepare_worker_handoff(repo, package, budget=800)
    response_path = repo / ".agenvantage" / "orchestration" / "packages" / "W-test" / "worker-response.json"
    response_path.parent.mkdir(parents=True, exist_ok=True)
    response_path.write_text(
        json.dumps(
            {
                "package_id": "W-test",
                "status": "completed",
                "files_changed": ["README.md"],
                "tests_passed": True,
            }
        ),
        encoding="utf-8",
    )
    review = review_worker_handoff(repo, "W-test")
    assert review["status"] == "rejected"
    assert any("out_of_scope_file" in item for item in review["findings"])


def test_orchestrate_plan_cli(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    partition = tmp_path / "partition.json"
    partition.write_text(
        json.dumps(
            {
                "waves": [{"wave_id": "W1", "packages": ["P1"]}],
                "packages": [
                    {
                        "package_id": "P1",
                        "role": "worker",
                        "title": "Demo",
                        "task_prompt": "Explain repo",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "plan.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agenvantage",
            "orchestrate",
            "plan",
            "--partition",
            str(partition),
            "--repo",
            str(repo),
            "--output",
            str(output),
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    payload = json.loads(completed.stdout)
    assert payload["waves"][0]["wave_id"] == "W1"
    assert output.is_file()


def test_dispatch_worker_run_dry_run(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    package = {
        "package_id": "W-dry",
        "title": "Explain run",
        "task_prompt": "Explain run()",
        "allowed_paths": ["app.py"],
    }
    result = dispatch_worker_run(repo, package, execute=False)
    assert result["executed"] is False
    assert "agenvantage" in " ".join(result["command"])
