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


def _claim_audit(
    summary_by_policy: dict[str, dict[str, Any]],
    audit_requirements: dict[str, Any] | None,
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

    cost_supported = (
        candidate["request_count"] >= 1
        and candidate["mean_request_cost_usd"] < baseline["mean_request_cost_usd"]
    )
    latency_supported = (
        candidate["request_count"] >= minimum_latency_samples
        and candidate["p50_latency_ms"] < baseline["p50_latency_ms"]
    )
    quality_supported = (
        candidate["distinct_case_count"] >= minimum_broad_case_count
        and candidate["distinct_failure_type_count"] >= minimum_failure_type_count
        and candidate["correctness_pass_rate"] >= baseline["correctness_pass_rate"] - 0.02
        and candidate["grounded_citation_pass_rate"]
        >= baseline["grounded_citation_pass_rate"] - 0.02
        and candidate["safety_pass_rate"] >= baseline["safety_pass_rate"]
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
                "Mean request cost is lower than full_unaligned."
                if cost_supported
                else "No measured mean request-cost reduction versus full_unaligned."
            ),
        },
        "latency_improvement": {
            "supported": latency_supported,
            "reason": (
                f"p50 latency improved with at least {minimum_latency_samples} samples."
                if latency_supported
                else f"Latency claim requires at least {minimum_latency_samples} requests for the compared policy and a better p50 than full_unaligned."
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
                "Correctness, safety, and grounded-citation pass rates met the declared tolerance across a broad, diverse sample."
                if quality_supported
                else (
                    "Broad quality-retention proof still requires "
                    f"{minimum_broad_case_count}+ distinct cases, "
                    f"{minimum_failure_type_count}+ failure types, "
                    "and tolerance checks against the baseline policy."
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


def summarize_provider_validation_records(
    records: list[dict[str, Any]],
    dataset: ProviderValidationDataset | None = None,
    dataset_requirements: dict[str, Any] | None = None,
    pricing: PricingSnapshot | None = None,
) -> dict[str, Any]:
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
        "dataset_id": dataset.dataset_id if dataset else None,
        "environment_scope": dataset.environment_scope if dataset else None,
        "dataset_requirements": (
            _dataset_requirements(dataset) if dataset is not None else dataset_requirements
        ),
        "pricing_snapshot": pricing.to_dict() if pricing else None,
        "record_count": len(records),
        "policies": policies,
        "claim_audit": _claim_audit(
            policies,
            _dataset_requirements(dataset) if dataset is not None else dataset_requirements,
        ),
        "records": records,
    }


def summarize_saved_provider_validation_report(
    raw_report: dict[str, Any],
    dataset: ProviderValidationDataset | None = None,
    pricing: PricingSnapshot | None = None,
) -> dict[str, Any]:
    pricing_snapshot = pricing
    if pricing_snapshot is None and isinstance(raw_report.get("pricing_snapshot"), dict):
        pricing_snapshot = PricingSnapshot.from_dict(raw_report["pricing_snapshot"])

    return summarize_provider_validation_records(
        raw_report.get("records", []),
        dataset=dataset,
        dataset_requirements=raw_report.get("dataset_requirements"),
        pricing=pricing_snapshot,
    )
