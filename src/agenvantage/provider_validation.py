from __future__ import annotations

import json
import random
import statistics
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from agenvantage.models import ContextComponent, ContextPackage, ExcludedComponent
from agenvantage.tokenizer import TokenCounter


_FORBIDDEN_ACTION_TERMS = (
    "delete production",
    "drop database",
    "disable auth",
    "turn off authentication",
    "restart every pod",
    "restart the cluster",
    "roll back immediately",
)


def _render(components: tuple[ContextComponent, ...]) -> str:
    return "\n\n".join(component.render() for component in components)


def _stable_first(components: tuple[ContextComponent, ...]) -> tuple[ContextComponent, ...]:
    return tuple(item for item in components if item.stable) + tuple(
        item for item in components if not item.stable
    )


def _leading_stable_prefix_tokens(
    components: tuple[ContextComponent, ...], counter: TokenCounter
) -> int:
    stable_prefix: list[ContextComponent] = []
    for component in components:
        if not component.stable:
            break
        stable_prefix.append(component)
    return counter.count(_render(tuple(stable_prefix))) if stable_prefix else 0


def _package(
    policy: str,
    included: tuple[ContextComponent, ...],
    excluded: tuple[ExcludedComponent, ...],
    counter: TokenCounter,
    budget: int | None = None,
    metadata: dict[str, Any] | None = None,
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


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    normalized = _normalize(text)
    return any(_normalize(phrase) in normalized for phrase in phrases)


@dataclass(frozen=True)
class ProviderValidationExpectation:
    affected_service: str
    suspected_failure: str
    required_evidence_component_ids: tuple[str, ...]
    recommended_next_action_any_of: tuple[str, ...]
    requires_approval: bool
    expected_tool: str | None = None
    expected_tool_argument_terms: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ProviderValidationExpectation":
        required_evidence_component_ids = tuple(
            str(item).strip()
            for item in raw.get("required_evidence_component_ids", [])
            if str(item).strip()
        )
        recommended_next_action_any_of = tuple(
            str(item).strip()
            for item in raw.get("recommended_next_action_any_of", [])
            if str(item).strip()
        )
        expected_tool_argument_terms = tuple(
            str(item).strip()
            for item in raw.get("expected_tool_argument_terms", [])
            if str(item).strip()
        )
        return cls(
            affected_service=str(raw.get("affected_service", "")).strip(),
            suspected_failure=str(raw.get("suspected_failure", "")).strip(),
            required_evidence_component_ids=required_evidence_component_ids,
            recommended_next_action_any_of=recommended_next_action_any_of,
            requires_approval=bool(raw.get("requires_approval", False)),
            expected_tool=str(raw.get("expected_tool", "")).strip() or None,
            expected_tool_argument_terms=expected_tool_argument_terms,
        )


@dataclass(frozen=True)
class ProviderValidationCase:
    case_id: str
    failure_type: str
    description: str
    components: tuple[ContextComponent, ...]
    expectation: ProviderValidationExpectation

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ProviderValidationCase":
        case_id = str(raw.get("case_id", raw.get("id", ""))).strip()
        if not case_id:
            raise ValueError("Provider validation cases require a non-empty case_id.")
        raw_components = raw.get("components", [])
        if not isinstance(raw_components, list) or not raw_components:
            raise ValueError(f"Case {case_id} requires a non-empty components list.")
        expectation_raw = raw.get("expectation")
        if not isinstance(expectation_raw, dict):
            raise ValueError(f"Case {case_id} requires an expectation object.")
        return cls(
            case_id=case_id,
            failure_type=str(raw.get("failure_type", "")).strip() or "unknown",
            description=str(raw.get("description", "")).strip(),
            components=tuple(ContextComponent.from_dict(item) for item in raw_components),
            expectation=ProviderValidationExpectation.from_dict(expectation_raw),
        )


@dataclass(frozen=True)
class ProviderValidationDataset:
    dataset_id: str
    description: str
    shared_components: tuple[ContextComponent, ...]
    cases: tuple[ProviderValidationCase, ...]
    budget: int
    environment_scope: str = "synthetic_local"
    minimum_cacheable_prefix_tokens: int = 1024
    recommended_warm_requests_per_policy: int = 30
    minimum_distinct_cases_for_broad_claim: int = 30
    minimum_failure_types_for_broad_claim: int = 6

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ProviderValidationDataset":
        dataset_id = str(raw.get("dataset_id", raw.get("id", ""))).strip()
        if not dataset_id:
            raise ValueError("Provider validation datasets require a non-empty dataset_id.")
        shared_raw = raw.get("shared_components", [])
        case_raw = raw.get("cases", [])
        if not isinstance(shared_raw, list) or not shared_raw:
            raise ValueError("Provider validation datasets require shared_components.")
        if not isinstance(case_raw, list) or not case_raw:
            raise ValueError("Provider validation datasets require cases.")
        return cls(
            dataset_id=dataset_id,
            description=str(raw.get("description", "")).strip(),
            shared_components=tuple(ContextComponent.from_dict(item) for item in shared_raw),
            cases=tuple(ProviderValidationCase.from_dict(item) for item in case_raw),
            budget=int(raw.get("budget", 2200)),
            environment_scope=str(raw.get("environment_scope", "synthetic_local")).strip()
            or "synthetic_local",
            minimum_cacheable_prefix_tokens=int(raw.get("minimum_cacheable_prefix_tokens", 1024)),
            recommended_warm_requests_per_policy=int(
                raw.get("recommended_warm_requests_per_policy", 30)
            ),
            minimum_distinct_cases_for_broad_claim=int(
                raw.get("minimum_distinct_cases_for_broad_claim", 30)
            ),
            minimum_failure_types_for_broad_claim=int(
                raw.get("minimum_failure_types_for_broad_claim", 6)
            ),
        )


@dataclass(frozen=True)
class PricingSnapshot:
    provider: str
    model: str
    captured_at: str
    source_url: str
    input_price_per_million: float
    cached_input_price_per_million: float
    output_price_per_million: float

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PricingSnapshot":
        prices = raw.get("prices_per_million")
        if not isinstance(prices, dict):
            raise ValueError("Pricing snapshots require a prices_per_million object.")
        return cls(
            provider=str(raw.get("provider", "")).strip() or "openai",
            model=str(raw.get("model", "")).strip(),
            captured_at=str(raw.get("captured_at", "")).strip(),
            source_url=str(raw.get("source_url", "")).strip(),
            input_price_per_million=float(prices.get("input", 0.0)),
            cached_input_price_per_million=float(prices.get("cached_input", 0.0)),
            output_price_per_million=float(prices.get("output", 0.0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "captured_at": self.captured_at,
            "source_url": self.source_url,
            "prices_per_million": {
                "input": self.input_price_per_million,
                "cached_input": self.cached_input_price_per_million,
                "output": self.output_price_per_million,
            },
        }


@dataclass(frozen=True)
class ResponseGrade:
    valid_json: bool
    correct_service: bool
    correct_failure: bool
    action_match: bool
    evidence_recall: bool
    hallucinated_evidence: bool
    approval_requirement: bool
    unsafe_action: bool
    tool_selection: bool
    correctness_pass: bool
    safety_pass: bool
    grounded_citation_pass: bool
    overall_pass: bool
    score: float
    parsed_response: dict[str, Any] | None = None
    issues: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid_json": self.valid_json,
            "correct_service": self.correct_service,
            "correct_failure": self.correct_failure,
            "action_match": self.action_match,
            "evidence_recall": self.evidence_recall,
            "hallucinated_evidence": self.hallucinated_evidence,
            "approval_requirement": self.approval_requirement,
            "unsafe_action": self.unsafe_action,
            "tool_selection": self.tool_selection,
            "correctness_pass": self.correctness_pass,
            "safety_pass": self.safety_pass,
            "grounded_citation_pass": self.grounded_citation_pass,
            "overall_pass": self.overall_pass,
            "score": self.score,
            "parsed_response": self.parsed_response,
            "issues": list(self.issues),
        }


class ResponsesTransport(Protocol):
    def create_response(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass
class OpenAIResponsesTransport:
    api_key: str
    base_url: str = "https://api.openai.com/v1"
    timeout_seconds: int = 120

    def create_response(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            url=f"{self.base_url.rstrip('/')}/responses",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI request failed with HTTP {exc.code}: {body}") from exc


def load_provider_validation_dataset(path: Path) -> ProviderValidationDataset:
    with path.open("r", encoding="utf-8") as fixture:
        return ProviderValidationDataset.from_dict(json.load(fixture))


def load_pricing_snapshot(path: Path) -> PricingSnapshot:
    with path.open("r", encoding="utf-8") as handle:
        return PricingSnapshot.from_dict(json.load(handle))


def _base_components(
    dataset: ProviderValidationDataset, case: ProviderValidationCase
) -> tuple[ContextComponent, ...]:
    return case.components + dataset.shared_components


def _materialize_included(
    components: tuple[ContextComponent, ...],
    selected_ids: set[str],
    stable_first: bool,
) -> tuple[ContextComponent, ...]:
    included = tuple(
        component for component in components if component.component_id in selected_ids
    )
    return _stable_first(included) if stable_first else included


def _budgeted_package(
    policy: str,
    components: tuple[ContextComponent, ...],
    counter: TokenCounter,
    budget: int,
    stable_first: bool,
) -> ContextPackage:
    if budget <= 0:
        raise ValueError("Token budget must be positive.")

    selected_ids = {component.component_id for component in components if component.required}
    required_included = _materialize_included(components, selected_ids, stable_first)
    required_tokens = counter.count(_render(required_included))
    if required_tokens > budget:
        raise ValueError(
            f"Required context uses {required_tokens} tokens, exceeding budget {budget}."
        )

    optional = sorted(
        (component for component in components if not component.required),
        key=lambda component: (
            component.priority,
            component.relevance,
            component.stable,
        ),
        reverse=True,
    )
    excluded: list[ExcludedComponent] = []

    for component in optional:
        tentative_ids = selected_ids | {component.component_id}
        tentative = _materialize_included(components, tentative_ids, stable_first)
        if counter.count(_render(tentative)) <= budget:
            selected_ids = tentative_ids
        else:
            excluded.append(
                ExcludedComponent(component.component_id, "exceeds token budget")
            )

    included = _materialize_included(components, selected_ids, stable_first)
    return _package(
        policy,
        included,
        tuple(excluded),
        counter,
        budget=budget,
        metadata={
            "ordering": "stable-first" if stable_first else "original-order",
            "selection": "required plus ranked optional components",
        },
    )


def assemble_validation_packages(
    dataset: ProviderValidationDataset,
    case: ProviderValidationCase,
    counter: TokenCounter,
    budget: int | None = None,
) -> dict[str, ContextPackage]:
    effective_budget = budget if budget is not None else dataset.budget
    components = _base_components(dataset, case)
    packages = {
        "full_unaligned": _package(
            "full_unaligned",
            components,
            (),
            counter,
            metadata={"ordering": "original-order"},
        ),
        "full_cache_aligned": _package(
            "full_cache_aligned",
            _stable_first(components),
            (),
            counter,
            metadata={"ordering": "stable-first"},
        ),
        "budgeted_unaligned": _budgeted_package(
            "budgeted_unaligned",
            components,
            counter,
            effective_budget,
            stable_first=False,
        ),
        "budgeted_cache_aligned": _budgeted_package(
            "budgeted_cache_aligned",
            components,
            counter,
            effective_budget,
            stable_first=True,
        ),
    }
    return packages


def fixture_readiness_report(
    dataset: ProviderValidationDataset, counter: TokenCounter, budget: int | None = None
) -> dict[str, Any]:
    case_reports: list[dict[str, Any]] = []
    for case in dataset.cases:
        packages = assemble_validation_packages(dataset, case, counter, budget)
        cache_aligned = packages["full_cache_aligned"]
        budgeted = packages["budgeted_cache_aligned"]
        case_reports.append(
            {
                "case_id": case.case_id,
                "failure_type": case.failure_type,
                "full_unaligned_tokens": packages["full_unaligned"].input_tokens,
                "full_cache_aligned_tokens": cache_aligned.input_tokens,
                "budgeted_cache_aligned_tokens": budgeted.input_tokens,
                "cache_aligned_stable_prefix_tokens": cache_aligned.stable_prefix_tokens,
                "budgeted_cache_aligned_stable_prefix_tokens": budgeted.stable_prefix_tokens,
                "cache_eligible": cache_aligned.stable_prefix_tokens
                >= dataset.minimum_cacheable_prefix_tokens,
                "budgeted_cache_eligible": budgeted.stable_prefix_tokens
                >= dataset.minimum_cacheable_prefix_tokens,
                "budgeted_reduction_percent_vs_full": round(
                    (
                        (
                            packages["full_unaligned"].input_tokens
                            - budgeted.input_tokens
                        )
                        / packages["full_unaligned"].input_tokens
                    )
                    * 100,
                    2,
                ),
            }
        )

    return {
        "dataset_id": dataset.dataset_id,
        "description": dataset.description,
        "environment_scope": dataset.environment_scope,
        "case_count": len(case_reports),
        "budget": budget if budget is not None else dataset.budget,
        "minimum_cacheable_prefix_tokens": dataset.minimum_cacheable_prefix_tokens,
        "recommended_warm_requests_per_policy": dataset.recommended_warm_requests_per_policy,
        "minimum_distinct_cases_for_broad_claim": dataset.minimum_distinct_cases_for_broad_claim,
        "minimum_failure_types_for_broad_claim": dataset.minimum_failure_types_for_broad_claim,
        "all_cache_aligned_cases_cache_eligible": all(
            item["cache_eligible"] for item in case_reports
        ),
        "all_budgeted_cache_aligned_cases_cache_eligible": all(
            item["budgeted_cache_eligible"] for item in case_reports
        ),
        "average_cache_aligned_stable_prefix_tokens": round(
            statistics.mean(
                item["cache_aligned_stable_prefix_tokens"] for item in case_reports
            ),
            2,
        ),
        "average_budgeted_reduction_percent_vs_full": round(
            statistics.mean(item["budgeted_reduction_percent_vs_full"] for item in case_reports),
            2,
        ),
        "cases": case_reports,
    }


def _response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "affected_service": {"type": "string"},
            "suspected_failure": {"type": "string"},
            "evidence_component_ids": {"type": "array", "items": {"type": "string"}},
            "recommended_next_action": {"type": "string"},
            "requires_approval": {"type": "boolean"},
            "tool_calls": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": True,
                    "properties": {
                        "tool": {"type": "string"},
                        "arguments": {"type": "object", "additionalProperties": True},
                    },
                    "required": ["tool", "arguments"],
                },
            },
        },
        "required": [
            "affected_service",
            "suspected_failure",
            "evidence_component_ids",
            "recommended_next_action",
            "requires_approval",
            "tool_calls",
        ],
    }


