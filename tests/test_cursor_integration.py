from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agenvantage.cursor_integration import (
    SKILL_NAME,
    bundled_cursor_skill,
    cursor_skill_destination,
    cursor_skill_status,
    install_cursor_skill,
    uninstall_cursor_skill,
)


def test_bundled_skill_is_automatic_and_graph_backend_enabled() -> None:
    skill = bundled_cursor_skill()

    assert "name: agenvantage-context" in skill
    assert "disable-model-invocation" not in skill
    assert "--graph-backend auto" in skill
    assert "--handoff-json" in skill
    assert len(skill.splitlines()) < 500


def test_install_status_and_uninstall_are_idempotent(tmp_path: Path) -> None:
    destination = tmp_path / SKILL_NAME

    installed = install_cursor_skill(destination)
    unchanged = install_cursor_skill(destination)

    assert installed["changed"] is True
    assert unchanged["changed"] is False
    assert cursor_skill_status(destination)["status"] == "current"
    assert uninstall_cursor_skill(destination)["changed"] is True
    assert uninstall_cursor_skill(destination)["changed"] is False


def test_modified_skill_requires_force(tmp_path: Path) -> None:
    destination = tmp_path / SKILL_NAME
    install_cursor_skill(destination)
    (destination / "SKILL.md").write_text("custom instructions\n", encoding="utf-8")

    with pytest.raises(FileExistsError):
        install_cursor_skill(destination)
    with pytest.raises(FileExistsError):
        uninstall_cursor_skill(destination)

    replaced = install_cursor_skill(destination, force=True)
    assert replaced["status"] == "current"
    (destination / "SKILL.md").write_text("custom instructions\n", encoding="utf-8")
    assert uninstall_cursor_skill(destination, force=True)["changed"] is True


def test_personal_destination_uses_cursor_skills_directory(tmp_path: Path) -> None:
    assert cursor_skill_destination(home=tmp_path) == (
        tmp_path / ".cursor" / "skills" / SKILL_NAME
    )


def test_cursor_cli_install_status_and_uninstall(tmp_path: Path) -> None:
    env = {**os.environ, "HOME": str(tmp_path)}
    base = [sys.executable, "-m", "agenvantage", "cursor"]

    installed = subprocess.run(
        [*base, "install", "--json"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    status = subprocess.run(
        [*base, "status", "--json"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    removed = subprocess.run(
        [*base, "uninstall", "--json"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert json.loads(installed.stdout)["current"] is True
    assert json.loads(status.stdout)["status"] == "current"
    assert json.loads(removed.stdout)["status"] == "missing"
