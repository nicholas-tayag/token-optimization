from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from agenvantage.orchestrator import (
    build_development_plan,
    dispatch_worker_run,
    load_agent_roles,
    load_development_partition,
    prepare_worker_handoff,
    recommend_worker_route,
    review_worker_handoff,
    verify_worker_package,
)


def test_build_development_plan_marks_ready_wave_one_packages() -> None:
    partition = load_development_partition()
    plan = build_development_plan(partition, repo=Path("."))
    wave_one = next(item for item in plan["waves"] if item["wave_id"] == "W1")
    ready = [item for item in wave_one["packages"] if item["status"] == "ready"]
    assert {item["package_id"] for item in ready} >= {"W1.1", "W1.2", "W1.3"}


def test_build_development_plan_does_not_assume_ready_work_is_completed(
    tmp_path: Path,
) -> None:
    partition = {
        "waves": [
            {"wave_id": "W1", "packages": ["P1"]},
            {"wave_id": "W2", "packages": ["P2"]},
        ],
        "packages": [
            {"package_id": "P1", "role": "worker"},
            {"package_id": "P2", "role": "worker", "depends_on": ["P1"]},
        ],
    }

    plan = build_development_plan(partition, repo=tmp_path)

    assert plan["waves"][0]["packages"][0]["status"] == "ready"
    assert plan["waves"][1]["packages"][0]["status"] == "blocked"


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


def test_review_worker_handoff_uses_git_diff_and_independent_verifier(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "app.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=AgenVantage Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "initial",
        ],
        cwd=repo,
        check=True,
    )
    package = {
        "package_id": "W-git",
        "title": "Change app",
        "task_prompt": "Change the app value.",
        "allowed_paths": ["app.py"],
    }
    prepare_worker_handoff(repo, package, budget=800)
    source.write_text("VALUE = 2\n", encoding="utf-8")
    package_root = repo / ".agenvantage" / "orchestration" / "packages" / "W-git"
    (package_root / "worker-response.json").write_text(
        json.dumps(
            {
                "package_id": "W-git",
                "files_changed": ["app.py"],
                "tests_passed": True,
            }
        ),
        encoding="utf-8",
    )
    (package_root / "verifier.json").write_text(
        json.dumps({"package_id": "W-git", "tests_passed": True}),
        encoding="utf-8",
    )

    review = review_worker_handoff(repo, "W-git")

    assert review["status"] == "accepted"
    assert review["git_changed_paths"] == ["app.py"]


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
    result = dispatch_worker_run(
        repo,
        package,
        execute=False,
        worker_model="gpt-test-mini",
        worker_executable=sys.executable,
    )
    assert result["executed"] is False
    assert "agenvantage" in " ".join(result["command"])
    assert "worker placeholder" not in " ".join(result["command"])
    assert "--model gpt-test-mini" in " ".join(result["command"])
    assert result["worker_model"] == "gpt-test-mini"


def test_dispatch_worker_run_escalates_when_context_is_insufficient(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# Demo\n", encoding="utf-8")
    package = {
        "package_id": "W-docs",
        "title": "Update docs",
        "task_prompt": "Update the README guide.",
        "allowed_paths": ["README.md"],
    }

    result = dispatch_worker_run(repo, package, execute=False)

    assert result["worker_model"] == "gpt-5.6-sol"
    assert result["routing"]["model_tier"] == "high"
    assert result["routing"]["escalate"] is True
    assert any("context sufficiency" in reason for reason in result["routing"]["reasons"])
    assert result["routing"]["explicit_model_override"] is False


def test_agent_roles_config_has_all_routing_roles() -> None:
    roles = load_agent_roles()["roles"]

    assert set(roles) == {"manager", "worker", "verifier"}
    assert roles["worker"]["default_model"] == "gpt-5.6-terra"


def test_recommend_worker_route_uses_low_cost_model_for_bounded_docs() -> None:
    worker_role = load_agent_roles()["roles"]["worker"]
    route = recommend_worker_route(
        {
            "package_id": "W-docs",
            "role": "worker",
            "task_prompt": "Update the integration guide.",
            "allowed_paths": ["docs/integrations.md"],
        },
        worker_role,
    )

    assert route["model_tier"] == "low"
    assert route["model"] == "gpt-5.6-luna"
    assert route["reasoning_effort"] == "low"


def test_recommend_worker_route_escalates_security_work() -> None:
    worker_role = load_agent_roles()["roles"]["worker"]
    route = recommend_worker_route(
        {
            "package_id": "W-security",
            "role": "worker",
            "task_prompt": "Threat model secret handling across all services.",
            "allowed_paths": ["src/security.py"],
        },
        worker_role,
    )

    assert route["model_tier"] == "high"
    assert route["model"] == "gpt-5.6-sol"
    assert route["escalate"] is True


def test_verify_worker_package_runs_configured_command(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    package = {
        "package_id": "W-verify",
        "verification_command": f'{sys.executable} -c "print(123)"',
    }

    result = verify_worker_package(repo, package)

    assert result["tests_passed"] is True
    assert result["role"] == "verifier"
    assert "123" in result["stdout"]
    assert Path(result["output_path"]).is_file()
