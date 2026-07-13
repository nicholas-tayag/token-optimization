"""Manager/worker orchestration for hierarchical agent development."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from agenvantage.repo_context import build_context_package
from agenvantage.presets import get_preset
from agenvantage.tokenizer import TokenCounter

DEFAULT_PARTITION = Path(__file__).resolve().parents[2] / "examples" / "agent_development_partition.json"


def load_development_partition(path: Path | None = None) -> dict[str, Any]:
    partition_path = Path(path or DEFAULT_PARTITION)
    return json.loads(partition_path.read_text(encoding="utf-8"))


def _package_map(partition: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item["package_id"]): item for item in partition.get("packages", [])}


def build_development_plan(
    partition: dict[str, Any],
    *,
    repo: Path,
) -> dict[str, Any]:
    packages = _package_map(partition)
    completed: set[str] = set()
    waves_out: list[dict[str, Any]] = []
    for wave in partition.get("waves", []):
        wave_packages = []
        for package_id in wave.get("packages", []):
            package = packages.get(str(package_id))
            if package is None:
                continue
            depends = [str(item) for item in package.get("depends_on", [])]
            blocked_by = [item for item in depends if item not in completed]
            status = "ready" if not blocked_by else "blocked"
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
            if status == "ready" and package.get("role") == "worker":
                completed.add(str(package_id))
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
        "waves": waves_out,
        "manager_review_checklist": partition.get("manager_review_checklist", []),
    }


def orchestration_root(repo: Path) -> Path:
    return Path(repo).resolve() / ".agenvantage" / "orchestration"


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
) -> list[str]:
    _ = execute
    task = str(package.get("task_prompt") or package.get("title"))
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
        "--",
        "sh",
        "-c",
        "printf 'worker placeholder; implement package %s\\n' "
        f"{package['package_id']} && exit 0",
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
    if not payload.get("tests_passed"):
        findings.append("tests_not_passed")
    changed = [str(item) for item in payload.get("files_changed", [])]
    if allowed_paths and changed:
        for path in changed:
            if not any(path == allowed or path.endswith(allowed) for allowed in allowed_paths):
                findings.append(f"out_of_scope_file:{path}")
    status = "accepted" if not findings else "rejected"
    review = {
        "package_id": package_id,
        "status": status,
        "findings": findings,
        "allowed_paths": allowed_paths,
        "worker_response": payload,
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
) -> dict[str, Any]:
    worker_task = prepare_worker_handoff(repo, package)
    command = build_worker_agent_command(repo, package, worker_task=worker_task, execute=execute)
    result: dict[str, Any] = {
        "package_id": package["package_id"],
        "worker_task_path": str((orchestration_root(repo) / "packages" / package["package_id"] / "worker-task.json").resolve()),
        "command": command,
        "executed": False,
        "exit_code": None,
        "stdout": "",
        "stderr": "",
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
        "files_changed": [],
        "tests_run": "",
        "tests_passed": completed.returncode == 0,
        "risks": [],
        "manager_decision_needed": [],
    }
    response_path = orchestration_root(repo) / "packages" / package["package_id"] / "worker-response.json"
    response_path.write_text(json.dumps(response, indent=2) + "\n", encoding="utf-8")
    result["worker_response_path"] = str(response_path.resolve())
    return result


__all__ = [
    "build_development_plan",
    "build_worker_agent_command",
    "dispatch_worker_run",
    "load_development_partition",
    "orchestration_root",
    "prepare_worker_handoff",
    "review_worker_handoff",
]
