"""Task presets for developer-focused context packing.

Each preset bundles a default instruction block with sensible provenance
defaults so a developer can pick an intent (``explain``, ``review``,
``debug``, ``change``, ``compare``) instead of remembering individual flags.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaskPreset:
    name: str
    summary: str
    instructions: str
    include_diff: bool = False
    include_log: bool = False


_EXPLAIN = TaskPreset(
    name="explain",
    summary="Explain existing behavior from repository excerpts.",
    instructions=(
        "You are helping a developer understand existing code. Base every "
        "conclusion on the provided repository excerpts, cite source chunk "
        "identifiers when explaining behavior, and state clearly when the "
        "provided context is insufficient rather than inventing files, tests, "
        "or runtime results."
    ),
)

_REVIEW = TaskPreset(
    name="review",
    summary="Review a change for correctness, edge cases, and risk.",
    instructions=(
        "You are reviewing code for correctness, edge cases, and risk. Base "
        "findings on the provided excerpts and any diff, cite the chunk "
        "identifiers that support each point, call out missing tests or "
        "unhandled cases, and flag when you need more context instead of "
        "guessing."
    ),
    include_diff=True,
)

_DEBUG = TaskPreset(
    name="debug",
    summary="Localize a bug using excerpts, diff, and recent commits.",
    instructions=(
        "You are diagnosing a bug. Use the provided excerpts, diff, and recent "
        "commits to localize the most likely cause, cite chunk identifiers as "
        "evidence for each step, propose the smallest safe fix, and state what "
        "additional context you would need if the evidence is insufficient."
    ),
    include_diff=True,
    include_log=True,
)

_CHANGE = TaskPreset(
    name="change",
    summary="Plan a minimal, correct code change.",
    instructions=(
        "You are implementing a code change. Use the provided excerpts, diff, "
        "and recent commits to plan a minimal, correct edit. Name the files you "
        "would modify, cite the chunk identifiers you rely on, preserve "
        "existing conventions, and note any missing context before proposing "
        "changes."
    ),
    include_diff=True,
    include_log=True,
)

_COMPARE = TaskPreset(
    name="compare",
    summary="Compare implementations across repositories.",
    instructions=(
        "You are comparing implementations across the provided repositories. "
        "Cite at least one excerpt per repository, describe concrete "
        "differences and similarities, and avoid any claim that the excerpts do "
        "not support."
    ),
)

PRESETS: dict[str, TaskPreset] = {
    preset.name: preset for preset in (_EXPLAIN, _REVIEW, _DEBUG, _CHANGE, _COMPARE)
}

DEFAULT_PRESET = "explain"


def preset_names() -> list[str]:
    return list(PRESETS)


def get_preset(name: str) -> TaskPreset:
    try:
        return PRESETS[name]
    except KeyError as error:
        valid = ", ".join(PRESETS)
        raise KeyError(f"Unknown preset '{name}'. Choose one of: {valid}.") from error
