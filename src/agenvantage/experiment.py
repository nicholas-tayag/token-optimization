from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from opentelemetry import trace

from agenvantage.models import ContextPackage, ContextScenario
from agenvantage.policies import BudgetedContextPolicy, CacheAlignedPolicy, FullContextPolicy
from agenvantage.tokenizer import TokenCounter


def load_scenario(path: Path) -> ContextScenario:
    with path.open("r", encoding="utf-8") as fixture:
        return ContextScenario.from_dict(json.load(fixture))


def run_experiment(
    scenario: ContextScenario, counter: TokenCounter, budget: int
) -> dict[str, Any]:
    policies = (FullContextPolicy(), CacheAlignedPolicy(), BudgetedContextPolicy(budget))
    tracer = trace.get_tracer("agenvantage.experiment")
    packages: list[ContextPackage] = []

    with tracer.start_as_current_span("run_context_experiment") as experiment_span:
        experiment_span.set_attribute("agenvantage.scenario.id", scenario.scenario_id)
        experiment_span.set_attribute("agenvantage.experiment.policy_count", len(policies))
        for policy in policies:
            with tracer.start_as_current_span("assemble_context") as span:
                package = policy.assemble(scenario, counter)
                span.set_attribute("agenvantage.scenario.id", scenario.scenario_id)
                span.set_attribute("agenvantage.context.policy", package.policy)
                span.set_attribute("agenvantage.context.input_tokens", package.input_tokens)
                span.set_attribute(
                    "agenvantage.context.stable_prefix_tokens", package.stable_prefix_tokens
                )
                if package.budget is not None:
                    span.set_attribute("agenvantage.context.token_budget", package.budget)
                packages.append(package)

    baseline_tokens = packages[0].input_tokens
    results = []
    for package in packages:
        tokens_saved = baseline_tokens - package.input_tokens
        row = package.to_dict()
        row["tokens_saved_vs_full"] = tokens_saved
        row["token_reduction_percent_vs_full"] = round(
            (tokens_saved / baseline_tokens * 100) if baseline_tokens else 0.0, 2
        )
        results.append(row)

    return {
        "project": "AgenVantage",
        "scenario_id": scenario.scenario_id,
        "scenario_description": scenario.description,
        "tokenizer": {"model": counter.model, "encoding": counter.encoding_name},
        "measurement_notes": [
            "Token counts measure assembled input context locally.",
            "Stable-prefix tokens indicate structure for future cache experiments, not cache hits.",
            "No model quality, latency, or security outcome is measured in this milestone.",
        ],
        "policies": results,
    }
