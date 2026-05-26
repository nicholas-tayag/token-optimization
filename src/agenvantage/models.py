from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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
        component_id = str(raw["id"]).strip()
        text = str(raw["text"]).strip()
        if not component_id or not text:
            raise ValueError("Context components require non-empty id and text fields.")
        return cls(
            component_id=component_id,
            kind=str(raw.get("kind", "context")).strip() or "context",
            text=text,
            required=bool(raw.get("required", False)),
            stable=bool(raw.get("stable", False)),
            priority=int(raw.get("priority", 0)),
            relevance=float(raw.get("relevance", 0.0)),
            labels=tuple(str(label) for label in raw.get("labels", [])),
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
        components = tuple(ContextComponent.from_dict(item) for item in raw["components"])
        if not components:
            raise ValueError("A scenario must contain at least one context component.")
        return cls(
            scenario_id=str(raw["scenario_id"]),
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

