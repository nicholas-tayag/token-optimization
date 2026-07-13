"""Install AgenVantage's context skill for supported coding agents."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from importlib import resources
from pathlib import Path
from typing import Any, Literal

AgentTarget = Literal["cursor", "codex", "claude"]
SkillScope = Literal["personal", "project"]

SKILL_NAME = "agenvantage-context"
SUPPORTED_TARGETS: tuple[AgentTarget, ...] = ("cursor", "codex", "claude")
SUPPORTED_SCOPES: tuple[SkillScope, ...] = ("personal", "project")
_RESOURCE_PATH = ("resources", SKILL_NAME, "SKILL.md")
_TARGET_DIRECTORIES: dict[AgentTarget, str] = {
    "cursor": ".cursor",
    "codex": ".codex",
    "claude": ".claude",
}


def _validate_target(target: str) -> AgentTarget:
    if target not in SUPPORTED_TARGETS:
        choices = ", ".join(SUPPORTED_TARGETS)
        raise ValueError(f"Unsupported agent target {target!r}; expected one of: {choices}")
    return target  # type: ignore[return-value]


def _validate_scope(scope: str) -> SkillScope:
    if scope not in SUPPORTED_SCOPES:
        choices = ", ".join(SUPPORTED_SCOPES)
        raise ValueError(f"Unsupported skill scope {scope!r}; expected one of: {choices}")
    return scope  # type: ignore[return-value]


def bundled_agent_skill() -> str:
    """Return the canonical packaged skill template."""
    resource = resources.files("agenvantage")
    for part in _RESOURCE_PATH:
        resource = resource.joinpath(part)
    return resource.read_text(encoding="utf-8")


def rendered_agent_skill(command: str = "agenvantage") -> str:
    """Render the canonical skill with the exact requested command."""
    if not command:
        raise ValueError("command must not be empty")
    return bundled_agent_skill().replace("{{AGENVANTAGE_COMMAND}}", command)


def _project_root(start: Path) -> Path:
    root = start.resolve()
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return root
    if completed.returncode == 0 and completed.stdout.strip():
        return Path(completed.stdout.strip()).resolve()
    return root


def agent_skill_destination(
    target: AgentTarget | str,
    *,
    scope: SkillScope | str = "personal",
    project_root: Path | None = None,
    home: Path | None = None,
) -> Path:
    """Resolve a target's personal or project skill directory."""
    normalized_target = _validate_target(target)
    normalized_scope = _validate_scope(scope)
    base = (
        _project_root(Path(project_root or Path.cwd()))
        if normalized_scope == "project"
        else Path(home or Path.home()).expanduser().resolve()
    )
    return base / _TARGET_DIRECTORIES[normalized_target] / "skills" / SKILL_NAME


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def agent_skill_status(
    target: AgentTarget | str,
    *,
    scope: SkillScope | str = "personal",
    project_root: Path | None = None,
    home: Path | None = None,
    destination: Path | None = None,
    command: str = "agenvantage",
) -> dict[str, Any]:
    """Report whether a target's installed skill matches the packaged skill."""
    normalized_target = _validate_target(target)
    normalized_scope = _validate_scope(scope)
    resolved = Path(destination) if destination is not None else agent_skill_destination(
        normalized_target,
        scope=normalized_scope,
        project_root=project_root,
        home=home,
    )
    expected = rendered_agent_skill(command)
    base = {
        "target": normalized_target,
        "scope": normalized_scope,
        "destination": str(resolved),
    }
    if not resolved.exists():
        return {
            **base,
            "status": "missing",
            "installed": False,
            "current": False,
        }
    if not resolved.is_dir():
        return {
            **base,
            "status": "modified",
            "installed": True,
            "current": False,
            "reason": "destination is not a directory",
        }

    entries = sorted(path.name for path in resolved.iterdir())
    skill_path = resolved / "SKILL.md"
    if entries != ["SKILL.md"] or not skill_path.is_file():
        return {
            **base,
            "status": "modified",
            "installed": True,
            "current": False,
            "reason": "skill directory contains unexpected or missing files",
        }
    try:
        installed = skill_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return {
            **base,
            "status": "unreadable",
            "installed": True,
            "current": False,
            "reason": str(exc),
        }
    current = installed == expected
    return {
        **base,
        "status": "current" if current else "modified",
        "installed": True,
        "current": current,
        "bundled_sha256": _digest(expected),
        "installed_sha256": _digest(installed),
    }


def install_agent_skill(
    target: AgentTarget | str,
    *,
    scope: SkillScope | str = "personal",
    project_root: Path | None = None,
    home: Path | None = None,
    destination: Path | None = None,
    force: bool = False,
    command: str = "agenvantage",
) -> dict[str, Any]:
    """Install or update a target's skill without overwriting modifications."""
    normalized_target = _validate_target(target)
    normalized_scope = _validate_scope(scope)
    resolved = Path(destination) if destination is not None else agent_skill_destination(
        normalized_target,
        scope=normalized_scope,
        project_root=project_root,
        home=home,
    )
    status = agent_skill_status(
        normalized_target,
        scope=normalized_scope,
        destination=resolved,
        command=command,
    )
    if status["status"] == "current":
        return {**status, "changed": False}
    if status["installed"] and not force:
        raise FileExistsError(
            f"Refusing to overwrite modified {normalized_target} skill at {resolved}. "
            "Use force=True to replace it."
        )
    if resolved.exists():
        if resolved.is_dir():
            shutil.rmtree(resolved)
        else:
            resolved.unlink()
    resolved.mkdir(parents=True, exist_ok=True)
    (resolved / "SKILL.md").write_text(rendered_agent_skill(command), encoding="utf-8")
    return {
        **agent_skill_status(
            normalized_target,
            scope=normalized_scope,
            destination=resolved,
            command=command,
        ),
        "changed": True,
    }


def uninstall_agent_skill(
    target: AgentTarget | str,
    *,
    scope: SkillScope | str = "personal",
    project_root: Path | None = None,
    home: Path | None = None,
    destination: Path | None = None,
    force: bool = False,
    command: str = "agenvantage",
) -> dict[str, Any]:
    """Remove a target's skill without deleting modified content."""
    normalized_target = _validate_target(target)
    normalized_scope = _validate_scope(scope)
    resolved = Path(destination) if destination is not None else agent_skill_destination(
        normalized_target,
        scope=normalized_scope,
        project_root=project_root,
        home=home,
    )
    status = agent_skill_status(
        normalized_target,
        scope=normalized_scope,
        destination=resolved,
        command=command,
    )
    if not status["installed"]:
        return {**status, "changed": False}
    if not status["current"] and not force:
        raise FileExistsError(
            f"Refusing to remove modified {normalized_target} skill at {resolved}. "
            "Use force=True to remove it."
        )
    if resolved.is_dir():
        shutil.rmtree(resolved)
    else:
        resolved.unlink()
    return {
        "target": normalized_target,
        "scope": normalized_scope,
        "status": "missing",
        "destination": str(resolved),
        "installed": False,
        "current": False,
        "changed": True,
    }


__all__ = [
    "AgentTarget",
    "SKILL_NAME",
    "SUPPORTED_SCOPES",
    "SUPPORTED_TARGETS",
    "SkillScope",
    "agent_skill_destination",
    "agent_skill_status",
    "bundled_agent_skill",
    "install_agent_skill",
    "rendered_agent_skill",
    "uninstall_agent_skill",
]
