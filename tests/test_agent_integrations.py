from __future__ import annotations

from pathlib import Path

import pytest

from agenvantage.agent_integrations import (
    SKILL_NAME,
    SUPPORTED_TARGETS,
    agent_skill_destination,
    agent_skill_status,
    install_agent_skill,
    rendered_agent_skill,
    uninstall_agent_skill,
)


@pytest.mark.parametrize(
    ("target", "directory"),
    [("cursor", ".cursor"), ("codex", ".codex"), ("claude", ".claude")],
)
def test_personal_and_project_destinations(
    tmp_path: Path,
    target: str,
    directory: str,
) -> None:
    assert agent_skill_destination(target, home=tmp_path) == (
        tmp_path.resolve() / directory / "skills" / SKILL_NAME
    )
    project = tmp_path / "project"
    project.mkdir()
    assert agent_skill_destination(
        target,
        scope="project",
        project_root=project,
    ) == project.resolve() / directory / "skills" / SKILL_NAME


@pytest.mark.parametrize("target", SUPPORTED_TARGETS)
@pytest.mark.parametrize("scope", ["personal", "project"])
def test_install_status_uninstall_lifecycle(
    tmp_path: Path,
    target: str,
    scope: str,
) -> None:
    kwargs = (
        {"home": tmp_path}
        if scope == "personal"
        else {"project_root": tmp_path}
    )

    missing = agent_skill_status(target, scope=scope, **kwargs)
    installed = install_agent_skill(target, scope=scope, **kwargs)
    unchanged = install_agent_skill(target, scope=scope, **kwargs)
    removed = uninstall_agent_skill(target, scope=scope, **kwargs)
    absent = uninstall_agent_skill(target, scope=scope, **kwargs)

    assert missing["status"] == "missing"
    assert installed["status"] == "current"
    assert installed["changed"] is True
    assert unchanged["changed"] is False
    assert removed["changed"] is True
    assert absent["changed"] is False


@pytest.mark.parametrize("target", SUPPORTED_TARGETS)
def test_rendering_embeds_exact_command_and_canonical_behavior(
    tmp_path: Path,
    target: str,
) -> None:
    command = "/opt/Agen Vantage/bin/agenvantage"
    result = install_agent_skill(target, home=tmp_path, command=command)
    skill = (Path(result["destination"]) / "SKILL.md").read_text(encoding="utf-8")

    assert f"{command} pack \\" in skill
    assert "{{AGENVANTAGE_COMMAND}}" not in skill
    assert "--graph-backend auto" in skill
    assert "feature`, `debug`, `review`, or `explain`" in skill
    assert "trivial one-line edits" in skill
    assert "already been generated" in skill
    assert "falls back to baseline retrieval" in skill
    assert "disable-model-invocation" not in skill
    assert skill == rendered_agent_skill(command)


def test_modified_or_extra_files_require_force(tmp_path: Path) -> None:
    result = install_agent_skill("codex", home=tmp_path)
    destination = Path(result["destination"])
    (destination / "NOTES.md").write_text("keep me\n", encoding="utf-8")

    assert agent_skill_status("codex", home=tmp_path)["status"] == "modified"
    with pytest.raises(FileExistsError):
        install_agent_skill("codex", home=tmp_path)
    with pytest.raises(FileExistsError):
        uninstall_agent_skill("codex", home=tmp_path)

    replaced = install_agent_skill("codex", home=tmp_path, force=True)
    assert replaced["status"] == "current"
    (destination / "SKILL.md").write_text("custom\n", encoding="utf-8")
    assert uninstall_agent_skill("codex", home=tmp_path, force=True)["changed"] is True


@pytest.mark.parametrize(
    ("argument", "value"),
    [("target", "unknown"), ("scope", "workspace")],
)
def test_invalid_target_and_scope_are_rejected(argument: str, value: str) -> None:
    kwargs = {argument: value}
    if argument == "target":
        with pytest.raises(ValueError):
            agent_skill_destination(**kwargs)
    else:
        with pytest.raises(ValueError):
            agent_skill_destination("cursor", **kwargs)
