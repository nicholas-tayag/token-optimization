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


FEATURE_BASE_INSTRUCTIONS = (
    "You are preparing a feature implementation plan. Use the provided "
    "repository excerpts to identify the most likely files to modify, the "
    "tests to update or add, and any supporting config or helper files an "
    "implementer should inspect first. Cite chunk identifiers for each "
    "suggested file, preserve existing conventions, and call out missing "
    "signals instead of hallucinating an edit surface. When missing signals "
    "are reported, call expand_context before implementing; do not guess."
)

_FULL_DISCIPLINE = """IMPLEMENTATION LADDER (apply after reading provided context):
1. Does this need to exist? If no, do not add it (YAGNI).
2. Already in selected chunks? Reuse it; do not rewrite it.
3. Standard library covers it? Use the standard library.
4. Native platform or framework API? Prefer it over a new dependency.
5. Existing installed dependency? Reuse it.
6. One line sufficient? Write one line.
7. Only then write the minimum code that passes the listed tests.

Never simplify away input validation, authentication, path traversal checks,
data-loss error handling, security-sensitive parsing, or accessibility when UI
is touched. When a guard or contract test is selected, run it before declaring
the task complete."""

_LITE_DISCIPLINE = (
    "IMPLEMENTATION DISCIPLINE: Reuse existing code and dependencies, prefer "
    "standard or native APIs, and write the minimum change that passes the "
    "listed tests. Never remove validation, security, data-loss handling, or "
    "selected guard-test coverage."
)

IMPLEMENTATION_DISCIPLINES = {
    "full": _FULL_DISCIPLINE,
    "lite": _LITE_DISCIPLINE,
    "off": "",
}


def instructions_for_preset(name: str, discipline: str = "full") -> str:
    preset = get_preset(name)
    if name != "feature":
        return preset.instructions
    try:
        discipline_text = IMPLEMENTATION_DISCIPLINES[discipline]
    except KeyError as error:
        choices = ", ".join(IMPLEMENTATION_DISCIPLINES)
        raise KeyError(f"Unknown discipline '{discipline}'. Choose one of: {choices}.") from error
    return FEATURE_BASE_INSTRUCTIONS + (f"\n\n{discipline_text}" if discipline_text else "")


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

_FEATURE = TaskPreset(
    name="feature",
    summary="Find the minimum sufficient context to start implementing a feature.",
    instructions=FEATURE_BASE_INSTRUCTIONS + "\n\n" + _FULL_DISCIPLINE,
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
    preset.name: preset
    for preset in (_EXPLAIN, _REVIEW, _DEBUG, _CHANGE, _FEATURE, _COMPARE)
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
