"""Paired Codex control-vs-AgenVantage validation harness."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agenvantage.presets import get_preset
from agenvantage.repo_context import build_context_package
from agenvantage.tokenizer import TokenCounter


@dataclass(frozen=True)
class PairedCodexCase:
    case_id: str
    repository: str
    repo_path: Path
    commit: str
    feature: str
    expected_edit_paths: tuple[str, ...]
    expected_test_paths: tuple[str, ...]
    acceptance_command: tuple[str, ...]
    repository_test_command: tuple[str, ...]
    budget: int
    tokenizer_model: str
    graph_backend: str


@dataclass(frozen=True)
class PairedCodexRunner:
    provider: str
    model: str
    ephemeral: bool
    sandbox: str


def load_paired_codex_dataset(path: Path) -> tuple[PairedCodexRunner, list[PairedCodexCase]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("paired Codex dataset must be a JSON object")
    runner_payload = payload.get("default_runner")
    if runner_payload is None:
        runner_payload = {}
    if not isinstance(runner_payload, dict):
        raise ValueError("default_runner must be a JSON object")
    runner = PairedCodexRunner(
        provider=str(runner_payload.get("provider") or "codex"),
        model=str(runner_payload.get("model") or "gpt-5.4-mini"),
        ephemeral=bool(runner_payload.get("ephemeral", True)),
        sandbox=str(runner_payload.get("sandbox") or "workspace-write"),
    )
    cases: list[PairedCodexCase] = []
    case_payloads = payload.get("cases", [])
    if not isinstance(case_payloads, list):
        raise ValueError("cases must be a JSON array")
    for item in case_payloads:
        if not isinstance(item, dict):
            raise ValueError("each paired Codex case must be a JSON object")
        cases.append(
            PairedCodexCase(
                case_id=str(item["case_id"]),
                repository=str(item["repository"]),
                repo_path=Path(item["repo_path"]),
                commit=str(item["commit"]),
                feature=str(item["feature"]),
                expected_edit_paths=tuple(item.get("expected_edit_paths") or ()),
                expected_test_paths=tuple(item.get("expected_test_paths") or ()),
                acceptance_command=tuple(item.get("acceptance_command") or ()),
                repository_test_command=tuple(item.get("repository_test_command") or ()),
                budget=int(item.get("budget") or 6000),
                tokenizer_model=str(item.get("tokenizer_model") or "gpt-4o-mini"),
                graph_backend=str(item.get("graph_backend") or "auto"),
            )
        )
    return runner, cases


def _resolve_case_repo(case: PairedCodexCase, repos_root: Path) -> Path:
    path = case.repo_path
    if path.is_absolute():
        return path.resolve()
    return (repos_root / path).resolve()


def _git_head(repo: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def _git_remote(repo: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else str(repo)


def build_adhoc_paired_case(
    repo: Path,
    task: str,
    *,
    case_id: str | None = None,
    budget: int = 6000,
    graph_backend: str = "auto",
    tokenizer_model: str = "gpt-4o-mini",
) -> PairedCodexCase:
    resolved = Path(repo).resolve()
    return PairedCodexCase(
        case_id=case_id or f"{resolved.name}-compare",
        repository=_git_remote(resolved),
        repo_path=resolved,
        commit=_git_head(resolved),
        feature=task,
        expected_edit_paths=(),
        expected_test_paths=(),
        acceptance_command=(),
        repository_test_command=(),
        budget=budget,
        tokenizer_model=tokenizer_model,
        graph_backend=graph_backend,
    )


def default_compare_output_dir(repo: Path) -> Path:
    return Path(repo).resolve() / ".agenvantage" / "compare"


def format_compare_summary(report: dict[str, Any]) -> str:
    lines = ["AgenVantage compare", ""]
    for case in report.get("cases", []):
        local = case["local_prompt"]
        lines.extend(
            [
                f"Repository: {case.get('repository')} @ {case.get('commit', '')[:12]}",
                f"Task: {case.get('feature')}",
                "",
                "First-request context:",
                f"  Full scan (counterfactual): {local['agenvantage_full_scan_counterfactual_tokens']:,} tokens",
                f"  AgenVantage handoff:        {local['agenvantage_handoff_tokens']:,} tokens",
                f"  Saved:                      {local['prompt_tokens_saved_vs_full_scan']:,} tokens ({local['prompt_reduction_percent_vs_full_scan']}%)",
                "",
            ]
        )
        selected = local.get("selected_paths") or []
        if selected:
            lines.append("Selected files:")
            for path in selected[:8]:
                lines.append(f"  - {path}")
            if len(selected) > 8:
                lines.append(f"  - ... and {len(selected) - 8} more")
            lines.append("")
        handoff = local.get("handoff_markdown_path")
        if handoff:
            lines.append(f"Handoff written to: {handoff}")
        live = case.get("live_codex") or {}
        lines.append(f"Live agent comparison: {live.get('status', 'not_run')}")
        if case.get("treatment_delta_percent"):
            delta = case["treatment_delta_percent"]
            lines.extend(
                [
                    "",
                    "Trajectory delta (treatment vs control):",
                    f"  Input tokens: {delta.get('input_tokens')}%",
                    f"  Output tokens: {delta.get('output_tokens')}%",
                ]
            )
        lines.append("")
    lines.extend(
        [
            "Use with any agent:",
            "  agenvantage codex \"<same task>\"",
            "  agenvantage claude \"<same task>\"",
            "",
            "Run live paired Codex validation:",
            "  agenvantage compare --task \"...\" --live",
            "",
            report.get("claim_boundary", {}).get("reason", ""),
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _recall(expected: tuple[str, ...], actual: list[str]) -> float:
    if not expected:
        return 1.0
    actual_set = set(actual)
    return len([path for path in expected if path in actual_set]) / len(expected)


def _surface_paths(report: dict[str, Any], key: str) -> list[str]:
    surface = report.get("change_surface") or {}
    return [
        str(item.get("relative_path") or item.get("path") or "")
        for item in surface.get(key, [])
        if item.get("relative_path") or item.get("path")
    ]


def _selected_paths(report: dict[str, Any]) -> list[str]:
    return sorted(
        {
            str(chunk.get("relative_path") or chunk.get("path") or "")
            for chunk in report.get("selected_chunks", [])
            if chunk.get("relative_path") or chunk.get("path")
        }
    )


def build_local_prompt_metrics(
    case: PairedCodexCase,
    *,
    repos_root: Path,
    top_k: int = 20,
) -> dict[str, Any]:
    repo = _resolve_case_repo(case, repos_root)
    if not repo.is_dir():
        raise FileNotFoundError(f"Repository not found for case {case.case_id}: {repo}")
    preset = get_preset("feature")
    counter = TokenCounter(case.tokenizer_model)
    markdown, report = build_context_package(
        repo,
        case.feature,
        case.budget,
        counter,
        top_k,
        instructions=preset.instructions,
        include_diff=preset.include_diff,
        include_log=preset.include_log,
        workflow="feature",
        graph_backend=case.graph_backend,
    )
    accounting = report.get("prompt_token_accounting") or {}
    full_scan = int(accounting.get("full_scan_prompt_tokens") or 0)
    packed = int(accounting.get("packed_prompt_tokens") or 0)
    saved = int(accounting.get("prompt_tokens_saved_vs_full_scan") or max(full_scan - packed, 0))
    reduction = float(accounting.get("prompt_reduction_percent_vs_full_scan") or 0.0)
    selected = _selected_paths(report)
    edit_paths = _surface_paths(report, "edit_targets")
    test_paths = _surface_paths(report, "test_targets")
    return {
        "repo": str(repo),
        "commit": case.commit,
        "handoff_markdown_path_hint": ".agenvantage/paired-validation/<case>/treatment-handoff.md",
        "handoff_markdown_bytes": len(markdown.encode("utf-8")),
        "agenvantage_handoff_tokens": packed,
        "agenvantage_full_scan_counterfactual_tokens": full_scan,
        "prompt_tokens_saved_vs_full_scan": saved,
        "prompt_reduction_percent_vs_full_scan": reduction,
        "selected_chunk_count": len(report.get("selected_chunks") or []),
        "selected_paths": selected,
        "change_surface": report.get("change_surface") or {},
        "graph": report.get("graph") or {},
        "grounding": {
            "expected_edit_paths": list(case.expected_edit_paths),
            "expected_test_paths": list(case.expected_test_paths),
            "edit_target_recall": _recall(case.expected_edit_paths, edit_paths),
            "test_target_recall": _recall(case.expected_test_paths, test_paths),
            "selected_edit_recall": _recall(case.expected_edit_paths, selected),
            "selected_test_recall": _recall(case.expected_test_paths, selected),
        },
        "experiments_compare": _experiment_variants(
            full_scan=full_scan,
            packed=packed,
            original_user=int(accounting.get("original_user_prompt_tokens") or 0),
        ),
        "report": report,
        "markdown": markdown,
    }


def _experiment_variants(
    *,
    full_scan: int,
    packed: int,
    original_user: int,
) -> list[dict[str, Any]]:
    stable_cache = max(packed - original_user, 0)
    return [
        {
            "variant_id": "full_scan",
            "label": "Control-style full scan (counterfactual)",
            "prompt_tokens": full_scan,
            "tokens_saved_vs_full_scan": 0,
            "reduction_percent_vs_full_scan": 0.0,
            "cache_eligible_input_tokens": 0,
        },
        {
            "variant_id": "packed_text",
            "label": "AgenVantage treatment handoff",
            "prompt_tokens": packed,
            "tokens_saved_vs_full_scan": max(full_scan - packed, 0),
            "reduction_percent_vs_full_scan": round(
                (max(full_scan - packed, 0) / full_scan) * 100, 2
            )
            if full_scan
            else 0.0,
            "cache_eligible_input_tokens": stable_cache,
        },
    ]


def parse_codex_jsonl_usage(text: str) -> dict[str, Any]:
    totals = {
        "turn_count": 0,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "uncached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
    }
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_type = str(event.get("type") or event.get("event") or "")
        if event_type not in {"turn.completed", "response.completed"}:
            continue
        usage = event.get("usage") or {}
        if not usage and isinstance(event.get("data"), dict):
            usage = event["data"].get("usage") or {}
        if not usage:
            continue
        totals["turn_count"] += 1
        input_tokens = _usage_token_count(
            usage.get("input_tokens")
            or usage.get("prompt_tokens")
            or usage.get("total_input_tokens")
            or 0
        )
        cached = _usage_token_count(
            usage.get("cached_input_tokens")
            or usage.get("cache_read_input_tokens")
            or (usage.get("input_tokens_details") or {}).get("cached_tokens")
            or (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
            or 0
        )
        output_tokens = _usage_token_count(
            usage.get("output_tokens") or usage.get("completion_tokens") or 0
        )
        reasoning = _usage_token_count(
            usage.get("reasoning_output_tokens")
            or usage.get("reasoning_tokens")
            or (usage.get("output_tokens_details") or {}).get("reasoning_tokens")
            or 0
        )
        totals["input_tokens"] += input_tokens
        totals["cached_input_tokens"] += cached
        totals["output_tokens"] += output_tokens
        totals["reasoning_output_tokens"] += reasoning
    totals["uncached_input_tokens"] = max(
        totals["input_tokens"] - totals["cached_input_tokens"], 0
    )
    return totals


def _usage_token_count(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, float) and not value.is_integer():
        return 0
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _codex_exec_prefix(codex_executable: str | None = None) -> list[str]:
    if codex_executable and codex_executable not in {"npx", "auto"}:
        return [codex_executable]
    if resolved := shutil.which("codex"):
        return [resolved]
    if shutil.which("npx"):
        return ["npx", "-y", "@openai/codex"]
    raise FileNotFoundError(
        "Codex CLI is unavailable. Install @openai/codex or pass --codex-executable."
    )


def _codex_base_command(
    runner: PairedCodexRunner,
    repo: Path,
    *,
    codex_executable: str | None = None,
) -> list[str]:
    command = [
        *_codex_exec_prefix(codex_executable),
        "exec",
        "--cd",
        str(repo),
        "--sandbox",
        runner.sandbox,
    ]
    if runner.model:
        command.extend(["--model", runner.model])
    if runner.ephemeral:
        command.append("--ephemeral")
    command.append("--json")
    return command


def _run_command(
    command: list[str],
    *,
    cwd: Path,
    input_text: str | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    started = time.time()
    completed = subprocess.run(
        command,
        cwd=cwd,
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout_seconds,
    )
    wall_time_seconds = round(time.time() - started, 2)
    usage = parse_codex_jsonl_usage(completed.stdout)
    return {
        "command": command,
        "exit_code": completed.returncode,
        "wall_time_seconds": wall_time_seconds,
        "stdout_path_hint": "stdout captured in artifact jsonl file",
        "stderr_tail": completed.stderr[-4000:],
        **usage,
    }


def _run_tests(repo: Path, command: tuple[str, ...]) -> dict[str, Any]:
    if not command:
        return {"skipped": True}
    completed = subprocess.run(
        list(command),
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    passed = failed = skipped = 0
    for line in completed.stdout.splitlines():
        lowered = line.lower()
        if " passed" in lowered and " in " in lowered:
            parts = line.replace(",", "").split()
            for index, token in enumerate(parts):
                if token == "passed":
                    try:
                        passed = int(parts[index - 1])
                    except (IndexError, ValueError):
                        pass
                if token == "failed":
                    try:
                        failed = int(parts[index - 1])
                    except (IndexError, ValueError):
                        pass
                if token == "skipped":
                    try:
                        skipped = int(parts[index - 1])
                    except (IndexError, ValueError):
                        pass
    return {
        "command": list(command),
        "exit_code": completed.returncode,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "stdout_tail": completed.stdout[-2000:],
        "stderr_tail": completed.stderr[-2000:],
    }


def _git_diff_sha256(repo: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), "diff", "--no-ext-diff"],
        capture_output=True,
        text=True,
        check=False,
    )
    payload = completed.stdout.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def run_codex_control(
    case: PairedCodexCase,
    runner: PairedCodexRunner,
    *,
    repo: Path,
    codex_executable: str | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    command = _codex_base_command(runner, repo, codex_executable=codex_executable)
    command.append(case.feature)
    record = _run_command(command, cwd=repo, timeout_seconds=timeout_seconds)
    acceptance = _run_tests(repo, case.acceptance_command)
    repository_tests = _run_tests(repo, case.repository_test_command)
    record["external_acceptance"] = {
        "passed": 1 if acceptance.get("exit_code") == 0 else 0,
        "failed": 0 if acceptance.get("exit_code") == 0 else 1,
    }
    record["repository_tests"] = {
        "passed": repository_tests.get("passed", 0),
        "failed": repository_tests.get("failed", 0),
        "skipped": repository_tests.get("skipped", 0),
    }
    record["task_success"] = (
        acceptance.get("exit_code") == 0 and repository_tests.get("exit_code") == 0
    )
    record["diff_sha256"] = _git_diff_sha256(repo)
    return record


def run_codex_treatment(
    case: PairedCodexCase,
    runner: PairedCodexRunner,
    *,
    repo: Path,
    handoff_markdown: str,
    codex_executable: str | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    command = _codex_base_command(runner, repo, codex_executable=codex_executable)
    command.append("-")
    record = _run_command(
        command,
        cwd=repo,
        input_text=handoff_markdown,
        timeout_seconds=timeout_seconds,
    )
    acceptance = _run_tests(repo, case.acceptance_command)
    repository_tests = _run_tests(repo, case.repository_test_command)
    record["external_acceptance"] = {
        "passed": 1 if acceptance.get("exit_code") == 0 else 0,
        "failed": 0 if acceptance.get("exit_code") == 0 else 1,
    }
    record["repository_tests"] = {
        "passed": repository_tests.get("passed", 0),
        "failed": repository_tests.get("failed", 0),
        "skipped": repository_tests.get("skipped", 0),
    }
    record["task_success"] = (
        acceptance.get("exit_code") == 0 and repository_tests.get("exit_code") == 0
    )
    record["diff_sha256"] = _git_diff_sha256(repo)
    return record


def _percent_delta(control: float | int, treatment: float | int) -> float | None:
    if not control:
        return None
    return round(((float(treatment) - float(control)) / float(control)) * 100, 2)


def compute_treatment_delta(
    control: dict[str, Any],
    treatment: dict[str, Any],
) -> dict[str, float | None]:
    fields = (
        "wall_time_seconds",
        "input_tokens",
        "cached_input_tokens",
        "uncached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
    )
    return {
        field: _percent_delta(control.get(field, 0), treatment.get(field, 0))
        for field in fields
    }


def _aggregate_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    def total(variant: str, field: str) -> int | float:
        return sum(float(case.get(variant, {}).get(field) or 0) for case in cases)

    control_successes = sum(bool(case.get("control", {}).get("task_success")) for case in cases)
    treatment_successes = sum(bool(case.get("treatment", {}).get("task_success")) for case in cases)
    control_input = int(total("control", "input_tokens"))
    treatment_input = int(total("treatment", "input_tokens"))
    return {
        "case_count": len(cases),
        "control_successes": control_successes,
        "treatment_successes": treatment_successes,
        "control_success_rate": round(control_successes / len(cases), 4) if cases else 0.0,
        "treatment_success_rate": round(treatment_successes / len(cases), 4) if cases else 0.0,
        "control_input_tokens": control_input,
        "treatment_input_tokens": treatment_input,
        "raw_input_reduction_percent": _percent_delta(control_input, treatment_input),
        "control_cached_input_tokens": int(total("control", "cached_input_tokens")),
        "treatment_cached_input_tokens": int(total("treatment", "cached_input_tokens")),
        "control_uncached_input_tokens": int(total("control", "uncached_input_tokens")),
        "treatment_uncached_input_tokens": int(total("treatment", "uncached_input_tokens")),
        "control_output_tokens": int(total("control", "output_tokens")),
        "treatment_output_tokens": int(total("treatment", "output_tokens")),
        "control_reasoning_output_tokens": int(total("control", "reasoning_output_tokens")),
        "treatment_reasoning_output_tokens": int(total("treatment", "reasoning_output_tokens")),
        "control_wall_time_seconds": round(total("control", "wall_time_seconds"), 2),
        "treatment_wall_time_seconds": round(total("treatment", "wall_time_seconds"), 2),
    }


def render_paired_codex_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Paired Codex Validation",
        "",
        f"Generated: {report.get('generated_at')}",
        f"Mode: {report.get('mode')}",
        "",
        "## Summary",
        "",
    ]
    if report.get("mode") == "local_only":
        lines.extend(
            [
                "This run measured local AgenVantage prompt compression and grounding.",
                "Live Codex trajectories were not executed.",
                "",
            ]
        )
    aggregate = report.get("aggregate") or {}
    if aggregate:
        lines.extend(
            [
                "| Metric | Control | Treatment | Treatment change |",
                "| --- | ---: | ---: | ---: |",
                f"| Input tokens | {aggregate.get('control_input_tokens', 'n/a')} | {aggregate.get('treatment_input_tokens', 'n/a')} | {aggregate.get('raw_input_reduction_percent', 'n/a')}% |",
                f"| Cached input | {aggregate.get('control_cached_input_tokens', 'n/a')} | {aggregate.get('treatment_cached_input_tokens', 'n/a')} | |",
                f"| Output tokens | {aggregate.get('control_output_tokens', 'n/a')} | {aggregate.get('treatment_output_tokens', 'n/a')} | |",
                f"| Wall time (s) | {aggregate.get('control_wall_time_seconds', 'n/a')} | {aggregate.get('treatment_wall_time_seconds', 'n/a')} | |",
                "",
            ]
        )
    for case in report.get("cases", []):
        lines.extend(
            [
                f"## Case `{case['case_id']}`",
                "",
                f"Repository: `{case.get('repository')}` @ `{case.get('commit')}`",
                f"Feature: {case.get('feature')}",
                "",
                "### Local prompt metrics (AgenVantage)",
                "",
                f"- Full-scan counterfactual: **{case['local_prompt']['agenvantage_full_scan_counterfactual_tokens']:,}** tokens",
                f"- Treatment handoff: **{case['local_prompt']['agenvantage_handoff_tokens']:,}** tokens",
                f"- Saved vs full scan: **{case['local_prompt']['prompt_tokens_saved_vs_full_scan']:,}** tokens ({case['local_prompt']['prompt_reduction_percent_vs_full_scan']}%)",
                f"- Edit-target recall: {case['local_prompt']['grounding']['edit_target_recall']}",
                f"- Test-target recall: {case['local_prompt']['grounding']['test_target_recall']}",
                "",
            ]
        )
        selected = case["local_prompt"].get("selected_paths") or []
        if selected:
            lines.append("Selected paths:")
            lines.extend(f"- `{path}`" for path in selected[:12])
            if len(selected) > 12:
                lines.append(f"- ... and {len(selected) - 12} more")
            lines.append("")
        live = case.get("live_codex") or {}
        lines.append(f"Live Codex status: **{live.get('status', 'not_run')}**")
        if live.get("reason"):
            lines.append(f"Reason: {live['reason']}")
        lines.append("")
        if case.get("control") and case.get("treatment"):
            delta = case.get("treatment_delta_percent") or {}
            control = case["control"]
            treatment = case["treatment"]
            lines.extend(
                [
                    "### Live trajectory comparison",
                    "",
                    "| Metric | Control | Treatment | Delta % |",
                    "| --- | ---: | ---: | ---: |",
                    f"| Input tokens | {control.get('input_tokens', 0):,} | {treatment.get('input_tokens', 0):,} | {delta.get('input_tokens')}% |",
                    f"| Cached input | {control.get('cached_input_tokens', 0):,} | {treatment.get('cached_input_tokens', 0):,} | {delta.get('cached_input_tokens')}% |",
                    f"| Output tokens | {control.get('output_tokens', 0):,} | {treatment.get('output_tokens', 0):,} | {delta.get('output_tokens')}% |",
                ]
            )
            if "wall_time_seconds" in control or "wall_time_seconds" in treatment:
                lines.append(
                    f"| Wall time (s) | {control.get('wall_time_seconds', 'n/a')} | {treatment.get('wall_time_seconds', 'n/a')} | {delta.get('wall_time_seconds')}% |"
                )
            if "task_success" in control or "task_success" in treatment:
                lines.append(
                    f"| Task success | {control.get('task_success', 'n/a')} | {treatment.get('task_success', 'n/a')} | |"
                )
            lines.append("")
    lines.extend(
        [
            "## Claim boundary",
            "",
            report.get("claim_boundary", {}).get("reason", ""),
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _prepare_output_dir(output_dir: Path, case_id: str) -> Path:
    case_dir = output_dir / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    return case_dir


def _codex_availability(codex_executable: str | None = None) -> tuple[bool, str | None]:
    if codex_executable:
        return Path(codex_executable).exists() or shutil.which(codex_executable) is not None, codex_executable
    if resolved := shutil.which("codex"):
        return True, resolved
    if npx := shutil.which("npx"):
        return True, npx
    return False, None


def run_paired_codex_validation(
    *,
    fixture: Path | None = None,
    adhoc_cases: list[PairedCodexCase] | None = None,
    default_runner: PairedCodexRunner | None = None,
    repos_root: Path | None = None,
    output_dir: Path,
    mode: str = "local_only",
    codex_executable: str | None = None,
    timeout_seconds: int | None = None,
    max_cases: int | None = None,
    control_jsonl: Path | None = None,
    treatment_jsonl: Path | None = None,
) -> dict[str, Any]:
    if adhoc_cases:
        runner = default_runner or PairedCodexRunner(
            provider="codex",
            model="gpt-5.4-mini",
            ephemeral=True,
            sandbox="workspace-write",
        )
        cases = adhoc_cases
        repos_root = repos_root or Path(".")
    elif fixture is not None:
        runner, cases = load_paired_codex_dataset(fixture)
        repos_root = repos_root or Path(".")
    else:
        raise ValueError("Provide either fixture or adhoc_cases.")
    if max_cases is not None:
        cases = cases[:max_cases]
    output_dir.mkdir(parents=True, exist_ok=True)
    report_cases: list[dict[str, Any]] = []
    codex_available, resolved_executable = _codex_availability(codex_executable)

    for case in cases:
        repo = _resolve_case_repo(case, repos_root)
        case_dir = _prepare_output_dir(output_dir, case.case_id)
        local = build_local_prompt_metrics(case, repos_root=repos_root)
        handoff_path = case_dir / "treatment-handoff.md"
        manifest_path = case_dir / "treatment-manifest.json"
        handoff_path.write_text(local.pop("markdown"), encoding="utf-8")
        manifest_path.write_text(json.dumps(local.pop("report"), indent=2) + "\n", encoding="utf-8")
        local["handoff_markdown_path"] = str(handoff_path.resolve())
        local["manifest_path"] = str(manifest_path.resolve())

        case_record: dict[str, Any] = {
            "case_id": case.case_id,
            "repository": case.repository,
            "commit": case.commit,
            "feature": case.feature,
            "local_prompt": {
                key: value
                for key, value in local.items()
                if key not in {"report", "markdown"}
            },
            "live_codex": {"status": "not_run"},
        }

        if mode == "live":
            try:
                case_record["control"] = run_codex_control(
                    case,
                    runner,
                    repo=repo,
                    codex_executable=codex_executable or resolved_executable,
                    timeout_seconds=timeout_seconds,
                )
                case_record["treatment"] = run_codex_treatment(
                    case,
                    runner,
                    repo=repo,
                    handoff_markdown=handoff_path.read_text(encoding="utf-8"),
                    codex_executable=codex_executable or resolved_executable,
                    timeout_seconds=timeout_seconds,
                )
                case_record["treatment_delta_percent"] = compute_treatment_delta(
                    case_record["control"],
                    case_record["treatment"],
                )
                case_record["live_codex"] = {"status": "completed"}
            except FileNotFoundError as exc:
                case_record["live_codex"] = {"status": "blocked", "reason": str(exc)}
            except subprocess.TimeoutExpired:
                case_record["live_codex"] = {
                    "status": "blocked",
                    "reason": f"Codex run exceeded timeout of {timeout_seconds}s.",
                }
        elif control_jsonl and treatment_jsonl:
            control_text = control_jsonl.read_text(encoding="utf-8")
            treatment_text = treatment_jsonl.read_text(encoding="utf-8")
            case_record["control"] = parse_codex_jsonl_usage(control_text)
            case_record["treatment"] = parse_codex_jsonl_usage(treatment_text)
            case_record["treatment_delta_percent"] = compute_treatment_delta(
                case_record["control"],
                case_record["treatment"],
            )
            case_record["live_codex"] = {
                "status": "imported",
                "control_jsonl": str(control_jsonl.resolve()),
                "treatment_jsonl": str(treatment_jsonl.resolve()),
            }

        report_cases.append(case_record)

    aggregate = _aggregate_cases(report_cases) if any(
        case.get("control") and case.get("treatment") for case in report_cases
    ) else {}
    report = {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%d"),
        "mode": mode if mode == "live" else ("imported" if control_jsonl else "local_only"),
        "runner": {
            "name": runner.provider,
            "model": runner.model,
            "ephemeral": runner.ephemeral,
        },
        "cases": report_cases,
        "aggregate": aggregate,
        "claim_boundary": {
            "proves_general_token_savings": bool(aggregate),
            "proves_quality_retention": bool(aggregate),
            "proves_provider_cost_savings": False,
            "reason": (
                "Local prompt compression proves first-request context reduction only. "
                "Live paired Codex trajectories are required to claim end-to-end agent token savings."
                if not aggregate
                else "Paired live trajectories were recorded for the selected cases, but sample size may still be too small for general claims."
            ),
        },
    }
    json_path = output_dir / "paired-codex-validation.json"
    md_path = output_dir / "paired-codex-validation.md"
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_paired_codex_markdown(report), encoding="utf-8")
    report["output_paths"] = {
        "json": str(json_path.resolve()),
        "markdown": str(md_path.resolve()),
    }
    return report


def codex_jsonl_to_provider_records(
    text: str,
    *,
    trace_id: str | None = None,
    provider: str = "codex",
    model: str = "",
    policy_id: str = "trajectory",
) -> list[dict[str, Any]]:
    usage = parse_codex_jsonl_usage(text)
    if usage["turn_count"] == 0 and usage["input_tokens"] == 0:
        return []
    return [
        {
            "trace_id": trace_id,
            "provider": provider,
            "model": model,
            "policy_id": policy_id,
            "input_tokens": usage["input_tokens"],
            "cached_input_tokens": usage["cached_input_tokens"],
            "output_tokens": usage["output_tokens"],
            "reasoning_output_tokens": usage["reasoning_output_tokens"],
            "turn_count": usage["turn_count"],
            "source": "codex_jsonl",
        }
    ]


def run_paired_codex_live_capture(
    *,
    repo: Path,
    task: str,
    output_dir: Path,
    runner: PairedCodexRunner | None = None,
    codex_executable: str | None = None,
    timeout_seconds: int | None = None,
    reset_repo: bool = False,
) -> dict[str, Any]:
    repo = Path(repo).resolve()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    case = build_adhoc_paired_case(repo, task)
    runner = runner or PairedCodexRunner(
        provider="codex",
        model="gpt-5.4-mini",
        ephemeral=True,
        sandbox="workspace-write",
    )
    local = build_local_prompt_metrics(case, repos_root=repo.parent)
    handoff_path = output_dir / "treatment-handoff.md"
    handoff_path.write_text(local.pop("markdown"), encoding="utf-8")

    control_jsonl = output_dir / "control.jsonl"
    treatment_jsonl = output_dir / "treatment.jsonl"
    control_record = run_codex_control(
        case,
        runner,
        repo=repo,
        codex_executable=codex_executable,
        timeout_seconds=timeout_seconds,
    )
    control_jsonl.write_text(
        json.dumps({"type": "turn.completed", "usage": {
            "input_tokens": control_record.get("input_tokens", 0),
            "cached_input_tokens": control_record.get("cached_input_tokens", 0),
            "output_tokens": control_record.get("output_tokens", 0),
            "reasoning_output_tokens": control_record.get("reasoning_output_tokens", 0),
        }}) + "\n",
        encoding="utf-8",
    )
    if reset_repo:
        subprocess.run(["git", "-C", str(repo), "reset", "--hard"], check=False)
        subprocess.run(["git", "-C", str(repo), "clean", "-fd"], check=False)
    treatment_record = run_codex_treatment(
        case,
        runner,
        repo=repo,
        handoff_markdown=handoff_path.read_text(encoding="utf-8"),
        codex_executable=codex_executable,
        timeout_seconds=timeout_seconds,
    )
    treatment_jsonl.write_text(
        json.dumps({"type": "turn.completed", "usage": {
            "input_tokens": treatment_record.get("input_tokens", 0),
            "cached_input_tokens": treatment_record.get("cached_input_tokens", 0),
            "output_tokens": treatment_record.get("output_tokens", 0),
            "reasoning_output_tokens": treatment_record.get("reasoning_output_tokens", 0),
        }}) + "\n",
        encoding="utf-8",
    )
    report = run_paired_codex_validation(
        adhoc_cases=[case],
        repos_root=repo.parent,
        output_dir=output_dir,
        mode="local_only",
        control_jsonl=control_jsonl,
        treatment_jsonl=treatment_jsonl,
    )
    report["capture_paths"] = {
        "control_jsonl": str(control_jsonl.resolve()),
        "treatment_jsonl": str(treatment_jsonl.resolve()),
        "handoff": str(handoff_path.resolve()),
    }
    report["control"] = control_record
    report["treatment"] = treatment_record
    return report


__all__ = [
    "PairedCodexCase",
    "PairedCodexRunner",
    "build_adhoc_paired_case",
    "build_local_prompt_metrics",
    "compute_treatment_delta",
    "codex_jsonl_to_provider_records",
    "default_compare_output_dir",
    "format_compare_summary",
    "load_paired_codex_dataset",
    "parse_codex_jsonl_usage",
    "render_paired_codex_markdown",
    "run_codex_control",
    "run_codex_treatment",
    "run_paired_codex_live_capture",
    "run_paired_codex_validation",
]
