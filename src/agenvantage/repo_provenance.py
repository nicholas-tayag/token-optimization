from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agenvantage.tokenizer import TokenCounter


@dataclass(frozen=True)
class ProvenanceSection:
    section_id: str
    repo_label: str
    repo_path: str
    kind: str
    text: str
    tokens: int

    def render(self) -> str:
        heading = f"### {self.repo_label} {self.kind.replace('_', ' ').title()}"
        body = self.text.rstrip()
        if self.kind == "git_diff":
            return f"{heading}\n\n```diff\n{body}\n```"
        return f"{heading}\n\n```text\n{body}\n```"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.section_id,
            "repo_label": self.repo_label,
            "repo_path": self.repo_path,
            "kind": self.kind,
            "tokens": self.tokens,
        }


def _run_git(repo: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _render_section(
    *,
    repo: Path,
    repo_label: str,
    kind: str,
    text: str,
    counter: TokenCounter,
) -> ProvenanceSection | None:
    normalized = text.strip()
    if not normalized:
        return None
    section_id = f"{repo_label}:{kind}"
    section = ProvenanceSection(
        section_id=section_id,
        repo_label=repo_label,
        repo_path=str(repo.resolve()),
        kind=kind,
        text=normalized,
        tokens=0,
    )
    rendered = section.render()
    return ProvenanceSection(
        section_id=section.section_id,
        repo_label=section.repo_label,
        repo_path=section.repo_path,
        kind=section.kind,
        text=section.text,
        tokens=counter.count(rendered),
    )


def build_repo_provenance_sections(
    repo: Path,
    repo_label: str,
    *,
    counter: TokenCounter,
    include_diff: bool,
    include_log: bool,
    log_commits: int = 5,
) -> tuple[ProvenanceSection, ...]:
    repo = repo.resolve()
    sections: list[ProvenanceSection] = []
    if include_diff:
        staged = _run_git(repo, ["diff", "--cached", "--no-ext-diff", "--", "."])
        unstaged = _run_git(repo, ["diff", "--no-ext-diff", "--", "."])
        diff_parts = []
        if staged:
            diff_parts.append("# Staged changes\n\n" + staged)
        if unstaged:
            diff_parts.append("# Working tree changes\n\n" + unstaged)
        section = _render_section(
            repo=repo,
            repo_label=repo_label,
            kind="git_diff",
            text="\n\n".join(diff_parts),
            counter=counter,
        )
        if section is not None:
            sections.append(section)
    if include_log:
        log_text = _run_git(
            repo,
            [
                "log",
                f"-n{log_commits}",
                "--date=short",
                "--pretty=format:%h %ad %s",
                "--stat",
                "--",
                ".",
            ],
        )
        section = _render_section(
            repo=repo,
            repo_label=repo_label,
            kind="git_log",
            text=log_text,
            counter=counter,
        )
        if section is not None:
            sections.append(section)
    return tuple(sections)
