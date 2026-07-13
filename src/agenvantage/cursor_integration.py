"""Backward-compatible Cursor wrappers for the shared agent skill installer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .agent_integrations import (
    SKILL_NAME,
    agent_skill_destination,
    agent_skill_status,
    bundled_agent_skill,
    install_agent_skill,
    rendered_agent_skill,
    uninstall_agent_skill,
)


def bundled_cursor_skill() -> str:
    return bundled_agent_skill()


def rendered_cursor_skill(command: str = "agenvantage") -> str:
    return rendered_agent_skill(command)


def cursor_skill_destination(
    *,
    project: bool = False,
    project_root: Path | None = None,
    home: Path | None = None,
) -> Path:
    return agent_skill_destination(
        "cursor",
        scope="project" if project else "personal",
        project_root=project_root,
        home=home,
    )


def cursor_skill_status(
    destination: Path,
    *,
    command: str = "agenvantage",
) -> dict[str, Any]:
    status = agent_skill_status("cursor", destination=destination, command=command)
    return {key: value for key, value in status.items() if key not in {"target", "scope"}}


def install_cursor_skill(
    destination: Path,
    *,
    force: bool = False,
    command: str = "agenvantage",
) -> dict[str, Any]:
    status = install_agent_skill(
        "cursor",
        destination=destination,
        force=force,
        command=command,
    )
    return {key: value for key, value in status.items() if key not in {"target", "scope"}}


def uninstall_cursor_skill(
    destination: Path,
    *,
    force: bool = False,
    command: str = "agenvantage",
) -> dict[str, Any]:
    status = uninstall_agent_skill(
        "cursor",
        destination=destination,
        force=force,
        command=command,
    )
    return {key: value for key, value in status.items() if key not in {"target", "scope"}}


__all__ = [
    "SKILL_NAME",
    "bundled_cursor_skill",
    "cursor_skill_destination",
    "cursor_skill_status",
    "install_cursor_skill",
    "rendered_cursor_skill",
    "uninstall_cursor_skill",
]
