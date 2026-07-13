"""Provider-free planning of repository context for software tasks.

This module deliberately deals in labels and observations rather than files or
provider responses.  A later repository adapter can use the slots as a stable
request contract.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_/-]*")
_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:\.\.?/|/|[A-Za-z0-9_.-]+/)[A-Za-z0-9_./-]+(?:\.[A-Za-z0-9]+)?"
)
_IDENTIFIER_RE = re.compile(r"(?<![A-Za-z0-9_])(?:[A-Za-z_][A-Za-z0-9_]*\.){1,}[A-Za-z_][A-Za-z0-9_]*|(?<![A-Za-z0-9_])[A-Z](?=[A-Za-z0-9_]*[A-Z0-9])[A-Za-z0-9_]*(?![A-Za-z0-9_])")
_STOP = {
    "a", "an", "and", "as", "at", "be", "by", "for", "from", "how", "in", "is", "it", "of", "on", "or", "the", "to", "with",
    "add", "build", "create", "explain", "implement", "introduce", "show", "support",
}


def _stem(word: str) -> str:
    """Apply a deliberately small, deterministic stemmer for labels."""
    word = word.lower().replace("_", "-")
    if len(word) > 5 and word.endswith("ies"):
        return word[:-3] + "y"
    # Keep descriptive past-tense labels such as "missed" useful while still
    # collapsing the common code variants "rendering" and "renders".
    for suffix in ("ing", "es", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)]
    if word.endswith("ed") and len(word) > 5 and word[:-2].endswith("r"):
        return word[:-2]
    return word


def normalize_label(label: str) -> str:
    """Return canonical token units for a label, excluding generic actions."""
    words = [word for raw in _WORD_RE.findall(str(label)) if (word := _stem(raw)) not in _STOP]
    return " ".join(words)


def _task_units(task: str) -> set[str]:
    """Return boundary-aware task terms without dropping action words."""
    return {
        _stem(raw)
        for raw in re.findall(r"[A-Za-z][A-Za-z0-9_-]*", task.lower())
    }


@dataclass(frozen=True)
class Concept:
    canonical: str
    labels: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"canonical": self.canonical, "labels": list(self.labels)}


def normalize_concepts(concepts: Iterable[str] | str) -> tuple[Concept, ...]:
    """Tokenize, normalize, and stable-deduplicate concept units."""
    if isinstance(concepts, str):
        concepts = (concepts,)
    result: list[Concept] = []
    positions: dict[str, int] = {}
    labels: list[list[str]] = []
    for raw in concepts:
        label = str(raw).strip()
        for token in normalize_label(label).split():
            canonical = token
            if canonical not in positions:
                positions[canonical] = len(result)
                result.append(Concept(canonical, (token,)))
                labels.append([token])
            elif token not in labels[positions[canonical]]:
                labels[positions[canonical]].append(token)
                index = positions[canonical]
                result[index] = Concept(canonical, tuple(labels[index]))
    return tuple(result)


@dataclass(frozen=True)
class EvidenceSlot:
    name: str
    required: bool
    rationale: str
    conditional_on: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "required": self.required, "rationale": self.rationale, "conditional_on": list(self.conditional_on)}


@dataclass(frozen=True)
class Grounding:
    status: str
    missing_slots: tuple[str, ...]
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "missing_slots": list(self.missing_slots), "confidence": self.confidence}


@dataclass(frozen=True)
class ContextPlan:
    task: str
    shapes: tuple[str, ...]
    concepts: tuple[Concept, ...]
    paths: tuple[str, ...]
    identifiers: tuple[str, ...]
    evidence_slots: tuple[EvidenceSlot, ...]
    grounding: Grounding

    @property
    def task_shapes(self) -> tuple[str, ...]:
        return self.shapes

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "shapes": list(self.shapes),
            "concepts": [c.to_dict() for c in self.concepts],
            "paths": list(self.paths),
            "identifiers": list(self.identifiers),
            "evidence_slots": [slot.to_dict() for slot in self.evidence_slots],
            "grounding": self.grounding.to_dict(),
        }


_SHAPE_TERMS: Mapping[str, tuple[str, ...]] = {
    "feature": ("add", "build", "create", "implement", "feature", "support", "introduce"),
    "debug": ("bug", "debug", "error", "exception", "failure", "fix", "crash", "traceback", "stack trace"),
    "review": ("review", "diff", "pull request", "pr", "changes", "inspect"),
    "explain": ("explain", "understand", "why", "how does", "walk through", "describe"),
}


def detect_task_shapes(task: str) -> tuple[str, ...]:
    text = task.lower()
    task_units = _task_units(task)
    found = [
        shape
        for shape, terms in _SHAPE_TERMS.items()
        if any(
            term in text if " " in term else _stem(term) in task_units
            for term in terms
        )
    ]
    if "feature" in found and "review" in found and not any(
        marker in text
        for marker in ("review the", "code review", "pull request", "review this", "diff")
    ):
        found.remove("review")
    if not found:
        found = ["feature"]
    if len(found) > 1:
        found = ["compound"] + found
    return tuple(found)


_SLOT_ALIASES: Mapping[str, tuple[str, ...]] = {
    "implementation": ("implementation", "implement", "code", "source", "module", "component"),
    "state/data": ("state", "data", "model", "store", "database", "api", "schema"),
    "integration/render": ("integration", "render", "ui", "view", "route", "endpoint", "wiring"),
    "analogous behavior": ("analogous", "similar", "example", "pattern", "existing", "precedent"),
    "validation/tests": ("test", "tests", "testing", "validation", "verify", "coverage", "reproduce"),
    "repo constraints": ("constraint", "convention", "repository", "repo", "dependency", "compatibility"),
    "style": ("style", "visual", "ui", "dashboard", "component", "view", "layout", "render", "css", "theme"),
    "config": ("config", "configuration", "setting", "settings", "manifest"),
    "schema": ("schema",),
    "migration": ("migration", "migrate"),
}


def _unique(items: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for item in items if item))


def _extract(task: str, explicit: Iterable[str] | None, pattern: re.Pattern[str]) -> tuple[str, ...]:
    values = list(explicit or ()) + [m.group(0) for m in pattern.finditer(task)]
    return _unique(v.strip(".,:;()[]{}") for v in values if v.strip(".,:;()[]{}"))


def _slots(task: str, shapes: tuple[str, ...]) -> tuple[EvidenceSlot, ...]:
    text = task.lower()
    task_units = _task_units(task)
    feature = "feature" in shapes or "compound" in shapes
    slots: list[EvidenceSlot] = []
    state_terms = ("state", "data", "model", "store", "database", "schema")
    integration_terms = (
        "integration",
        "render",
        "ui",
        "view",
        "route",
        "endpoint",
        "api",
        "wiring",
        "command",
        "cli",
    )
    slot_requirements = {
        "implementation": True,
        "state/data": any(term in task_units for term in state_terms),
        "integration/render": any(
            term in task_units for term in integration_terms
        ),
        "analogous behavior": False,
        "validation/tests": True,
        "repo constraints": True,
    }
    for name, is_required in slot_requirements.items():
        slots.append(EvidenceSlot(name, is_required, f"Evidence needed for {name}."))
    ui_terms = ("ui", "dashboard", "visualization", "component", "view", "layout", "css", "theme")
    atomic_terms = {
        "config": ("config", "configuration", "setting", "settings", "manifest"),
        "schema": ("schema",),
        "migration": ("migration", "migrate"),
    }
    if feature and any(term in task_units for term in ui_terms):
        slots.append(EvidenceSlot("style", True, "UI intent requires evidence of the repository's visual and interaction conventions.", ui_terms))
    for name, terms in atomic_terms.items():
        if feature and any(term in task_units for term in terms):
            slots.append(EvidenceSlot(name, False, f"Task language indicates {name} evidence may apply.", terms))
    if "debug" in shapes:
        slots.append(EvidenceSlot("runtime failure evidence", True, "Reproduction, stack trace, and failing-path evidence are needed."))
    if "review" in shapes:
        slots.append(EvidenceSlot("diff and change context", True, "The reviewed diff and its surrounding behavior are needed."))
    if "explain" in shapes:
        slots.append(EvidenceSlot("execution flow", True, "Relevant control and data flow are needed for an explanation."))
    return tuple(slots)


def _grounding(concepts: tuple[Concept, ...], roles: Iterable[str], slots: tuple[EvidenceSlot, ...]) -> Grounding:
    observed = set(c.canonical for c in concepts)
    for role in roles:
        observed.update(normalize_label(role).split())
    covered = tuple(slot.name for slot in slots if any(alias in observed for alias in _SLOT_ALIASES.get(slot.name, (slot.name,))))
    required_slots = tuple(slot for slot in slots if slot.required)
    missing = tuple(slot.name for slot in required_slots if slot.name not in covered)
    covered_required = sum(slot.name in covered for slot in required_slots)
    ratio = covered_required / len(required_slots) if required_slots else 1.0
    status = "grounded" if not missing else "partial" if covered else "ungrounded"
    confidence = round(ratio, 2)
    return Grounding(status, missing, confidence)


def plan_context(task: str, *, observed_concepts: Iterable[str] = (), observed_roles: Iterable[str] = (), paths: Iterable[str] = (), identifiers: Iterable[str] = ()) -> ContextPlan:
    """Build a deterministic context request plan without reading the repository."""
    shapes = detect_task_shapes(task)
    concepts = normalize_concepts([task, *observed_concepts])
    slots = _slots(task, shapes)
    observed = normalize_concepts(observed_concepts)
    return ContextPlan(str(task), shapes, concepts, _extract(task, paths, _PATH_RE), _extract(task, identifiers, _IDENTIFIER_RE), slots, _grounding(observed, observed_roles, slots))


build_context_plan = plan_context
