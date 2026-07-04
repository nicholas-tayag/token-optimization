from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Iterable

from agenvantage.repo_context import build_multi_repo_context_package
from agenvantage.tokenizer import TokenCounter


SESSION_VERSION = 1
DEFAULT_MINIMUM_CACHEABLE_PREFIX_TOKENS = 1024
_TASK_MARKER = "## Task\n\n"


def _stable_session_parts(markdown: str, task: str) -> tuple[str, str]:
    if _TASK_MARKER not in markdown:
        stable_prefix = markdown.rstrip()
    else:
        head, tail = markdown.split(_TASK_MARKER, 1)
        if tail.startswith(task):
            remainder = tail[len(task) :].lstrip("\n")
        else:
            parts = tail.split("\n\n", 1)
            remainder = parts[1] if len(parts) == 2 else tail
        stable_prefix = (
            head.rstrip()
            + "\n\n"
            + "## Session Context\n\n"
            + "The repository context below is the stable prefix for this feature session. "
            + "Keep it byte-for-byte identical across follow-up requests to preserve provider prompt-cache eligibility.\n\n"
            + remainder.strip()
        )
    dynamic_packet = build_dynamic_task_packet(task)
    return stable_prefix.rstrip() + "\n", dynamic_packet


def _prompt(stable_prefix: str, dynamic_packet: str) -> str:
    return stable_prefix.rstrip() + "\n\n" + dynamic_packet.rstrip() + "\n"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _session_id(repo_paths: Iterable[Path], task: str, stable_prefix: str) -> str:
    payload = json.dumps(
        {
            "repos": [str(path.resolve()) for path in repo_paths],
            "task": task,
            "stable_prefix_hash": _sha256(stable_prefix),
        },
        sort_keys=True,
    )
    return f"feature-{hashlib.sha1(payload.encode('utf-8')).hexdigest()[:12]}"


def default_session_path(repo: Path, session_id: str) -> Path:
    return repo.resolve() / ".agenvantage" / "sessions" / f"{session_id}.json"


def build_dynamic_task_packet(task: str) -> str:
    task = task.strip()
    if not task:
        raise ValueError("Task must not be empty.")
    return (
        "## Current Task\n\n"
        f"{task}\n\n"
        "## Response Guidance\n\n"
        "- Use the stable repository context above before asking for more files.\n"
        "- Identify edit files, tests to update, and any missing signals.\n"
        "- Do not claim runtime validation unless it has actually been run.\n"
    )


def create_feature_session(
    repos: Iterable[Path],
    task: str,
    budget: int,
    counter: TokenCounter,
    *,
    top_k: int,
    instructions: str,
    include_diff: bool = False,
    include_log: bool = False,
    include_globs: tuple[str, ...] = (),
    exclude_globs: tuple[str, ...] = (),
    session_id: str | None = None,
    minimum_cacheable_prefix_tokens: int = DEFAULT_MINIMUM_CACHEABLE_PREFIX_TOKENS,
) -> dict[str, Any]:
    repo_paths = tuple(repos)
    markdown, pack_report = build_multi_repo_context_package(
        repo_paths,
        task,
        budget,
        counter,
        top_k=top_k,
        instructions=instructions,
        include_diff=include_diff,
        include_log=include_log,
        include_globs=include_globs,
        exclude_globs=exclude_globs,
        workflow="feature",
    )
    stable_prefix, dynamic_packet = _stable_session_parts(markdown, task)
    stable_prefix = stable_prefix.rstrip()
    resolved_session_id = session_id or _session_id(repo_paths, task, stable_prefix)
    stable_prefix_tokens = counter.count(stable_prefix)
    dynamic_packet_tokens = counter.count(dynamic_packet)
    initial_prompt = _prompt(stable_prefix, dynamic_packet)
    initial_prompt_tokens = counter.count(initial_prompt)
    return {
        "version": SESSION_VERSION,
        "workflow": "feature_session",
        "session_id": resolved_session_id,
        "created_at_unix_s": int(time.time()),
        "initial_task": task,
        "repo_paths": [str(path.resolve()) for path in repo_paths],
        "settings": {
            "budget": budget,
            "top_k": top_k,
            "model": counter.model,
            "encoding": counter.encoding_name,
            "include_diff": include_diff,
            "include_log": include_log,
            "include_globs": list(include_globs),
            "exclude_globs": list(exclude_globs),
        },
        "cache": {
            "minimum_cacheable_prefix_tokens": minimum_cacheable_prefix_tokens,
            "stable_prefix_tokens": stable_prefix_tokens,
            "cache_eligible": stable_prefix_tokens >= minimum_cacheable_prefix_tokens,
            "stable_prefix_sha256": _sha256(stable_prefix),
            "initial_dynamic_packet_tokens": dynamic_packet_tokens,
            "initial_prompt_tokens": initial_prompt_tokens,
            "initial_reusable_prefix_percent": round(
                (stable_prefix_tokens / initial_prompt_tokens) * 100,
                2,
            )
            if initial_prompt_tokens
            else 0.0,
        },
        "change_surface": pack_report.get("change_surface", {}),
        "selected_chunks": pack_report.get("selected_chunks", []),
        "safety": pack_report.get("safety", {}),
        "prompt_token_accounting": pack_report.get("prompt_token_accounting", {}),
        "stable_prefix_markdown": stable_prefix,
        "initial_dynamic_packet_markdown": dynamic_packet,
        "initial_prompt_markdown": initial_prompt,
    }


