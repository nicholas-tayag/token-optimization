"""Commands for deterministic AgenVantage-to-agent wrappers."""

from __future__ import annotations

import shutil
from pathlib import Path


def resolve_agent_command(
    provider: str,
    *,
    repo: Path,
    executable: str | None = None,
    agent_model: str | None = None,
) -> list[str]:
    provider = provider.casefold()
    repo = Path(repo).resolve()
    if provider == "codex":
        if executable is not None:
            command = [executable]
        elif resolved := shutil.which("codex"):
            command = [resolved]
        elif npx := shutil.which("npx"):
            command = [npx, "-y", "@openai/codex"]
        else:
            raise FileNotFoundError(
                "Codex CLI is unavailable. Install @openai/codex or run `agenvantage doctor`."
            )
        command.extend(
            [
                "exec",
                "--cd",
                str(repo),
                "--sandbox",
                "workspace-write",
            ]
        )
        if agent_model:
            command.extend(["--model", agent_model])
        command.append("-")
        return command
    if provider == "claude":
        resolved = executable or shutil.which("claude")
        if not resolved:
            raise FileNotFoundError(
                "Claude Code is unavailable. Install the Claude CLI or run `agenvantage doctor`."
            )
        command = [
            resolved,
            "--print",
            "--permission-mode",
            "acceptEdits",
        ]
        if agent_model:
            command.extend(["--model", agent_model])
        return command
    raise ValueError("provider must be 'codex' or 'claude'")


__all__ = ["resolve_agent_command"]
