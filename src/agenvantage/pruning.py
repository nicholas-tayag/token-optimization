"""Goal-conditioned line pruning for selected code excerpts."""

from __future__ import annotations

import re
from typing import Any


_ALWAYS_KEEP_MARKERS = (
    "[REDACTED_",
    "authorization",
    "api_key",
    "apikey",
    "secret",
    "password",
    "private_key",
    "client_secret",
)

_TOKEN_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}")


def _tokenize(text: str) -> set[str]:
    split_camel = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    normalized = split_camel.replace("_", " ").replace("-", " ")
    return {token.casefold() for token in _TOKEN_PATTERN.findall(normalized)}


def goal_hints_from_context(
    task: str,
    *,
    change_surface: dict[str, Any] | None = None,
    preset: str = "feature",
) -> set[str]:
    hints = _tokenize(task)
    if change_surface:
        for key in ("edit_targets", "test_targets", "config_targets", "supporting_targets"):
            for item in change_surface.get(key, []):
                path = str(item.get("relative_path") or item.get("path") or "")
                hints.update(_tokenize(path.replace("/", " ").replace(".", " ")))
                hints.update(_tokenize(" ".join(str(v) for v in item.get("matched_terms") or [])))
    if preset in {"debug", "change", "review"}:
        hints.update({"error", "exception", "fail", "test", "assert", "fix", "bug"})
    if preset == "feature":
        hints.update({"def", "class", "test", "implement", "add"})
    return {hint for hint in hints if len(hint) >= 2}


def score_line(line: str, hints: set[str], *, line_number: int) -> float:
    stripped = line.strip()
    if not stripped:
        return 0.0
    score = 0.0
    line_tokens = _tokenize(stripped)
    overlap = line_tokens & hints
    score += len(overlap) * 2.0
    lowered = stripped.casefold()
    if any(marker in lowered for marker in _ALWAYS_KEEP_MARKERS):
        score += 6.0
    if any(len(hint) >= 3 and hint in lowered for hint in hints):
        score += 2.0
    if stripped.startswith(("def ", "class ", "async def ", "@", "test_", "export ")):
        score += 3.0
    if any(keyword in lowered for keyword in ("return", "raise", "assert", "yield", "import", "from ")):
        score += 1.5
    if stripped.startswith("#") and overlap:
        score += 0.5
    if line_number <= 3:
        score += 0.25
    return score


def prune_lines(
    text: str,
    *,
    goal_hints: set[str],
    min_score: float = 0.5,
    always_keep_blank_separators: bool = True,
) -> tuple[str, dict[str, Any]]:
    lines = text.splitlines()
    if not lines:
        return text, {"kept_lines": 0, "dropped_lines": 0, "original_lines": 0}

    scored: list[tuple[int, str, float]] = []
    for index, line in enumerate(lines, start=1):
        scored.append((index, line, score_line(line, goal_hints, line_number=index)))

    kept: list[str] = []
    dropped: list[int] = []
    for line_number, line, line_score in scored:
        stripped = line.strip()
        lowered = stripped.casefold()
        if stripped and any(marker in lowered for marker in _ALWAYS_KEEP_MARKERS):
            kept.append(line)
            continue
        if stripped in {"{", "}", "};", "});", "],", "),"} or re.fullmatch(
            r"[{}()[\];,]+", stripped
        ):
            kept.append(line)
            continue
        if not stripped and always_keep_blank_separators and kept and kept[-1].strip():
            kept.append(line)
            continue
        if line_score >= min_score or (not stripped and kept):
            kept.append(line)
        else:
            dropped.append(line_number)

    if not any(item.strip() for item in kept):
        kept = lines

    return (
        "\n".join(kept).rstrip() + ("\n" if kept else ""),
        {
            "original_lines": len(lines),
            "kept_lines": len(kept),
            "dropped_lines": len(dropped),
            "dropped_line_numbers": dropped,
            "goal_hint_count": len(goal_hints),
        },
    )


__all__ = ["goal_hints_from_context", "prune_lines", "score_line"]
