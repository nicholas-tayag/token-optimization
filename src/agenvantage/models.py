from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"", "0", "false", "no", "off"}:
            return False
        if normalized in {"1", "true", "yes", "on"}:
            return True
    return bool(value)


def _clean_labels(values: Iterable[Any]) -> tuple[str, ...]:
    labels: list[str] = []
    for value in values:
        label = str(value).strip()
        if label:
            labels.append(label)
    return tuple(labels)


@dataclass(frozen=True)
class ContextComponent:
    component_id: str
    kind: str
    text: str
    required: bool = False
    stable: bool = False
    priority: int = 0
    relevance: float = 0.0
    labels: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ContextComponent":
        component_id = str(raw.get("id", "")).strip()
        text = str(raw.get("text", "")).strip()
        if not component_id or not text:
            raise ValueError("Context components require non-empty id and text fields.")
        return cls(
            component_id=component_id,
            kind=str(raw.get("kind", "context")).strip() or "context",
            text=text,
            required=_coerce_bool(raw.get("required", False)),
            stable=_coerce_bool(raw.get("stable", False)),
            priority=int(raw.get("priority", 0)),
            relevance=float(raw.get("relevance", 0.0)),
            labels=_clean_labels(raw.get("labels", [])),
        )

    def render(self) -> str:
        return f"[{self.kind.upper()}:{self.component_id}]\n{self.text}"


@dataclass(frozen=True)
class ContextScenario:
    scenario_id: str
    description: str
    components: tuple[ContextComponent, ...]

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ContextScenario":
        scenario_id = str(raw.get("scenario_id", raw.get("id", ""))).strip()
        if not scenario_id:
            raise ValueError("Scenarios require a non-empty scenario_id or id field.")

        raw_components = raw.get("components")
        if not isinstance(raw_components, list):
            raise ValueError("Scenarios require a components list.")

        components = tuple(ContextComponent.from_dict(item) for item in raw_components)
        if not components:
            raise ValueError("A scenario must contain at least one context component.")
        return cls(
            scenario_id=scenario_id,
            description=str(raw.get("description", "")),
            components=components,
        )


@dataclass(frozen=True)
class ExcludedComponent:
    component_id: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.component_id, "reason": self.reason}


@dataclass(frozen=True)
class ContextPackage:
    policy: str
    included: tuple[ContextComponent, ...]
    excluded: tuple[ExcludedComponent, ...]
    rendered_context: str
    input_tokens: int
    stable_prefix_tokens: int
    budget: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "input_tokens": self.input_tokens,
            "stable_prefix_tokens": self.stable_prefix_tokens,
            "budget": self.budget,
            "included_components": [item.component_id for item in self.included],
            "excluded_components": [item.to_dict() for item in self.excluded],
            "metadata": self.metadata,
        }
