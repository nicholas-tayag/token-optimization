"""Manager/worker orchestration for hierarchical agent development."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from agenvantage.agent_launcher import resolve_agent_command
from agenvantage.model_routing import RoutingDecision, route_task
from agenvantage.repo_context import build_context_package
from agenvantage.presets import get_preset
from agenvantage.tokenizer import TokenCounter

DEFAULT_PARTITION = Path(__file__).resolve().parents[2] / "examples" / "agent_development_partition.json"
DEFAULT_ROLES = Path(__file__).resolve().parents[2] / "examples" / "agent_roles.json"


def load_development_partition(path: Path | None = None) -> dict[str, Any]:
    partition_path = Path(path or DEFAULT_PARTITION)
    return json.loads(partition_path.read_text(encoding="utf-8"))


def load_agent_roles(path: Path | None = None) -> dict[str, Any]:
    payload = json.loads(Path(path or DEFAULT_ROLES).read_text(encoding="utf-8"))
    if not isinstance(payload.get("roles"), dict):
        raise ValueError("Agent role config requires a roles object.")
    return payload


def recommend_worker_route(
    package: dict[str, Any],
    worker_role: dict[str, Any],
) -> dict[str, Any]:
    """Choose a configured worker model from deterministic package signals."""

    task = str(package.get("task_prompt") or package.get("title") or package.get("package_id"))
    allowed_paths = [str(path) for path in package.get("allowed_paths", [])]
    routing_task = task
    if allowed_paths and all(path.startswith("docs/") or path == "README.md" for path in allowed_paths):
        routing_task += " documentation"
    elif package.get("role") == "verifier":
        routing_task += " tests verification"
    elif 0 < len(allowed_paths) <= 2:
        routing_task += " isolated single module"

    decision: RoutingDecision = route_task(
        routing_task,
        failed_attempts=int(package.get("failed_attempts", 0) or 0),
    )
    models_by_tier = worker_role.get("models_by_tier") or {}
    model = models_by_tier.get(decision.model_tier) or worker_role.get("default_model")
    result = {
        **decision.to_dict(),
        "provider": worker_role.get("provider") or "codex",
        "model": model,
        "explicit_model_override": False,
    }
    sufficiency = package.get("context_sufficiency") or {}
    confidence = float(sufficiency.get("confidence", 1.0) or 0.0)
    status = str(sufficiency.get("status", "sufficient"))
    if result["model_tier"] == "low" and (status != "sufficient" or confidence < 0.9):
        result.update(
            {
                "model_tier": "high",
                "estimated_relative_cost_tier": "high",
                "reasoning_effort": "high",
                "escalate": True,
                "model": models_by_tier.get("high") or worker_role.get("default_model"),
                "reasons": [
                    *result["reasons"],
                    f"context sufficiency {status} at {confidence:.2f} requires escalation",
                ],
            }
        )
    return result


def _package_map(partition: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item["package_id"]): item for item in partition.get("packages", [])}


def _completed_packages(repo: Path) -> set[str]:
    packages_root = orchestration_root(repo) / "packages"
    if not packages_root.is_dir():
        return set()
    completed: set[str] = set()
    for package_root in packages_root.iterdir():
        review_path = package_root / "manager-review.json"
        verifier_path = package_root / "verifier.json"
        try:
            if review_path.is_file():
                review = json.loads(review_path.read_text(encoding="utf-8"))
                if review.get("status") == "accepted":
                    completed.add(package_root.name)
                    continue
            if verifier_path.is_file():
                verifier = json.loads(verifier_path.read_text(encoding="utf-8"))
                if verifier.get("status") == "passed":
                    completed.add(package_root.name)
        except (OSError, json.JSONDecodeError):
            continue
    return completed


def build_development_plan(
    partition: dict[str, Any],
    *,
    repo: Path,
) -> dict[str, Any]:
    packages = _package_map(partition)
    completed = _completed_packages(repo)
    waves_out: list[dict[str, Any]] = []
    for wave in partition.get("waves", []):
        wave_packages = []
        for package_id in wave.get("packages", []):
            package = packages.get(str(package_id))
            if package is None:
                continue
            depends = [str(item) for item in package.get("depends_on", [])]
            blocked_by = [item for item in depends if item not in completed]
            status = "completed" if str(package_id) in completed else (
                "ready" if not blocked_by else "blocked"
            )
            wave_packages.append(
                {
                    "package_id": package_id,
                    "title": package.get("title"),
                    "role": package.get("role"),
                    "status": status,
                    "blocked_by": blocked_by,
                    "allowed_paths": package.get("allowed_paths", []),
                }
            )
        waves_out.append(
            {
                "wave_id": wave.get("wave_id"),
                "name": wave.get("name"),
                "parallel": bool(wave.get("parallel")),
                "packages": wave_packages,
            }
        )
    return {
        "schema_version": partition.get("schema_version", 1),
        "prd": partition.get("prd"),
        "repo": str(Path(repo).resolve()),
        "north_star_metric": partition.get("north_star_metric"),
        "completed_packages": sorted(completed),
        "waves": waves_out,
        "manager_review_checklist": partition.get("manager_review_checklist", []),
    }


def orchestration_root(repo: Path) -> Path:
    return Path(repo).resolve() / ".agenvantage" / "orchestration"


def _git_changed_paths(repo: Path) -> set[str] | None:
    repo = Path(repo).resolve()
    commands = (
        ("git", "diff", "--name-only", "-z"),
        ("git", "diff", "--cached", "--name-only", "-z"),
        ("git", "ls-files", "--others", "--exclude-standard", "-z"),
    )
    changed: set[str] = set()
    for command in commands:
        completed = subprocess.run(
            command,
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            return None
        changed.update(
            path
            for path in completed.stdout.split("\0")
            if path and not path.startswith(".agenvantage/")
        )
    return changed


def prepare_worker_handoff(
    repo: Path,
    package: dict[str, Any],
    *,
    budget: int = 6000,
    top_k: int = 20,
) -> dict[str, Any]:
    repo = Path(repo).resolve()
    task = str(package.get("task_prompt") or package.get("title") or package.get("package_id"))
    preset = get_preset("feature")
    counter = TokenCounter()
    markdown, report = build_context_package(
        repo,
        task,
        budget,
        counter,
        top_k,
        instructions=preset.instructions,
        include_diff=preset.include_diff,
        include_log=preset.include_log,
        workflow="feature",
        graph_backend="auto",
    )
    package_id = str(package["package_id"])
    root = orchestration_root(repo)
    case_dir = root / "packages" / package_id
    case_dir.mkdir(parents=True, exist_ok=True)
    handoff_path = case_dir / "handoff.md"
    manifest_path = case_dir / "manifest.json"
    task_path = case_dir / "worker-task.json"
    handoff_path.write_text(markdown, encoding="utf-8")
    manifest_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    accounting = report.get("prompt_token_accounting") or {}
    worker_task = {
        "package_id": package_id,
        "title": package.get("title"),
        "role": package.get("role", "worker"),
        "task_prompt": task,
        "allowed_paths": package.get("allowed_paths", []),
        "acceptance": package.get("acceptance", []),
        "handoff_markdown_path": str(handoff_path.resolve()),
        "manifest_path": str(manifest_path.resolve()),
        "packed_prompt_tokens": accounting.get("packed_prompt_tokens"),
        "full_scan_prompt_tokens": accounting.get("full_scan_prompt_tokens"),
        "context_sufficiency": report.get("context_sufficiency", {}),
        "baseline_changed_paths": sorted(_git_changed_paths(repo) or ()),
        "manager_review_checklist": [],
    }
    task_path.write_text(json.dumps(worker_task, indent=2) + "\n", encoding="utf-8")
    return worker_task


def build_worker_agent_command(
    repo: Path,
    package: dict[str, Any],
    *,
    worker_task: dict[str, Any],
    execute: bool = False,
    worker_provider: str = "codex",
    worker_model: str | None = None,
    worker_executable: str | None = None,
) -> list[str]:
    _ = execute
    task = str(package.get("task_prompt") or package.get("title"))
    agent_command = resolve_agent_command(
        worker_provider,
        repo=repo,
        executable=worker_executable,
        agent_model=worker_model,
    )
    return [
        sys.executable,
        "-m",
        "agenvantage",
        "agent",
        "run",
        "--task",
        task,
        "--repo",
        str(Path(repo).resolve()),
        "--handoff-file",
        worker_task["handoff_markdown_path"],
        "--role",
        "worker",
        *(["--agent-model", worker_model] if worker_model else []),
        "--",
        *agent_command,
    ]


def review_worker_handoff(
    repo: Path,
    package_id: str,
    *,
    handoff_json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = orchestration_root(repo) / "packages" / package_id
    task_path = root / "worker-task.json"
    response_path = root / "worker-response.json"
    verifier_path = root / "verifier.json"
    package_task = json.loads(task_path.read_text(encoding="utf-8")) if task_path.is_file() else {}
    allowed_paths = [str(item) for item in package_task.get("allowed_paths", [])]
    payload = handoff_json or {}
    if response_path.is_file() and not payload:
        payload = json.loads(response_path.read_text(encoding="utf-8"))

    findings: list[str] = []
    if not payload:
        findings.append("missing_worker_response")
    if payload.get("package_id") != package_id:
        findings.append("package_id_mismatch")
    verifier = (
        json.loads(verifier_path.read_text(encoding="utf-8"))
        if verifier_path.is_file()
        else {}
    )
    if not verifier:
        findings.append("missing_verifier_artifact")
    elif not verifier.get("tests_passed"):
        findings.append("independent_verification_failed")

    baseline_changed = set(str(item) for item in package_task.get("baseline_changed_paths", []))
    current_changed = _git_changed_paths(repo)
    reported_changed = {str(item) for item in payload.get("files_changed", [])}
    if current_changed is None:
        findings.append("git_diff_unavailable")
        changed = sorted(reported_changed)
    else:
        changed = sorted(current_changed - baseline_changed)
        if reported_changed != set(changed):
            findings.append("worker_report_diff_mismatch")
        for path in sorted(baseline_changed.intersection(allowed_paths)):
            findings.append(f"ambiguous_preexisting_change:{path}")
    if allowed_paths and changed:
        for path in changed:
            if path not in allowed_paths:
                findings.append(f"out_of_scope_file:{path}")
    status = "accepted" if not findings else "rejected"
    review = {
        "package_id": package_id,
        "status": status,
        "findings": findings,
        "allowed_paths": allowed_paths,
        "git_changed_paths": changed,
        "worker_response": payload,
        "verifier": verifier,
        "manager_review_checklist": package_task.get("manager_review_checklist", []),
    }
    (root / "manager-review.json").write_text(json.dumps(review, indent=2) + "\n", encoding="utf-8")
    return review


def dispatch_worker_run(
    repo: Path,
    package: dict[str, Any],
    *,
    execute: bool = False,
    python_executable: str | None = None,
    worker_provider: str = "codex",
    worker_model: str | None = None,
    worker_executable: str | None = None,
    routing: dict[str, Any] | None = None,
    worker_role: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _ = python_executable
    worker_task = prepare_worker_handoff(repo, package)
    if routing is None:
        configured_role = worker_role or load_agent_roles()["roles"].get("worker", {})
        routing_package = {
            **package,
            "context_sufficiency": worker_task.get("context_sufficiency", {}),
        }
        routing = recommend_worker_route(routing_package, configured_role)
        if worker_model is not None:
            routing["model"] = worker_model
            routing["explicit_model_override"] = True
        else:
            worker_model = routing.get("model")
        if worker_provider == "codex" and configured_role.get("provider"):
            worker_provider = str(routing["provider"])
    command = build_worker_agent_command(
        repo,
        package,
        worker_task=worker_task,
        execute=execute,
        worker_provider=worker_provider,
        worker_model=worker_model,
        worker_executable=worker_executable,
    )
    result: dict[str, Any] = {
        "package_id": package["package_id"],
        "worker_task_path": str((orchestration_root(repo) / "packages" / package["package_id"] / "worker-task.json").resolve()),
        "command": command,
        "executed": False,
        "exit_code": None,
        "stdout": "",
        "stderr": "",
        "worker_provider": worker_provider,
        "worker_model": worker_model,
        "routing": routing,
    }
    if not execute:
        return result
    completed = subprocess.run(
        command,
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    result.update(
        {
            "executed": True,
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    )
    response = {
        "package_id": package["package_id"],
        "status": "completed" if completed.returncode == 0 else "failed",
        "files_changed": sorted(_git_changed_paths(repo) or ()),
        "tests_run": "",
        "tests_passed": False,
        "risks": ["Independent package verification has not run."],
        "manager_decision_needed": [],
    }
    response_path = orchestration_root(repo) / "packages" / package["package_id"] / "worker-response.json"
    response_path.write_text(json.dumps(response, indent=2) + "\n", encoding="utf-8")
    result["worker_response_path"] = str(response_path.resolve())
    return result


def verify_worker_package(repo: Path, package: dict[str, Any]) -> dict[str, Any]:
    repo = Path(repo).resolve()
    package_id = str(package["package_id"])
    configured = package.get("verification_command")
    if configured:
        command = shlex.split(str(configured))
    else:
        test_paths = [
            str(path)
            for path in package.get("allowed_paths", [])
            if str(path).startswith("tests/")
        ]
        local_pytest = repo / ".venv" / "bin" / "pytest"
        command = [str(local_pytest) if local_pytest.is_file() else "pytest", "-q", *test_paths]
    completed = subprocess.run(command, cwd=repo, capture_output=True, text=True, check=False)
    result = {
        "package_id": package_id,
        "role": "verifier",
        "status": "passed" if completed.returncode == 0 else "failed",
        "tests_passed": completed.returncode == 0,
        "command": command,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    output = orchestration_root(repo) / "packages" / package_id / "verifier.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    result["output_path"] = str(output.resolve())
    return result


__all__ = [
    "build_development_plan",
    "build_worker_agent_command",
    "dispatch_worker_run",
    "load_agent_roles",
    "load_development_partition",
    "orchestration_root",
    "prepare_worker_handoff",
    "review_worker_handoff",
    "verify_worker_package",
]
