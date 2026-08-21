"""Deterministic prompt hygiene for local, low-cost agent workflows.

This is intentionally separate from repository retrieval. It improves the
user-authored task before AgenVantage selects repository evidence, without
calling a model or claiming provider-side savings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agenvantage.tokenizer import TokenCounter

PROMPT_MODES = ("general", "codex", "pr-review")

_FILLER_PATTERNS = (
    re.compile(r"\bplease\b", re.IGNORECASE),
    re.compile(r"\bkind of\b", re.IGNORECASE),
    re.compile(r"\bsort of\b", re.IGNORECASE),
    re.compile(r"\bi think\b", re.IGNORECASE),
    re.compile(r"\bmaybe\b", re.IGNORECASE),
    re.compile(r"\bjust\b", re.IGNORECASE),
    re.compile(r"\breally\b", re.IGNORECASE),
    re.compile(r"\bactually\b", re.IGNORECASE),
    re.compile(r"\bif possible\b", re.IGNORECASE),
    re.compile(r"\byou know\b", re.IGNORECASE),
)
_MODE_HINTS = {
    "codex": ("repo", "codebase", "implement", "tests", "bug", "fix", "refactor", "cli", "server", "component"),
    "pr-review": ("pr", "pull request", "diff", "review", "severity", "finding", "regression", "security"),
    "general": ("email", "summarize", "explain", "draft", "brainstorm", "compare", "outline"),
}


@dataclass(frozen=True)
class PromptOptimization:
    mode: str
    original_prompt: str
    optimized_prompt: str
    original_tokens: int
    optimized_tokens: int
    missing_info: tuple[str, ...]
    clarity_score: int

    @property
    def estimated_savings_tokens(self) -> int:
        return max(0, self.original_tokens - self.optimized_tokens)

    @property
    def estimated_savings_percent(self) -> float:
        if self.original_tokens <= 0:
            return 0.0
        return round(self.estimated_savings_tokens / self.original_tokens * 100, 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": self.mode,
            "original_prompt": self.original_prompt,
            "optimized_prompt": self.optimized_prompt,
            "original_tokens": self.original_tokens,
            "optimized_tokens": self.optimized_tokens,
            "estimated_savings_tokens": self.estimated_savings_tokens,
            "estimated_savings_percent": self.estimated_savings_percent,
            "missing_info": list(self.missing_info),
            "clarity_score": self.clarity_score,
            "claim_boundary": "local tokenizer estimate; provider billing is not measured",
        }


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()


def detect_prompt_mode(value: str) -> str:
    text = value.lower()
    scores = {
        mode: sum(1 for hint in hints if hint in text)
        for mode, hints in _MODE_HINTS.items()
    }
    if scores["pr-review"] >= 2:
        return "pr-review"
    if scores["codex"] >= 2:
        return "codex"
    return "general"


def _sentences(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", normalize_whitespace(value)) if part.strip()]


def _remove_filler(value: str) -> str:
    result = value
    for pattern in _FILLER_PATTERNS:
        result = pattern.sub("", result)
    return normalize_whitespace(result)


def _dedupe_sentences(value: str) -> str:
    seen: set[str] = set()
    unique: list[str] = []
    for sentence in _sentences(value):
        key = re.sub(r"[^a-z0-9 ]", "", sentence.lower()).strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(sentence)
    return " ".join(unique)


def _missing_info(value: str, mode: str) -> tuple[str, ...]:
    missing: list[str] = []
    if len(value) < 20:
        missing.append("specific task")
    if not re.search(r"\b(output|format|return|respond|show|give me|write)\b", value, re.IGNORECASE):
        missing.append("desired output format")
    if not re.search(r"\b(context|because|for|audience|repo|file|diff|current)\b", value, re.IGNORECASE):
        missing.append("relevant context")
    if mode == "codex" and not re.search(r"\b(test|verify|acceptance|pass|run)\b", value, re.IGNORECASE):
        missing.append("validation expectation")
    if mode == "pr-review" and not re.search(r"\b(severity|actionable|risk|regression|security|test)\b", value, re.IGNORECASE):
        missing.append("review priorities")
    return tuple(dict.fromkeys(missing))


def _sections(value: str, mode: str) -> tuple[str, str, str, str, str]:
    task = re.sub(r"^(task|context|constraints|output|done when|acceptance criteria):\s*", "", _sentences(value)[0] if _sentences(value) else value, flags=re.IGNORECASE).strip(" .?!")
    task = task or "Complete the requested task"
    context = "Use provided context only." if re.search(r"\b(context|background|current|existing|repo|codebase|diff)\b", value, re.IGNORECASE) else (
        "Inspect relevant repo files first." if mode == "codex" else "Use provided information; state assumptions."
    )
    constraints = "Honor explicit limits." if re.search(r"\b(no|don't|do not|avoid|must|only|without)\b", value, re.IGNORECASE) else "Be concise."
    if mode == "codex":
        constraints += " Keep changes scoped."
    if mode == "pr-review":
        constraints += " Report only actionable findings."
    output = "Use requested format." if re.search(r"\b(json|table|bullets|markdown|list|email|code|diff)\b", value, re.IGNORECASE) else (
        "Findings by severity with rationale." if mode == "pr-review" else "Changes, validation, risks." if mode == "codex" else "Concise structured answer."
    )
    acceptance = "Explicit success criteria are met." if re.search(r"\b(done|success|acceptance|verify|test|pass|criteria)\b", value, re.IGNORECASE) else (
        "Works; checks reported; unrelated files untouched." if mode == "codex" else "Findings are specific and actionable." if mode == "pr-review" else "Answer satisfies the task directly."
    )
    return task, context, constraints, output, acceptance


def optimize_prompt(value: str, *, mode: str = "auto", counter: TokenCounter | None = None) -> PromptOptimization:
    original = normalize_whitespace(value)
    resolved_mode = detect_prompt_mode(original) if mode in {"", "auto"} else mode
    if resolved_mode not in PROMPT_MODES:
        raise ValueError(f"Unsupported prompt optimizer mode: {resolved_mode}")
    cleaned = _dedupe_sentences(_remove_filler(original))
    task, context, constraints, output, acceptance = _sections(cleaned, resolved_mode)
    structured = "\n".join((
        f"Task: {task}",
        f"Context: {context}",
        f"Constraints: {constraints}",
        "Token strategy: Put stable instructions first; keep context scoped; cap output.",
        f"Output: {output}",
        f"Done when: {acceptance}",
    ))
    token_counter = counter or TokenCounter()
    original_tokens = token_counter.count(original)
    cleaned_tokens = token_counter.count(cleaned)
    structured_tokens = token_counter.count(structured)
    # A short prompt should not pay for a template. Structure only when the
    # added guidance is no larger than the cleaned user request.
    optimized = structured if structured_tokens <= cleaned_tokens else cleaned
    optimized_tokens = structured_tokens if structured_tokens <= cleaned_tokens else cleaned_tokens
    sentences = [sentence.lower() for sentence in _sentences(original)]
    repeated_penalty = max(0, len(sentences) - len(set(sentences))) * 8
    missing = _missing_info(cleaned, resolved_mode)
    return PromptOptimization(
        mode=resolved_mode,
        original_prompt=original,
        optimized_prompt=optimized,
        original_tokens=original_tokens,
        optimized_tokens=optimized_tokens,
        missing_info=missing,
        clarity_score=max(0, 100 - len(missing) * 12 - repeated_penalty),
    )


def append_optimization_log(result: PromptOptimization, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        import json

        handle.write(json.dumps(result.to_dict(), sort_keys=True) + "\n")
