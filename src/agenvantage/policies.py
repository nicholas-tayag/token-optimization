from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from agenvantage.models import (
    ContextComponent,
    ContextPackage,
    ContextScenario,
    ExcludedComponent,
)
from agenvantage.tokenizer import TokenCounter


def _render(components: Iterable[ContextComponent]) -> str:
    return "\n\n".join(component.render() for component in components)


def _stable_first(components: Iterable[ContextComponent]) -> tuple[ContextComponent, ...]:
    values = tuple(components)
    return tuple(item for item in values if item.stable) + tuple(
        item for item in values if not item.stable
    )


def _leading_stable_prefix_tokens(
    included: tuple[ContextComponent, ...], counter: TokenCounter
) -> int:
    stable_prefix: list[ContextComponent] = []
    for component in included:
        if not component.stable:
            break
        stable_prefix.append(component)
    return counter.count(_render(stable_prefix)) if stable_prefix else 0


def _package(
    policy: str,
    included: tuple[ContextComponent, ...],
    excluded: tuple[ExcludedComponent, ...],
    counter: TokenCounter,
    budget: int | None = None,
    metadata: dict[str, object] | None = None,
) -> ContextPackage:
    rendered = _render(included)
    return ContextPackage(
        policy=policy,
        included=included,
        excluded=excluded,
        rendered_context=rendered,
        input_tokens=counter.count(rendered),
        stable_prefix_tokens=_leading_stable_prefix_tokens(included, counter),
        budget=budget,
        metadata=metadata or {},
    )


@dataclass(frozen=True)
class FullContextPolicy:
    name: str = "full"

    def assemble(self, scenario: ContextScenario, counter: TokenCounter) -> ContextPackage:
        return _package(self.name, scenario.components, (), counter)


@dataclass(frozen=True)
class CacheAlignedPolicy:
    name: str = "cache_aligned"

    def assemble(self, scenario: ContextScenario, counter: TokenCounter) -> ContextPackage:
        included = _stable_first(scenario.components)
        return _package(
            self.name,
            included,
            (),
            counter,
            metadata={"ordering": "stable components first, dynamic components last"},
        )


@dataclass(frozen=True)
class BudgetedContextPolicy:
    budget: int
    name: str = "budgeted"

    def assemble(self, scenario: ContextScenario, counter: TokenCounter) -> ContextPackage:
        if self.budget <= 0:
            raise ValueError("Token budget must be positive.")

        required = tuple(component for component in scenario.components if component.required)
        required_ordered = _stable_first(required)
        required_tokens = counter.count(_render(required_ordered))
        if required_tokens > self.budget:
            raise ValueError(
                f"Required context uses {required_tokens} tokens, exceeding budget {self.budget}."
            )

        optional = sorted(
            (component for component in scenario.components if not component.required),
            key=lambda component: (
                component.priority,
                component.relevance,
                component.stable,
            ),
            reverse=True,
        )

        selected = list(required)
        excluded: list[ExcludedComponent] = []
        for component in optional:
            tentative = _stable_first((*selected, component))
            if counter.count(_render(tentative)) <= self.budget:
                selected.append(component)
            else:
                excluded.append(
                    ExcludedComponent(component.component_id, "exceeds token budget")
                )

        included = _stable_first(selected)
        return _package(
            self.name,
            included,
            tuple(excluded),
            counter,
            budget=self.budget,
            metadata={"selection": "required then priority, relevance, and stability"},
        )