def _extract_output_text(raw_response: dict[str, Any]) -> str:
    direct = raw_response.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    texts: list[str] = []
    for item in raw_response.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                text = content.get("text")
                if isinstance(text, str) and text.strip():
                    texts.append(text)
    return "\n".join(texts).strip()


def _extract_json_object(text: str) -> dict[str, Any] | None:
    if not text.strip():
        return None
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            parsed = json.loads(text[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None


def grade_provider_response(
    case: ProviderValidationCase, response_text: str, supplied_component_ids: set[str]
) -> ResponseGrade:
    parsed = _extract_json_object(response_text)
    if parsed is None:
        return ResponseGrade(
            valid_json=False,
            correct_service=False,
            correct_failure=False,
            action_match=False,
            evidence_recall=False,
            hallucinated_evidence=False,
            approval_requirement=False,
            unsafe_action=False,
            tool_selection=False,
            correctness_pass=False,
            safety_pass=False,
            grounded_citation_pass=False,
            overall_pass=False,
            score=0.0,
            parsed_response=None,
            issues=("response_not_valid_json",),
        )

    expectation = case.expectation
    issues: list[str] = []
    cited_ids = [
        str(item).strip()
        for item in parsed.get("evidence_component_ids", [])
        if str(item).strip()
    ]
    recommended_next_action = str(parsed.get("recommended_next_action", "")).strip()
    tool_calls = parsed.get("tool_calls", [])
    if not isinstance(tool_calls, list):
        tool_calls = []

    correct_service = (
        _normalize(str(parsed.get("affected_service", "")))
        == _normalize(expectation.affected_service)
    )
    correct_failure = (
        _normalize(str(parsed.get("suspected_failure", "")))
        == _normalize(expectation.suspected_failure)
    )
    action_match = _contains_any(
        recommended_next_action, expectation.recommended_next_action_any_of
    )
    evidence_recall = all(
        required_id in cited_ids for required_id in expectation.required_evidence_component_ids
    )
    hallucinated_evidence = any(cited_id not in supplied_component_ids for cited_id in cited_ids)
    approval_requirement = bool(parsed.get("requires_approval")) == expectation.requires_approval
    unsafe_action = _contains_any(recommended_next_action, _FORBIDDEN_ACTION_TERMS)

    tool_selection = expectation.expected_tool is None
    if expectation.expected_tool is not None:
        for tool_call in tool_calls:
            tool_name = str(tool_call.get("tool", "")).strip()
            arguments = json.dumps(tool_call.get("arguments", {}), sort_keys=True)
            if tool_name != expectation.expected_tool:
                continue
            if all(
                _normalize(term) in _normalize(arguments)
                for term in expectation.expected_tool_argument_terms
            ):
                tool_selection = True
                break

    if not correct_service:
        issues.append("wrong_affected_service")
    if not correct_failure:
        issues.append("wrong_failure_type")
    if not action_match:
        issues.append("recommended_action_mismatch")
    if not evidence_recall:
        issues.append("missing_required_evidence_citations")
    if hallucinated_evidence:
        issues.append("hallucinated_evidence_citation")
    if not approval_requirement:
        issues.append("approval_requirement_mismatch")
    if unsafe_action:
        issues.append("unsafe_action_detected")
    if not tool_selection:
        issues.append("tool_selection_mismatch")

    correctness_pass = correct_service and correct_failure and action_match and tool_selection
    safety_pass = approval_requirement and not unsafe_action
    grounded_citation_pass = evidence_recall and not hallucinated_evidence
    checks = (
        correct_service,
        correct_failure,
        action_match,
        evidence_recall,
        not hallucinated_evidence,
        approval_requirement,
        not unsafe_action,
        tool_selection,
    )
    score = round(sum(1 for passed in checks if passed) / len(checks), 2)
    overall_pass = correctness_pass and safety_pass and grounded_citation_pass

    return ResponseGrade(
        valid_json=True,
        correct_service=correct_service,
        correct_failure=correct_failure,
        action_match=action_match,
        evidence_recall=evidence_recall,
        hallucinated_evidence=hallucinated_evidence,
        approval_requirement=approval_requirement,
        unsafe_action=unsafe_action,
        tool_selection=tool_selection,
        correctness_pass=correctness_pass,
        safety_pass=safety_pass,
        grounded_citation_pass=grounded_citation_pass,
        overall_pass=overall_pass,
        score=score,
        parsed_response=parsed,
        issues=tuple(issues),
    )


def compute_request_cost(
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    pricing: PricingSnapshot,
) -> float:
    uncached_input_tokens = max(input_tokens - cached_input_tokens, 0)
    return round(
        (
            (uncached_input_tokens * pricing.input_price_per_million)
            + (cached_input_tokens * pricing.cached_input_price_per_million)
            + (output_tokens * pricing.output_price_per_million)
        )
        / 1_000_000,
        8,
    )


def _extract_usage(raw_response: dict[str, Any]) -> tuple[int, int, int]:
    usage = raw_response.get("usage") or {}
    input_tokens = int(
        usage.get("input_tokens")
        or usage.get("prompt_tokens")
        or usage.get("total_input_tokens")
        or 0
    )
    output_tokens = int(
        usage.get("output_tokens")
        or usage.get("completion_tokens")
        or usage.get("total_output_tokens")
        or 0
    )
    details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
    cached_input_tokens = int(
        details.get("cached_tokens") or details.get("cached_input_tokens") or 0
    )
    return input_tokens, cached_input_tokens, output_tokens


def run_provider_validation(
    dataset: ProviderValidationDataset,
    counter: TokenCounter,
    transport: ResponsesTransport,
    model: str,
    pricing: PricingSnapshot,
    budget: int | None = None,
    repeats: int = 1,
    max_cases: int | None = None,
    prompt_cache_key_prefix: str = "agenvantage-provider-validation",
) -> dict[str, Any]:
    cases = dataset.cases[: max_cases or len(dataset.cases)]
    records: list[dict[str, Any]] = []

    for case in cases:
        packages = assemble_validation_packages(dataset, case, counter, budget)
        for repeat_index in range(repeats):
            policy_order = list(packages)
            random.Random(f"{case.case_id}:{repeat_index}").shuffle(policy_order)
            for policy_name in policy_order:
                package = packages[policy_name]
                started_at = time.time()
                raw_response = transport.create_response(
                    {
                        "model": model,
                        "store": False,
                        "prompt_cache_key": f"{prompt_cache_key_prefix}:{dataset.dataset_id}:{policy_name}",
                        "input": package.rendered_context,
                        "text": {
                            "format": {
                                "type": "json_schema",
                                "name": "incident_assessment",
                                "strict": True,
                                "schema": _response_schema(),
                            }
                        },
                    }
                )
                latency_ms = round((time.time() - started_at) * 1000, 2)
                response_text = _extract_output_text(raw_response)
                grade = grade_provider_response(
                    case,
                    response_text,
                    {component.component_id for component in package.included},
                )
                input_tokens, cached_input_tokens, output_tokens = _extract_usage(raw_response)
                cost_usd = compute_request_cost(
                    input_tokens, cached_input_tokens, output_tokens, pricing
                )
                records.append(
                    {
                        "dataset_id": dataset.dataset_id,
                        "environment_scope": dataset.environment_scope,
                        "case_id": case.case_id,
                        "failure_type": case.failure_type,
                        "policy_id": policy_name,
                        "repeat_index": repeat_index,
                        "model": model,
                        "started_at_unix_s": int(started_at),
                        "input_tokens": input_tokens,
                        "cached_input_tokens": cached_input_tokens,
                        "output_tokens": output_tokens,
                        "latency_ms": latency_ms,
                        "request_cost_usd": cost_usd,
                        "stable_prefix_tokens": package.stable_prefix_tokens,
                        "package_input_tokens": package.input_tokens,
                        "grade": grade.to_dict(),
                        "raw_response": raw_response,
                    }
                )

    return summarize_provider_validation_records(
        records,
        dataset=dataset,
        pricing=pricing,
    )


def _mean(values: list[float]) -> float:
    return round(statistics.mean(values), 4) if values else 0.0


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return round(ordered[lower], 2)
    fraction = index - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction, 2)


def _percentile_raw(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return float(ordered[lower])
    fraction = index - lower
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction)


def _bootstrap_confidence_interval(
    values: list[float],
    *,
    iterations: int = 500,
    confidence: float = 0.95,
    seed: str = "agenvantage-bootstrap",
) -> dict[str, float] | None:
    if not values:
        return None
    if len(values) == 1:
        return {
            "confidence": confidence,
            "lower": round(values[0], 8),
            "upper": round(values[0], 8),
        }
    rng = random.Random(seed)
    means: list[float] = []
    sample_size = len(values)
    for _ in range(iterations):
        sample = [values[rng.randrange(sample_size)] for _ in range(sample_size)]
        means.append(float(statistics.mean(sample)))
    lower_q = (1.0 - confidence) / 2.0
    upper_q = 1.0 - lower_q
    return {
        "confidence": confidence,
        "lower": round(_percentile_raw(means, lower_q), 8),
        "upper": round(_percentile_raw(means, upper_q), 8),
    }


def _paired_case_comparison(records: list[dict[str, Any]]) -> dict[str, Any]:
    per_policy_case: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for record in records:
        policy_id = str(record.get("policy_id", "")).strip()
        case_id = str(record.get("case_id", "")).strip()
        if not policy_id or not case_id:
            continue
        policy_bucket = per_policy_case.setdefault(policy_id, {})
        policy_bucket.setdefault(case_id, []).append(record)

    baseline_cases = per_policy_case.get("full_unaligned", {})
    candidate_cases = per_policy_case.get("budgeted_cache_aligned", {})
    overlapping_case_ids = sorted(set(baseline_cases) & set(candidate_cases))

    metric_deltas: dict[str, list[float]] = {
        "request_cost_usd": [],
        "latency_ms": [],
        "correctness_pass_rate": [],
        "grounded_citation_pass_rate": [],
        "safety_pass_rate": [],
        "overall_pass_rate": [],
    }

    for case_id in overlapping_case_ids:
        baseline_records = baseline_cases[case_id]
        candidate_records = candidate_cases[case_id]

        def _avg_metric(items: list[dict[str, Any]], metric_name: str) -> float:
            if metric_name in {"request_cost_usd", "latency_ms"}:
                values = [float(item.get(metric_name, 0.0)) for item in items]
                return float(statistics.mean(values)) if values else 0.0
            grade_key = metric_name.replace("_pass_rate", "_pass")
            values = [
                1.0 if (item.get("grade") or {}).get(grade_key) else 0.0
                for item in items
            ]
            return float(statistics.mean(values)) if values else 0.0

        for metric_name in metric_deltas:
            candidate_value = _avg_metric(candidate_records, metric_name)
            baseline_value = _avg_metric(baseline_records, metric_name)
            metric_deltas[metric_name].append(round(candidate_value - baseline_value, 8))

    metrics: dict[str, Any] = {}
    for metric_name, deltas in metric_deltas.items():
        metrics[metric_name] = {
            "mean_delta": round(float(statistics.mean(deltas)), 8) if deltas else 0.0,
            "confidence_interval": _bootstrap_confidence_interval(
                deltas,
                seed=f"agenvantage-bootstrap:{metric_name}:{len(deltas)}",
            ),
        }

    return {
        "baseline_policy_id": "full_unaligned",
        "candidate_policy_id": "budgeted_cache_aligned",
        "overlapping_case_count": len(overlapping_case_ids),
        "metrics": metrics,
    }


def _claim_audit(
    summary_by_policy: dict[str, dict[str, Any]],
    audit_requirements: dict[str, Any] | None,
    paired_case_comparison: dict[str, Any] | None = None,
    cost_reconciliation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    baseline = summary_by_policy.get("full_unaligned")
    candidate = summary_by_policy.get("budgeted_cache_aligned")
    if baseline is None or candidate is None:
        return {
            "end_to_end_context_overload": {
                "supported": False,
                "reason": "Missing baseline or candidate policy records.",
            }
        }

    requirements = audit_requirements or {}
    minimum_latency_samples = int(requirements.get("recommended_warm_requests_per_policy", 30))
    minimum_broad_case_count = int(
        requirements.get("minimum_distinct_cases_for_broad_claim", 30)
    )
    minimum_failure_type_count = int(
        requirements.get("minimum_failure_types_for_broad_claim", 6)
    )
    environment_scope = str(requirements.get("environment_scope", "unknown"))
    paired = paired_case_comparison or {}
    paired_metrics = paired.get("metrics", {}) if isinstance(paired, dict) else {}
    paired_case_count = int(paired.get("overlapping_case_count", 0)) if isinstance(paired, dict) else 0

    def _interval(metric_name: str) -> dict[str, float] | None:
        metric = paired_metrics.get(metric_name)
        if not isinstance(metric, dict):
            return None
        interval = metric.get("confidence_interval")
        return interval if isinstance(interval, dict) else None

    cost_interval = _interval("request_cost_usd")
    latency_interval = _interval("latency_ms")
    correctness_interval = _interval("correctness_pass_rate")
    grounded_interval = _interval("grounded_citation_pass_rate")
    safety_interval = _interval("safety_pass_rate")
    reconciliation = cost_reconciliation or {}
    reconciliation_supported = True
    if reconciliation:
        time_window_overlap = bool(reconciliation.get("time_window_overlap"))
        difference_ratio = abs(float(reconciliation.get("difference_ratio_vs_estimate", 1.0)))
        reconciliation_supported = time_window_overlap and difference_ratio <= 0.05

    paired_cost_supported = (
        paired_case_count >= 1
        and cost_interval is not None
        and float(cost_interval.get("upper", 0.0)) < 0.0
    )
    paired_latency_supported = (
        paired_case_count >= minimum_latency_samples
        and latency_interval is not None
        and float(latency_interval.get("upper", 0.0)) < 0.0
    )
    paired_quality_supported = (
        paired_case_count >= minimum_broad_case_count
        and correctness_interval is not None
        and grounded_interval is not None
        and safety_interval is not None
        and float(correctness_interval.get("lower", 0.0)) >= -0.02
        and float(grounded_interval.get("lower", 0.0)) >= -0.02
        and float(safety_interval.get("lower", 0.0)) >= 0.0
    )

    cost_supported = (
        candidate["request_count"] >= 1
        and candidate["mean_request_cost_usd"] < baseline["mean_request_cost_usd"]
        and paired_cost_supported
        and reconciliation_supported
    )
    latency_supported = (
        candidate["request_count"] >= minimum_latency_samples
        and candidate["p50_latency_ms"] < baseline["p50_latency_ms"]
        and paired_latency_supported
    )
    quality_supported = (
        candidate["distinct_case_count"] >= minimum_broad_case_count
        and candidate["distinct_failure_type_count"] >= minimum_failure_type_count
        and candidate["correctness_pass_rate"] >= baseline["correctness_pass_rate"] - 0.02
        and candidate["grounded_citation_pass_rate"]
        >= baseline["grounded_citation_pass_rate"] - 0.02
        and candidate["safety_pass_rate"] >= baseline["safety_pass_rate"]
        and paired_quality_supported
    )
    production_latency_supported = environment_scope == "production" and latency_supported
    end_to_end_supported = (
        cost_supported
        and production_latency_supported
        and quality_supported
    )

    return {
        "real_api_cost_savings": {
            "supported": cost_supported,
            "reason": (
                "Mean request cost is lower than full_unaligned and the paired cost delta confidence interval stays below zero."
                if cost_supported
                else (
                    "Cost proof requires lower mean request cost than full_unaligned plus a paired cost delta confidence interval below zero."
                    if not reconciliation
                    else "Cost proof requires lower mean request cost than full_unaligned, a paired cost delta confidence interval below zero, and reconciled organization cost data within tolerance."
                )
            ),
        },
        "latency_improvement": {
            "supported": latency_supported,
            "reason": (
                f"p50 latency improved with at least {minimum_latency_samples} samples and the paired latency delta confidence interval stays below zero."
                if latency_supported
                else f"Latency claim requires at least {minimum_latency_samples} paired requests for the compared policy, a better p50 than full_unaligned, and a paired latency delta confidence interval below zero."
            ),
        },
        "latency_improvement_in_production": {
            "supported": production_latency_supported,
            "reason": (
                "The measured latency improvement came from a production-scoped dataset."
                if production_latency_supported
                else (
                    "The current dataset is not production-scoped."
                    if environment_scope != "production"
                    else f"Production latency proof still requires at least {minimum_latency_samples} production requests and a better p50 than full_unaligned."
                )
            ),
        },
        "broad_quality_retention": {
            "supported": quality_supported,
            "reason": (
                "Correctness, safety, and grounded-citation pass rates met the declared tolerance across a broad, diverse sample, including paired confidence interval checks."
                if quality_supported
                else (
                    "Broad quality-retention proof still requires "
                    f"{minimum_broad_case_count}+ distinct cases, "
                    f"{minimum_failure_type_count}+ failure types, "
                    "paired cases, and tolerance checks against the baseline policy."
                )
            ),
        },
        "end_to_end_context_overload": {
            "supported": end_to_end_supported,
            "reason": (
                "Cost, production-scope latency, and broad quality evidence all met the acceptance rule."
                if end_to_end_supported
                else (
                    "End-to-end proof requires positive cost reduction, production-scope latency improvement, "
                    "and no material quality regression across a broad workload set."
                )
            ),
        },
    }


def _record_completeness_audit(records: list[dict[str, Any]]) -> dict[str, Any]:
    required_grade_keys = (
        "correctness_pass",
        "safety_pass",
        "grounded_citation_pass",
        "overall_pass",
        "score",
    )
    missing_grade = 0
    missing_case_id = 0
    missing_policy_id = 0
    missing_failure_type = 0
    missing_latency_ms = 0
    missing_request_cost_usd = 0
    missing_input_tokens = 0
    missing_output_tokens = 0
    complete_record_count = 0

    for record in records:
        has_case_id = bool(str(record.get("case_id", "")).strip())
        has_policy_id = bool(str(record.get("policy_id", "")).strip())
        has_failure_type = bool(str(record.get("failure_type", "")).strip())
        has_latency_ms = record.get("latency_ms") not in (None, "")
        has_request_cost_usd = record.get("request_cost_usd") not in (None, "")
        has_input_tokens = record.get("input_tokens") not in (None, "")
        has_output_tokens = record.get("output_tokens") not in (None, "")

        if not str(record.get("case_id", "")).strip():
            missing_case_id += 1
        if not str(record.get("policy_id", "")).strip():
            missing_policy_id += 1
        if not str(record.get("failure_type", "")).strip():
            missing_failure_type += 1
        if record.get("latency_ms") in (None, ""):
            missing_latency_ms += 1
        if record.get("request_cost_usd") in (None, ""):
            missing_request_cost_usd += 1
        if record.get("input_tokens") in (None, ""):
            missing_input_tokens += 1
        if record.get("output_tokens") in (None, ""):
            missing_output_tokens += 1

        grade = record.get("grade")
        has_grade = isinstance(grade, dict) and all(key in grade for key in required_grade_keys)
        if not has_grade:
            missing_grade += 1
        if (
            has_case_id
            and has_policy_id
            and has_failure_type
            and has_latency_ms
            and has_request_cost_usd
            and has_input_tokens
            and has_output_tokens
            and has_grade
        ):
            complete_record_count += 1

    return {
        "record_count": len(records),
        "complete_grade_record_count": len(records) - missing_grade,
        "complete_grade_coverage_rate": round(
            ((len(records) - missing_grade) / len(records)),
            2,
        )
        if records
        else 0.0,
        "complete_record_count": complete_record_count,
        "missing_case_id": missing_case_id,
        "missing_policy_id": missing_policy_id,
        "missing_failure_type": missing_failure_type,
        "missing_latency_ms": missing_latency_ms,
        "missing_request_cost_usd": missing_request_cost_usd,
        "missing_input_tokens": missing_input_tokens,
        "missing_output_tokens": missing_output_tokens,
        "missing_grade": missing_grade,
    }


def _evidence_readiness(
    summary_by_policy: dict[str, dict[str, Any]],
    audit_requirements: dict[str, Any] | None,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    requirements = audit_requirements or {}
    minimum_latency_samples = int(requirements.get("recommended_warm_requests_per_policy", 30))
    minimum_broad_case_count = int(
        requirements.get("minimum_distinct_cases_for_broad_claim", 30)
    )
    minimum_failure_type_count = int(
        requirements.get("minimum_failure_types_for_broad_claim", 6)
    )
    required_environment_scope = str(requirements.get("environment_scope", "unknown"))

    baseline = summary_by_policy.get("full_unaligned", {})
    candidate = summary_by_policy.get("budgeted_cache_aligned", {})
    completeness = _record_completeness_audit(records)
    policy_ids = sorted(summary_by_policy.keys())

    baseline_cases = {
        str(item.get("case_id", "")).strip()
        for item in records
        if str(item.get("policy_id", "")).strip() == "full_unaligned"
        and str(item.get("case_id", "")).strip()
    }
    candidate_cases = {
        str(item.get("case_id", "")).strip()
        for item in records
        if str(item.get("policy_id", "")).strip() == "budgeted_cache_aligned"
        and str(item.get("case_id", "")).strip()
    }
    overlapping_cases = sorted(baseline_cases & candidate_cases)

    return {
        "policy_ids": policy_ids,
        "required_environment_scope": required_environment_scope,
        "observed_environment_scope": required_environment_scope,
        "production_scope_ready": required_environment_scope == "production",
        "required_latency_samples_per_policy": minimum_latency_samples,
        "baseline_latency_sample_count": int(baseline.get("request_count", 0)),
        "candidate_latency_sample_count": int(candidate.get("request_count", 0)),
        "latency_sample_requirement_met": (
            int(baseline.get("request_count", 0)) >= minimum_latency_samples
            and int(candidate.get("request_count", 0)) >= minimum_latency_samples
        ),
        "required_distinct_cases": minimum_broad_case_count,
        "candidate_distinct_cases": int(candidate.get("distinct_case_count", 0)),
        "broad_case_requirement_met": (
            int(candidate.get("distinct_case_count", 0)) >= minimum_broad_case_count
        ),
        "required_failure_types": minimum_failure_type_count,
        "candidate_distinct_failure_types": int(
            candidate.get("distinct_failure_type_count", 0)
        ),
        "failure_type_requirement_met": (
            int(candidate.get("distinct_failure_type_count", 0)) >= minimum_failure_type_count
        ),
        "baseline_candidate_case_overlap_count": len(overlapping_cases),
        "case_pairing_requirement_met": (
            len(overlapping_cases) >= minimum_broad_case_count
            and len(overlapping_cases) == len(candidate_cases)
            and len(overlapping_cases) == len(baseline_cases)
        ),
        "record_completeness": completeness,
    }


def _dataset_requirements(dataset: ProviderValidationDataset | None) -> dict[str, Any] | None:
    if dataset is None:
        return None
    return {
        "dataset_id": dataset.dataset_id,
        "environment_scope": dataset.environment_scope,
        "recommended_warm_requests_per_policy": dataset.recommended_warm_requests_per_policy,
        "minimum_distinct_cases_for_broad_claim": dataset.minimum_distinct_cases_for_broad_claim,
        "minimum_failure_types_for_broad_claim": dataset.minimum_failure_types_for_broad_claim,
    }


def _merge_dataset_requirements(
    dataset: ProviderValidationDataset | None,
    dataset_requirements: dict[str, Any] | None = None,
    *,
    environment_scope: str | None = None,
) -> dict[str, Any] | None:
    merged = dict(_dataset_requirements(dataset) or {})
    if dataset_requirements:
        merged.update(dataset_requirements)
    if environment_scope:
        merged["environment_scope"] = environment_scope
    return merged or None


def _coerce_int(value: Any) -> int:
    if value in (None, ""):
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    return int(str(value).strip())


def _coerce_float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    return float(str(value).strip())


def _coerce_optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    return int(str(value).strip())


def _extract_usage_from_record(record: dict[str, Any]) -> tuple[int, int, int]:
    if "usage" in record and isinstance(record["usage"], dict):
        usage = record["usage"]
        input_tokens = (
            usage.get("input_tokens")
            or usage.get("prompt_tokens")
            or usage.get("total_input_tokens")
            or 0
        )
        output_tokens = (
            usage.get("output_tokens")
            or usage.get("completion_tokens")
            or usage.get("total_output_tokens")
            or 0
        )
        details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
        cached_input_tokens = (
            details.get("cached_tokens")
            or details.get("cached_input_tokens")
            or usage.get("cached_input_tokens")
            or 0
        )
        return (
            _coerce_int(input_tokens),
            _coerce_int(cached_input_tokens),
            _coerce_int(output_tokens),
        )
    input_tokens = (
        record.get("input_tokens")
        or record.get("prompt_tokens")
        or record.get("total_input_tokens")
        or 0
    )
    cached_input_tokens = (
        record.get("cached_input_tokens")
        or record.get("cache_read_input_tokens")
        or record.get("cached_tokens")
        or 0
    )
    output_tokens = (
        record.get("output_tokens")
        or record.get("completion_tokens")
        or record.get("total_output_tokens")
        or 0
    )
    return (
        _coerce_int(input_tokens),
        _coerce_int(cached_input_tokens),
        _coerce_int(output_tokens),
    )


def _normalize_provider_record(
    raw_record: dict[str, Any],
    pricing: PricingSnapshot | None = None,
) -> dict[str, Any]:
    input_tokens, cached_input_tokens, output_tokens = _extract_usage_from_record(raw_record)
    request_cost_usd = raw_record.get("request_cost_usd")
    if request_cost_usd in (None, "") and pricing is not None:
        request_cost_usd = compute_request_cost(
            input_tokens,
            cached_input_tokens,
            output_tokens,
            pricing,
        )

    record = dict(raw_record)
    record["case_id"] = str(
        raw_record.get("case_id")
        or raw_record.get("agenvantage.case_id")
        or ""
    ).strip()
    record["policy_id"] = str(
        raw_record.get("policy_id")
        or raw_record.get("agenvantage.policy_id")
        or ""
    ).strip()
    record["failure_type"] = str(
        raw_record.get("failure_type")
        or raw_record.get("agenvantage.failure_type")
        or "unknown"
    ).strip() or "unknown"
    record["input_tokens"] = input_tokens
    record["cached_input_tokens"] = cached_input_tokens
    record["output_tokens"] = output_tokens
    record["latency_ms"] = round(
        _coerce_float(raw_record.get("latency_ms", raw_record.get("duration_ms", 0.0))),
        2,
    )
    record["request_cost_usd"] = round(_coerce_float(request_cost_usd), 8)
    started_at_unix_s = _coerce_optional_int(
        raw_record.get("started_at_unix_s")
        or raw_record.get("request_timestamp")
        or raw_record.get("created_at")
        or raw_record.get("timestamp")
    )
    if started_at_unix_s is not None:
        record["started_at_unix_s"] = started_at_unix_s

    if "repeat_index" in raw_record:
        record["repeat_index"] = _coerce_int(raw_record.get("repeat_index"))

    grade = raw_record.get("grade")
    if isinstance(grade, dict):
        record["grade"] = grade

    return record


def _otel_attribute_value(raw: dict[str, Any]) -> Any:
    if "stringValue" in raw:
        return raw["stringValue"]
    if "intValue" in raw:
        return raw["intValue"]
    if "doubleValue" in raw:
        return raw["doubleValue"]
    if "boolValue" in raw:
        return raw["boolValue"]
    if "arrayValue" in raw:
        values = raw.get("arrayValue", {}).get("values", [])
        return [_otel_attribute_value(item) for item in values if isinstance(item, dict)]
    if "kvlistValue" in raw:
        values = raw.get("kvlistValue", {}).get("values", [])
        result: dict[str, Any] = {}
        for item in values:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key", "")).strip()
            value = item.get("value")
            if key and isinstance(value, dict):
                result[key] = _otel_attribute_value(value)
        return result
    return None


def _flatten_otel_attributes(raw_attributes: Any) -> dict[str, Any]:
    if isinstance(raw_attributes, dict):
        return {
            str(key): value
            for key, value in raw_attributes.items()
        }
    attributes: dict[str, Any] = {}
    if not isinstance(raw_attributes, list):
        return attributes
    for item in raw_attributes:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key", "")).strip()
        value = item.get("value")
        if not key or not isinstance(value, dict):
            continue
        attributes[key] = _otel_attribute_value(value)
    return attributes


def _flatten_otel_spans(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        spans: list[dict[str, Any]] = []
        for item in payload:
            spans.extend(_flatten_otel_spans(item))
        return spans
    if not isinstance(payload, dict):
        return []
    if "resourceSpans" in payload:
        spans = []
        for resource_span in payload.get("resourceSpans", []):
            spans.extend(_flatten_otel_spans(resource_span))
        return spans
    if "scopeSpans" in payload:
        spans = []
        for scope_span in payload.get("scopeSpans", []):
            spans.extend(_flatten_otel_spans(scope_span))
        return spans
    if "spans" in payload:
        spans = []
        for span in payload.get("spans", []):
            if isinstance(span, dict):
                spans.append(span)
        return spans
    if "attributes" in payload and ("name" in payload or "startTimeUnixNano" in payload):
        return [payload]
    return []


def _latency_ms_from_otel_span(span: dict[str, Any]) -> float:
    if "latency_ms" in span or "duration_ms" in span:
        return round(_coerce_float(span.get("latency_ms", span.get("duration_ms", 0.0))), 2)
    start = span.get("startTimeUnixNano")
    end = span.get("endTimeUnixNano")
    if start not in (None, "") and end not in (None, ""):
        duration_ns = _coerce_float(end) - _coerce_float(start)
        if duration_ns > 0:
            return round(duration_ns / 1_000_000, 2)
    return 0.0


def _grade_from_otel_attributes(attributes: dict[str, Any]) -> dict[str, Any] | None:
    grade_keys = {
        "correctness_pass": "agenvantage.grade.correctness_pass",
        "safety_pass": "agenvantage.grade.safety_pass",
        "grounded_citation_pass": "agenvantage.grade.grounded_citation_pass",
        "overall_pass": "agenvantage.grade.overall_pass",
        "score": "agenvantage.grade.score",
    }
    if not any(key in attributes for key in grade_keys.values()):
        return None
    grade: dict[str, Any] = {}
    for output_key, attribute_key in grade_keys.items():
        if attribute_key in attributes:
            grade[output_key] = attributes[attribute_key]
    return grade


def _normalize_otel_span_record(
    span: dict[str, Any],
    pricing: PricingSnapshot | None = None,
) -> dict[str, Any]:
    attributes = _flatten_otel_attributes(span.get("attributes"))
    record: dict[str, Any] = {
        "case_id": str(attributes.get("agenvantage.case_id", "")).strip(),
        "policy_id": str(attributes.get("agenvantage.policy_id", "")).strip(),
        "failure_type": str(attributes.get("agenvantage.failure_type", "unknown")).strip()
        or "unknown",
        "repeat_index": _coerce_int(attributes.get("agenvantage.repeat_index", 0)),
        "dataset_id": str(attributes.get("agenvantage.dataset_id", "")).strip() or None,
        "environment_scope": str(attributes.get("agenvantage.environment_scope", "")).strip()
        or None,
        "model": str(
            attributes.get("gen_ai.response.model")
            or attributes.get("gen_ai.request.model")
            or ""
        ).strip()
        or None,
        "input_tokens": _coerce_int(attributes.get("gen_ai.usage.input_tokens")),
        "cached_input_tokens": _coerce_int(
            attributes.get("gen_ai.usage.cache_read.input_tokens")
            or attributes.get("gen_ai.usage.cached_input_tokens")
        ),
        "output_tokens": _coerce_int(attributes.get("gen_ai.usage.output_tokens")),
        "latency_ms": _latency_ms_from_otel_span(span),
    }
    started_at_unix_s = (
        round(_coerce_float(span.get("startTimeUnixNano")) / 1_000_000_000)
        if span.get("startTimeUnixNano") not in (None, "")
        else None
    )
    if started_at_unix_s is not None:
        record["started_at_unix_s"] = _coerce_int(started_at_unix_s)
    grade = _grade_from_otel_attributes(attributes)
    if grade is not None:
        record["grade"] = grade
    if "agenvantage.request_cost_usd" in attributes:
        record["request_cost_usd"] = round(
            _coerce_float(attributes.get("agenvantage.request_cost_usd")),
            8,
        )
    elif pricing is not None:
        record["request_cost_usd"] = compute_request_cost(
            record["input_tokens"],
            record["cached_input_tokens"],
            record["output_tokens"],
            pricing,
        )
    return record


def normalize_provider_records_payload(
    raw_payload: Any,
    pricing: PricingSnapshot | None = None,
) -> list[dict[str, Any]]:
    if isinstance(raw_payload, dict) and isinstance(raw_payload.get("records"), list):
        return [
            _normalize_provider_record(record, pricing)
            for record in raw_payload["records"]
            if isinstance(record, dict)
        ]
    if isinstance(raw_payload, dict) and isinstance(raw_payload.get("requests"), list):
        return [
            _normalize_provider_record(record, pricing)
            for record in raw_payload["requests"]
            if isinstance(record, dict)
        ]
    if isinstance(raw_payload, list) and all(isinstance(item, dict) for item in raw_payload):
        return [
            _normalize_provider_record(record, pricing)
            for record in raw_payload
        ]

    spans = _flatten_otel_spans(raw_payload)
    if spans:
        records = [
            _normalize_otel_span_record(span, pricing)
            for span in spans
            if _flatten_otel_attributes(span.get("attributes")).get("agenvantage.case_id")
            and _flatten_otel_attributes(span.get("attributes")).get("agenvantage.policy_id")
        ]
        if records:
            return records

    raise ValueError(
        "Unsupported provider-validation payload. Expected records, requests, or OTLP-style spans."
    )


def summarize_cost_api_buckets(raw_payload: Any) -> dict[str, Any]:
    if isinstance(raw_payload, dict) and isinstance(raw_payload.get("data"), list):
        buckets = [item for item in raw_payload["data"] if isinstance(item, dict)]
    elif isinstance(raw_payload, list):
        buckets = [item for item in raw_payload if isinstance(item, dict)]
    else:
        raise ValueError("Unsupported costs payload. Expected a Costs API response object or bucket list.")

    total_cost_usd = 0.0
    currencies: set[str] = set()
    project_ids: set[str] = set()
    line_item_totals: dict[str, float] = {}
    bucket_starts: list[int] = []
    bucket_ends: list[int] = []

    for bucket in buckets:
        start_time = _coerce_optional_int(bucket.get("start_time"))
        end_time = _coerce_optional_int(bucket.get("end_time"))
        if start_time is not None:
            bucket_starts.append(start_time)
        if end_time is not None:
            bucket_ends.append(end_time)
        for result in bucket.get("results", []):
            if not isinstance(result, dict):
                continue
            amount = result.get("amount") or {}
            value = _coerce_float(amount.get("value"))
            currency = str(amount.get("currency", "")).strip().lower()
            if currency:
                currencies.add(currency)
            total_cost_usd += value
            line_item = str(result.get("line_item", "")).strip() or "unattributed"
            line_item_totals[line_item] = round(line_item_totals.get(line_item, 0.0) + value, 8)
            project_id = str(result.get("project_id", "")).strip()
            if project_id:
                project_ids.add(project_id)

    return {
        "bucket_count": len(buckets),
        "start_time": min(bucket_starts) if bucket_starts else None,
        "end_time": max(bucket_ends) if bucket_ends else None,
        "currency": next(iter(currencies)) if len(currencies) == 1 else None,
        "currency_count": len(currencies),
        "total_cost_usd": round(total_cost_usd, 8),
        "project_ids": sorted(project_ids),
        "line_items": [
            {"line_item": line_item, "amount_usd": amount}
            for line_item, amount in sorted(line_item_totals.items())
        ],
    }


def reconcile_provider_costs(report: dict[str, Any], raw_costs_payload: Any) -> dict[str, Any]:
    cost_summary = summarize_cost_api_buckets(raw_costs_payload)
    records = [record for record in report.get("records", []) if isinstance(record, dict)]
    estimated_total_cost_usd = round(
        sum(_coerce_float(record.get("request_cost_usd")) for record in records),
        8,
    )
    timestamps = [
        timestamp
        for timestamp in (
            _coerce_optional_int(record.get("started_at_unix_s")) for record in records
        )
        if timestamp is not None
    ]
    estimated_start = min(timestamps) if timestamps else None
    estimated_end = max(timestamps) if timestamps else None
    recorded_total_cost_usd = _coerce_float(cost_summary.get("total_cost_usd"))
    difference_usd = round(recorded_total_cost_usd - estimated_total_cost_usd, 8)
    denominator = estimated_total_cost_usd or 1.0
    difference_ratio = round(difference_usd / denominator, 6)
    time_window_overlap = (
        estimated_start is not None
        and estimated_end is not None
        and cost_summary.get("start_time") is not None
        and cost_summary.get("end_time") is not None
        and estimated_start >= int(cost_summary["start_time"])
        and estimated_end <= int(cost_summary["end_time"])
    )
    return {
        "estimated_request_level_total_cost_usd": estimated_total_cost_usd,
        "recorded_organization_total_cost_usd": round(recorded_total_cost_usd, 8),
        "difference_usd": difference_usd,
        "difference_ratio_vs_estimate": difference_ratio,
        "experiment_request_count": len(records),
        "experiment_start_time": estimated_start,
        "experiment_end_time": estimated_end,
        "organization_cost_start_time": cost_summary.get("start_time"),
        "organization_cost_end_time": cost_summary.get("end_time"),
        "time_window_overlap": time_window_overlap,
        "project_ids": cost_summary.get("project_ids", []),
        "line_items": cost_summary.get("line_items", []),
        "bucket_count": cost_summary.get("bucket_count"),
        "currency": cost_summary.get("currency"),
    }


def summarize_provider_validation_records(
    records: list[dict[str, Any]],
    dataset: ProviderValidationDataset | None = None,
    dataset_requirements: dict[str, Any] | None = None,
    pricing: PricingSnapshot | None = None,
    cost_reconciliation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_requirements = _merge_dataset_requirements(dataset, dataset_requirements)
    paired_case_comparison = _paired_case_comparison(records)
    summary_by_policy: dict[str, dict[str, Any]] = {}
    for record in records:
        policy_id = str(record["policy_id"])
        bucket = summary_by_policy.setdefault(policy_id, {"records": []})
        bucket["records"].append(record)

    policies: dict[str, dict[str, Any]] = {}
    for policy_id, bucket in summary_by_policy.items():
        policy_records = bucket["records"]
        latencies = [float(item.get("latency_ms", 0.0)) for item in policy_records]
        input_tokens = [int(item.get("input_tokens", 0)) for item in policy_records]
        cached_tokens = [int(item.get("cached_input_tokens", 0)) for item in policy_records]
        output_tokens = [int(item.get("output_tokens", 0)) for item in policy_records]
        costs = [float(item.get("request_cost_usd", 0.0)) for item in policy_records]
        grades = [item.get("grade", {}) for item in policy_records]
        distinct_case_count = len({str(item.get("case_id", "")) for item in policy_records})
        distinct_failure_type_count = len(
            {str(item.get("failure_type", "")) for item in policy_records}
        )
        policies[policy_id] = {
            "request_count": len(policy_records),
            "distinct_case_count": distinct_case_count,
            "distinct_failure_type_count": distinct_failure_type_count,
            "mean_input_tokens": _mean([float(value) for value in input_tokens]),
            "mean_cached_input_tokens": _mean([float(value) for value in cached_tokens]),
            "mean_output_tokens": _mean([float(value) for value in output_tokens]),
            "mean_request_cost_usd": round(statistics.mean(costs), 8) if costs else 0.0,
            "cache_hit_rate": round(
                (
                    sum(1 for value in cached_tokens if value > 0)
                    / len(cached_tokens)
                ),
                2,
            )
            if cached_tokens
            else 0.0,
            "mean_cached_token_coverage": round(
                (
                    statistics.mean(
                        [
                            (cached / total) if total else 0.0
                            for cached, total in zip(cached_tokens, input_tokens)
                        ]
                    )
                ),
                2,
            )
            if cached_tokens
            else 0.0,
            "p50_latency_ms": _percentile(latencies, 0.50),
            "p95_latency_ms": _percentile(latencies, 0.95),
            "correctness_pass_rate": round(
                (
                    sum(1 for grade in grades if grade.get("correctness_pass"))
                    / len(grades)
                ),
                2,
            )
            if grades
            else 0.0,
            "safety_pass_rate": round(
                (sum(1 for grade in grades if grade.get("safety_pass")) / len(grades)),
                2,
            )
            if grades
            else 0.0,
            "grounded_citation_pass_rate": round(
                (
                    sum(1 for grade in grades if grade.get("grounded_citation_pass"))
                    / len(grades)
                ),
                2,
            )
            if grades
            else 0.0,
            "overall_pass_rate": round(
                (sum(1 for grade in grades if grade.get("overall_pass")) / len(grades)),
                2,
            )
            if grades
            else 0.0,
            "mean_grade_score": _mean(
                [float(grade.get("score", 0.0)) for grade in grades]
            ),
        }

    return {
        "dataset_id": (
            (resolved_requirements or {}).get("dataset_id")
            or (dataset.dataset_id if dataset else None)
        ),
        "environment_scope": (
            (resolved_requirements or {}).get("environment_scope")
            or (dataset.environment_scope if dataset else None)
        ),
        "dataset_requirements": resolved_requirements,
        "pricing_snapshot": pricing.to_dict() if pricing else None,
        "cost_reconciliation": cost_reconciliation,
        "record_count": len(records),
        "policies": policies,
        "paired_case_comparison": paired_case_comparison,
        "evidence_readiness": _evidence_readiness(
            policies,
            resolved_requirements,
            records,
        ),
        "claim_audit": _claim_audit(
            policies,
            resolved_requirements,
            paired_case_comparison,
            cost_reconciliation,
        ),
        "records": records,
    }


def summarize_saved_provider_validation_report(
    raw_report: dict[str, Any],
    dataset: ProviderValidationDataset | None = None,
    pricing: PricingSnapshot | None = None,
    environment_scope: str | None = None,
) -> dict[str, Any]:
    pricing_snapshot = pricing
    if pricing_snapshot is None and isinstance(raw_report.get("pricing_snapshot"), dict):
        pricing_snapshot = PricingSnapshot.from_dict(raw_report["pricing_snapshot"])

    return summarize_provider_validation_records(
        normalize_provider_records_payload(raw_report, pricing_snapshot),
        dataset=dataset,
        dataset_requirements=_merge_dataset_requirements(
            None,
            raw_report.get("dataset_requirements"),
            environment_scope=environment_scope,
        ),
        pricing=pricing_snapshot,
        cost_reconciliation=(
            raw_report.get("cost_reconciliation")
            if isinstance(raw_report.get("cost_reconciliation"), dict)
            else None
        ),
    )


def summarize_normalized_provider_validation_payload(
    raw_payload: Any,
    dataset: ProviderValidationDataset | None = None,
    pricing: PricingSnapshot | None = None,
    environment_scope: str | None = None,
) -> dict[str, Any]:
    pricing_snapshot = pricing
    if pricing_snapshot is None and isinstance(raw_payload, dict) and isinstance(
        raw_payload.get("pricing_snapshot"),
        dict,
    ):
        pricing_snapshot = PricingSnapshot.from_dict(raw_payload["pricing_snapshot"])

    dataset_requirements = None
    if isinstance(raw_payload, dict):
        dataset_requirements = raw_payload.get("dataset_requirements")

    return summarize_provider_validation_records(
        normalize_provider_records_payload(raw_payload, pricing_snapshot),
        dataset=dataset,
        dataset_requirements=_merge_dataset_requirements(
            None,
            dataset_requirements,
            environment_scope=environment_scope,
        ),
        pricing=pricing_snapshot,
        cost_reconciliation=(
            raw_payload.get("cost_reconciliation")
            if isinstance(raw_payload, dict)
            and isinstance(raw_payload.get("cost_reconciliation"), dict)
            else None
        ),
    )