def build_session_task(
    session: dict[str, Any],
    task: str,
    counter: TokenCounter,
) -> dict[str, Any]:
    stable_prefix = str(session.get("stable_prefix_markdown", "")).strip()
    if not stable_prefix:
        raise ValueError("Session artifact is missing stable_prefix_markdown.")
    dynamic_packet = build_dynamic_task_packet(task)
    prompt_markdown = _prompt(stable_prefix, dynamic_packet)
    stable_prefix_tokens = counter.count(stable_prefix)
    dynamic_packet_tokens = counter.count(dynamic_packet)
    prompt_tokens = counter.count(prompt_markdown)
    minimum_cacheable_prefix_tokens = int(
        (session.get("cache") or {}).get(
            "minimum_cacheable_prefix_tokens",
            DEFAULT_MINIMUM_CACHEABLE_PREFIX_TOKENS,
        )
    )
    return {
        "version": SESSION_VERSION,
        "workflow": "feature_session_task",
        "session_id": session.get("session_id"),
        "task": task,
        "cache": {
            "minimum_cacheable_prefix_tokens": minimum_cacheable_prefix_tokens,
            "stable_prefix_tokens": stable_prefix_tokens,
            "dynamic_packet_tokens": dynamic_packet_tokens,
            "prompt_tokens": prompt_tokens,
            "cache_eligible": stable_prefix_tokens >= minimum_cacheable_prefix_tokens,
            "stable_prefix_sha256": _sha256(stable_prefix),
            "reusable_prefix_percent": round(
                (stable_prefix_tokens / prompt_tokens) * 100,
                2,
            )
            if prompt_tokens
            else 0.0,
            "estimated_uncached_tokens_after_cache_hit": dynamic_packet_tokens,
            "estimated_reusable_tokens_after_cache_hit": stable_prefix_tokens,
        },
        "change_surface": session.get("change_surface", {}),
        "selected_chunks": session.get("selected_chunks", []),
        "safety": session.get("safety", {}),
        "dynamic_packet_markdown": dynamic_packet,
        "prompt_markdown": prompt_markdown,
    }


def save_session_artifact(session: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(session, indent=2) + "\n", encoding="utf-8")


def load_session_artifact(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if int(payload.get("version", 0)) != SESSION_VERSION:
        raise ValueError(f"Unsupported session artifact version in {path}.")
    if payload.get("workflow") != "feature_session":
        raise ValueError(f"Unsupported session workflow in {path}.")
    return payload
