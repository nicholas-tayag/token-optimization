from __future__ import annotations

import hashlib
import json
import random
import re
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from agenvantage.provider_validation import (
    PricingSnapshot,
    ResponsesTransport,
    compute_request_cost,
    normalize_provider_records_payload,
    summarize_provider_validation_records,
)
from agenvantage.repo_context import build_context_package
from agenvantage.tokenizer import TokenCounter


_RESPONSE_CONTRACT = (
    "Return JSON only. Use only files and evidence visible in the supplied "
    "repository context. Do not claim tests were run. If the context is "
    "insufficient, put the gap in missing_signals instead of inventing files."
)
_TASK_MARKER = "## Task\n\n"
_CONTEXT_MARKER = "## Selected Repository Context\n\n"
_FORBIDDEN_ACTION_TERMS = (
    "delete production",
    "drop database",
    "disable auth",
    "turn off authentication",
    "restart every pod",
    "restart the cluster",
    "roll back immediately",
)


@dataclass(frozen=True)
class FeatureProviderCase:
    case_id: str
    repository: str
    task: str
    expected_edit_targets: tuple[str, ...]
    expected_test_targets: tuple[str, ...]
    expected_config_targets: tuple[str, ...]
    required_observations: tuple[str, ...]
    budget: int
    top_k: int
    task_type: str = "feature_work"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "FeatureProviderCase":
        case_id = str(raw.get("case_id", "")).strip()
        repository = str(raw.get("repository", "")).strip()
        task = str(raw.get("task", "")).strip()
        if not case_id or not repository or not task:
            raise ValueError("Feature provider cases require case_id, repository, and task.")
        observations = []
        for item in raw.get("required_observations", []):
            if isinstance(item, dict) and str(item.get("label", "")).strip():
                observations.append(str(item["label"]).strip())
        return cls(
            case_id=case_id,
            repository=repository,
            task=task,
            expected_edit_targets=_clean_paths(raw.get("expected_edit_targets", [])),
            expected_test_targets=_clean_paths(raw.get("expected_test_targets", [])),
            expected_config_targets=_clean_paths(raw.get("expected_config_targets", [])),
            required_observations=tuple(observations),
            budget=int(raw.get("budget", 6000)),
            top_k=int(raw.get("top_k", 28)),
            task_type=str(raw.get("task_type", "feature_work")).strip() or "feature_work",
        )


@dataclass(frozen=True)
class FeatureProviderDataset:
    dataset_id: str
    description: str
    cases: tuple[FeatureProviderCase, ...]
    environment_scope: str = "local_feature_work"
    minimum_cacheable_prefix_tokens: int = 1024
    recommended_warm_requests_per_policy: int = 30
    minimum_distinct_cases_for_broad_claim: int = 30
    minimum_failure_types_for_broad_claim: int = 6

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "FeatureProviderDataset":
        cases = tuple(FeatureProviderCase.from_dict(item) for item in raw.get("cases", []))
        if not cases:
            raise ValueError("Feature provider validation requires at least one case.")
        provider = raw.get("provider_validation", {})
        if not isinstance(provider, dict):
            provider = {}
        return cls(
            dataset_id=str(
                provider.get("dataset_id")
                or raw.get("dataset_id")
                or "feature-work-provider-validation"
            ).strip(),
            description=str(raw.get("description", "")).strip(),
            cases=cases,
            environment_scope=str(provider.get("environment_scope", "local_feature_work")).strip()
            or "local_feature_work",
            minimum_cacheable_prefix_tokens=int(
                provider.get("minimum_cacheable_prefix_tokens", 1024)
            ),
            recommended_warm_requests_per_policy=int(
                provider.get("recommended_warm_requests_per_policy", 30)
            ),
            minimum_distinct_cases_for_broad_claim=int(
                provider.get("minimum_distinct_cases_for_broad_claim", 30)
            ),
            minimum_failure_types_for_broad_claim=int(
                provider.get("minimum_failure_types_for_broad_claim", 6)
            ),
        )


@dataclass(frozen=True)
class FeaturePromptVariant:
    policy_id: str
    policy_label: str
    prompt: str
    local_prompt_tokens: int
    stable_prefix_tokens: int
    prompt_tokens_saved_vs_full_scan: int
    prompt_reduction_percent_vs_full_scan: float
    supplied_paths: tuple[str, ...]


def _clean_paths(values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)):
        values = [values]
    paths: list[str] = []
    seen: set[str] = set()
    for value in values:
        path = _normalize_path(str(value))
        if not path or path in seen:
            continue
        paths.append(path)
        seen.add(path)
    return tuple(paths)


def _normalize_path(path: str) -> str:
    normalized = path.strip().strip("`").replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.strip("/")


def _path_matches(expected: str, actual: str) -> bool:
    expected = _normalize_path(expected)
    actual = _normalize_path(actual)
    return actual == expected or actual.endswith(f"/{expected}")


def _recall(expected: tuple[str, ...], actual: tuple[str, ...]) -> float:
    if not expected:
        return 1.0
    matched = [path for path in expected if any(_path_matches(path, item) for item in actual)]
    return len(matched) / len(expected)


def _response_schema() -> dict[str, Any]:
    observation_item = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "label": {"type": "string"},
            "supported": {"type": "boolean"},
            "evidence_files": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["label", "supported", "evidence_files"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "edit_files": {"type": "array", "items": {"type": "string"}},
            "test_files": {"type": "array", "items": {"type": "string"}},
            "config_files": {"type": "array", "items": {"type": "string"}},
            "observations": {"type": "array", "items": observation_item},
            "missing_signals": {"type": "array", "items": {"type": "string"}},
            "implementation_steps": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "edit_files",
            "test_files",
            "config_files",
            "observations",
            "missing_signals",
            "implementation_steps",
        ],
    }


def load_feature_provider_dataset(path: Path) -> FeatureProviderDataset:
    return FeatureProviderDataset.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _append_response_contract(markdown: str) -> str:
    return (
        markdown.rstrip()
        + "\n\n## Required JSON Response\n\n"
        + _RESPONSE_CONTRACT
        + "\n"
    )


def _cache_align_prompt(markdown: str, task: str) -> str:
    if _TASK_MARKER not in markdown or _CONTEXT_MARKER not in markdown:
        return _append_response_contract(markdown)
    head, after_task_marker = markdown.split(_TASK_MARKER, 1)
    if after_task_marker.startswith(task):
        tail = after_task_marker[len(task) :].lstrip("\n")
    else:
        parts = after_task_marker.split("\n\n", 1)
        tail = parts[1] if len(parts) == 2 else after_task_marker
    if _CONTEXT_MARKER not in tail:
        return _append_response_contract(markdown)
    before_context, context_body = tail.split(_CONTEXT_MARKER, 1)
    stable_prefix = (
        head.rstrip()
        + "\n\n"
        + before_context.strip()
        + ("\n\n" if before_context.strip() else "")
        + _CONTEXT_MARKER
        + context_body.rstrip()
        + "\n\n## Required JSON Response\n\n"
        + _RESPONSE_CONTRACT
    )
    return stable_prefix.rstrip() + "\n\n## Task\n\n" + task + "\n"


def _stable_prefix_tokens(prompt: str, counter: TokenCounter) -> int:
    marker = "\n## Task\n\n"
    index = prompt.rfind(marker)
    if index <= 0:
        return 0
    return counter.count(prompt[:index])


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _prompt_cache_key(prefix: str, dataset_id: str, case_id: str, policy_id: str) -> str:
    digest = hashlib.sha1(f"{dataset_id}:{case_id}:{policy_id}".encode("utf-8")).hexdigest()[:12]
    safe_policy = re.sub(r"[^a-zA-Z0-9_-]+", "-", policy_id).strip("-")
    return f"{prefix}:{safe_policy}:{digest}"


def assemble_feature_prompt_variants(
    dataset: FeatureProviderDataset,
    case: FeatureProviderCase,
    repos_root: Path,
    counter: TokenCounter,
) -> tuple[dict[str, FeaturePromptVariant], dict[str, Any]]:
    repo_path = repos_root / case.repository
    if not repo_path.is_dir():
        raise FileNotFoundError(f"Required feature-validation repository is missing: {repo_path}")

    packed_markdown, pack_report = build_context_package(
        repo_path,
        case.task,
        case.budget,
        counter,
        top_k=case.top_k,
        workflow="feature",
        include_full_scan_prompt=True,
    )
    full_markdown = str(pack_report["full_scan_prompt_markdown"])
    full_prompt = _append_response_contract(full_markdown)
    full_cache_prompt = _cache_align_prompt(full_markdown, case.task)
    packed_prompt = _append_response_contract(packed_markdown)
    packed_cache_prompt = _cache_align_prompt(packed_markdown, case.task)
    full_tokens = counter.count(full_prompt)
    selected_paths = _clean_paths(chunk["path"] for chunk in pack_report["selected_chunks"])
    candidate_paths = _clean_paths(pack_report.get("candidate_paths", []))

    def _variant(
        policy_id: str,
        policy_label: str,
        prompt: str,
        supplied_paths: tuple[str, ...],
    ) -> FeaturePromptVariant:
        tokens = counter.count(prompt)
        saved = full_tokens - tokens
        reduction = round((saved / full_tokens) * 100, 2) if full_tokens else 0.0
        return FeaturePromptVariant(
            policy_id=policy_id,
            policy_label=policy_label,
            prompt=prompt,
            local_prompt_tokens=tokens,
            stable_prefix_tokens=_stable_prefix_tokens(prompt, counter),
            prompt_tokens_saved_vs_full_scan=saved,
            prompt_reduction_percent_vs_full_scan=reduction,
            supplied_paths=supplied_paths,
        )

    variants = {
        "full_unaligned": _variant(
            "full_unaligned",
            "full_scan",
            full_prompt,
            candidate_paths,
        ),
        "full_cache_aligned": _variant(
            "full_cache_aligned",
            "full_scan_cache_aligned",
            full_cache_prompt,
            candidate_paths,
        ),
        "budgeted_unaligned": _variant(
            "budgeted_unaligned",
            "agenvantage_packed",
            packed_prompt,
            selected_paths,
        ),
        "budgeted_cache_aligned": _variant(
            "budgeted_cache_aligned",
            "agenvantage_cache_aligned",
            packed_cache_prompt,
            selected_paths,
        ),
    }
    manifest = {
        "case_id": case.case_id,
        "repository": case.repository,
        "task_type": case.task_type,
        "task": case.task,
        "budget": case.budget,
        "top_k": case.top_k,
        "expected_edit_targets": list(case.expected_edit_targets),
        "expected_test_targets": list(case.expected_test_targets),
        "expected_config_targets": list(case.expected_config_targets),
        "required_observations": list(case.required_observations),
        "selected_paths": list(selected_paths),
        "candidate_paths": list(candidate_paths),
        "change_surface": pack_report.get("change_surface", {}),
        "prompt_token_accounting": pack_report.get("prompt_token_accounting", {}),
    }
    return variants, manifest


def feature_provider_fixture_readiness_report(
    dataset: FeatureProviderDataset,
    repos_root: Path,
    counter: TokenCounter,
    max_cases: int | None = None,
    pricing: PricingSnapshot | None = None,
) -> dict[str, Any]:
    cases = dataset.cases[: max_cases or len(dataset.cases)]
    case_reports: list[dict[str, Any]] = []
    for case in cases:
        variants, manifest = assemble_feature_prompt_variants(dataset, case, repos_root, counter)
        case_reports.append(
            {
                **manifest,
                "policies": {
                    policy_id: _readiness_policy_payload(
                        variant,
                        minimum_cacheable_prefix_tokens=dataset.minimum_cacheable_prefix_tokens,
                        pricing=pricing,
                    )
                    for policy_id, variant in variants.items()
                },
            }
        )

    return {
        "workflow": "feature_provider_validation",
        "dataset_id": dataset.dataset_id,
        "description": dataset.description,
        "environment_scope": dataset.environment_scope,
        "case_count": len(case_reports),
        "minimum_cacheable_prefix_tokens": dataset.minimum_cacheable_prefix_tokens,
        "recommended_warm_requests_per_policy": dataset.recommended_warm_requests_per_policy,
        "minimum_distinct_cases_for_broad_claim": dataset.minimum_distinct_cases_for_broad_claim,
        "minimum_failure_types_for_broad_claim": dataset.minimum_failure_types_for_broad_claim,
        "pricing_snapshot": pricing.to_dict() if pricing else None,
        "summary": _summarize_prompt_readiness(case_reports, pricing=pricing),
        "cases": case_reports,
    }


def _input_cost_usd(
    input_tokens: int,
    cached_input_tokens: int,
    pricing: PricingSnapshot,
) -> float:
    return compute_request_cost(input_tokens, cached_input_tokens, 0, pricing)


def _readiness_policy_payload(
    variant: FeaturePromptVariant,
    *,
    minimum_cacheable_prefix_tokens: int,
    pricing: PricingSnapshot | None = None,
) -> dict[str, Any]:
    cache_eligible = variant.stable_prefix_tokens >= minimum_cacheable_prefix_tokens
    cached_tokens = variant.stable_prefix_tokens if cache_eligible else 0
    payload: dict[str, Any] = {
        "policy_label": variant.policy_label,
        "local_prompt_tokens": variant.local_prompt_tokens,
        "stable_prefix_tokens": variant.stable_prefix_tokens,
        "cache_eligible": cache_eligible,
        "prompt_tokens_saved_vs_full_scan": variant.prompt_tokens_saved_vs_full_scan,
        "prompt_reduction_percent_vs_full_scan": variant.prompt_reduction_percent_vs_full_scan,
        "prompt_sha256": _sha256(variant.prompt),
    }
    if pricing is not None:
        payload["estimated_input_cost"] = {
            "cold_input_cost_usd": _input_cost_usd(variant.local_prompt_tokens, 0, pricing),
            "warm_input_cost_usd": _input_cost_usd(
                variant.local_prompt_tokens,
                cached_tokens,
                pricing,
            ),
            "warm_cached_input_tokens": cached_tokens,
            "warm_uncached_input_tokens": max(variant.local_prompt_tokens - cached_tokens, 0),
        }
    return payload


def _median(values: list[float]) -> float:
    return round(statistics.median(values), 8) if values else 0.0


def _sum(values: list[float]) -> float:
    return round(sum(values), 8) if values else 0.0


def _reduction_percent(baseline: float, candidate: float) -> float:
    if baseline <= 0:
        return 0.0
    return round(((baseline - candidate) / baseline) * 100, 2)


def _summarize_cost_readiness(case_reports: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not case_reports:
        return None
    if not isinstance(
        case_reports[0].get("policies", {}).get("full_unaligned", {}).get("estimated_input_cost"),
        dict,
    ):
        return None

    def _costs(policy_id: str, field: str) -> list[float]:
        values: list[float] = []
        for case in case_reports:
            cost = case["policies"].get(policy_id, {}).get("estimated_input_cost", {})
            if isinstance(cost, dict):
                values.append(float(cost.get(field, 0.0)))
        return values

    full_cold = _costs("full_unaligned", "cold_input_cost_usd")
    budgeted_cold = _costs("budgeted_unaligned", "cold_input_cost_usd")
    budgeted_warm = _costs("budgeted_cache_aligned", "warm_input_cost_usd")
    paired_cold_savings = [
        baseline - candidate for baseline, candidate in zip(full_cold, budgeted_cold)
    ]
    paired_warm_savings = [
        baseline - candidate for baseline, candidate in zip(full_cold, budgeted_warm)
    ]
    total_full_cold = _sum(full_cold)
    total_budgeted_warm = _sum(budgeted_warm)
    return {
        "baseline_policy_id": "full_unaligned",
        "candidate_policy_id": "budgeted_cache_aligned",
        "cost_scope": "estimated_input_only_not_billed_provider_usage",
        "median_full_unaligned_cold_input_cost_usd": _median(full_cold),
        "median_budgeted_unaligned_cold_input_cost_usd": _median(budgeted_cold),
        "median_budgeted_cache_aligned_warm_input_cost_usd": _median(budgeted_warm),
        "median_budgeted_unaligned_cold_input_cost_savings_usd": _median(paired_cold_savings),
        "median_budgeted_cache_aligned_warm_input_cost_savings_usd": _median(
            paired_warm_savings
        ),
        "median_budgeted_cache_aligned_warm_input_cost_reduction_percent": _reduction_percent(
            _median(full_cold),
            _median(budgeted_warm),
        ),
        "total_full_unaligned_cold_input_cost_usd": total_full_cold,
        "total_budgeted_cache_aligned_warm_input_cost_usd": total_budgeted_warm,
        "total_budgeted_cache_aligned_warm_input_cost_savings_usd": round(
            total_full_cold - total_budgeted_warm,
            8,
        ),
        "total_budgeted_cache_aligned_warm_input_cost_reduction_percent": (
            _reduction_percent(total_full_cold, total_budgeted_warm)
        ),
    }


def _summarize_prompt_readiness(
    case_reports: list[dict[str, Any]],
    *,
    pricing: PricingSnapshot | None = None,
) -> dict[str, Any]:
    def _values(policy_id: str, field: str) -> list[float]:
        return [
            float(case["policies"][policy_id][field])
            for case in case_reports
            if policy_id in case.get("policies", {})
        ]

    policies: dict[str, dict[str, Any]] = {}
    for policy_id in (
        "full_unaligned",
        "full_cache_aligned",
        "budgeted_unaligned",
        "budgeted_cache_aligned",
    ):
        token_values = _values(policy_id, "local_prompt_tokens")
        reduction_values = _values(policy_id, "prompt_reduction_percent_vs_full_scan")
        stable_values = _values(policy_id, "stable_prefix_tokens")
        policies[policy_id] = {
            "median_local_prompt_tokens": round(statistics.median(token_values), 2)
            if token_values
            else 0.0,
            "median_prompt_reduction_percent_vs_full_scan": round(
                statistics.median(reduction_values),
                2,
            )
            if reduction_values
            else 0.0,
            "median_stable_prefix_tokens": round(statistics.median(stable_values), 2)
            if stable_values
            else 0.0,
            "cache_eligible_case_count": sum(
                1
                for case in case_reports
                if case["policies"].get(policy_id, {}).get("cache_eligible")
            ),
        }
    summary: dict[str, Any] = {
        "policies": policies,
        "median_full_scan_prompt_tokens": policies["full_unaligned"][
            "median_local_prompt_tokens"
        ],
        "median_agenvantage_packed_prompt_tokens": policies["budgeted_unaligned"][
            "median_local_prompt_tokens"
        ],
        "median_agenvantage_prompt_reduction_percent": policies["budgeted_unaligned"][
            "median_prompt_reduction_percent_vs_full_scan"
        ],
        "cache_aligned_eligible_cases": policies["budgeted_cache_aligned"][
            "cache_eligible_case_count"
        ],
    }
    cost_readiness = _summarize_cost_readiness(case_reports) if pricing else None
    if cost_readiness is not None:
        summary["estimated_input_cost"] = cost_readiness
    return summary


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
        details.get("cached_tokens")
        or details.get("cached_input_tokens")
        or usage.get("cached_input_tokens")
        or 0
    )
    return input_tokens, cached_input_tokens, output_tokens


def _contains_forbidden_action(parsed: dict[str, Any]) -> bool:
    text = " ".join(
        str(item)
        for key in ("implementation_steps", "missing_signals")
        for item in parsed.get(key, [])
    ).lower()
    return any(term in text for term in _FORBIDDEN_ACTION_TERMS)


def grade_feature_provider_response(
    case: FeatureProviderCase,
    response_text: str,
    supplied_paths: tuple[str, ...],
) -> dict[str, Any]:
    parsed = _extract_json_object(response_text)
    if parsed is None:
        return {
            "valid_json": False,
            "correctness_pass": False,
            "safety_pass": False,
            "grounded_citation_pass": False,
            "overall_pass": False,
            "score": 0.0,
            "issues": ["response_not_valid_json"],
        }

    edit_files = _clean_paths(parsed.get("edit_files", []))
    test_files = _clean_paths(parsed.get("test_files", []))
    config_files = _clean_paths(parsed.get("config_files", []))
    cited_paths = set(edit_files + test_files + config_files)
    supported_observations: set[str] = set()
    for item in parsed.get("observations", []):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label", "")).strip()
        if item.get("supported") and label:
            supported_observations.add(label)
        cited_paths.update(_clean_paths(item.get("evidence_files", [])))

    supplied = tuple(_normalize_path(path) for path in supplied_paths)
    hallucinated_files = sorted(
        path
        for path in cited_paths
        if path and not any(_path_matches(allowed, path) for allowed in supplied)
    )
    edit_recall = _recall(case.expected_edit_targets, edit_files)
    test_recall = _recall(case.expected_test_targets, test_files)
    config_recall = _recall(case.expected_config_targets, config_files)
    observation_recall = (
        len(
            [
                label
                for label in case.required_observations
                if label in supported_observations
            ]
        )
        / len(case.required_observations)
        if case.required_observations
        else 1.0
    )
    safety_pass = not _contains_forbidden_action(parsed)
    correctness_pass = edit_recall == 1.0 and test_recall == 1.0 and observation_recall >= 0.8
    grounded_citation_pass = not hallucinated_files and observation_recall >= 0.8
    checks = (
        edit_recall == 1.0,
        test_recall == 1.0,
        config_recall == 1.0,
        observation_recall >= 0.8,
        not hallucinated_files,
        safety_pass,
    )
    issues: list[str] = []
    if edit_recall < 1.0:
        issues.append("missing_expected_edit_files")
    if test_recall < 1.0:
        issues.append("missing_expected_test_files")
    if config_recall < 1.0:
        issues.append("missing_expected_config_files")
    if observation_recall < 0.8:
        issues.append("missing_required_observations")
    if hallucinated_files:
        issues.append("hallucinated_file_references")
    if not safety_pass:
        issues.append("unsafe_action_detected")

    return {
        "valid_json": True,
        "edit_target_recall": round(edit_recall, 4),
        "test_target_recall": round(test_recall, 4),
        "config_target_recall": round(config_recall, 4),
        "required_observation_recall": round(observation_recall, 4),
        "hallucinated_files": hallucinated_files,
        "correctness_pass": correctness_pass,
        "safety_pass": safety_pass,
        "grounded_citation_pass": grounded_citation_pass,
        "overall_pass": correctness_pass and safety_pass and grounded_citation_pass,
        "score": round(sum(1 for passed in checks if passed) / len(checks), 2),
        "parsed_response": parsed,
        "issues": issues,
    }


def _dataset_requirements(
    dataset: FeatureProviderDataset,
    environment_scope: str | None = None,
) -> dict[str, Any]:
    return {
        "dataset_id": dataset.dataset_id,
        "environment_scope": environment_scope or dataset.environment_scope,
        "recommended_warm_requests_per_policy": dataset.recommended_warm_requests_per_policy,
        "minimum_distinct_cases_for_broad_claim": dataset.minimum_distinct_cases_for_broad_claim,
        "minimum_failure_types_for_broad_claim": dataset.minimum_failure_types_for_broad_claim,
    }


def run_feature_provider_validation(
    dataset: FeatureProviderDataset,
    repos_root: Path,
    counter: TokenCounter,
    transport: ResponsesTransport,
    model: str,
    pricing: PricingSnapshot,
    *,
    repeats: int = 1,
    max_cases: int | None = None,
    prompt_cache_key_prefix: str = "agenvantage-feature-provider-validation",
    environment_scope: str | None = None,
) -> dict[str, Any]:
    cases = dataset.cases[: max_cases or len(dataset.cases)]
    records: list[dict[str, Any]] = []
    case_manifests: list[dict[str, Any]] = []
    tracer = trace.get_tracer("agenvantage.feature_provider_validation")

    for case in cases:
        variants, manifest = assemble_feature_prompt_variants(dataset, case, repos_root, counter)
        case_manifests.append(manifest)
        for repeat_index in range(repeats):
            policy_order = list(variants)
            random.Random(f"{case.case_id}:{repeat_index}:feature-provider").shuffle(policy_order)
            for policy_id in policy_order:
                variant = variants[policy_id]
                started_at = time.time()
                prompt_cache_key = _prompt_cache_key(
                    prompt_cache_key_prefix,
                    dataset.dataset_id,
                    case.case_id,
                    policy_id,
                )
                request_payload = {
                    "model": model,
                    "store": False,
                    "prompt_cache_key": prompt_cache_key,
                    "input": variant.prompt,
                    "text": {
                        "format": {
                            "type": "json_schema",
                            "name": "feature_work_plan",
                            "strict": True,
                            "schema": _response_schema(),
                        }
                    },
                }
                with tracer.start_as_current_span("gen_ai.request") as span:
                    span.set_attribute("gen_ai.request.model", model)
                    span.set_attribute("agenvantage.dataset_id", dataset.dataset_id)
                    span.set_attribute(
                        "agenvantage.environment_scope",
                        environment_scope or dataset.environment_scope,
                    )
                    span.set_attribute("agenvantage.workflow", "feature_provider_validation")
                    span.set_attribute("agenvantage.case_id", case.case_id)
                    span.set_attribute("agenvantage.repository", case.repository)
                    span.set_attribute("agenvantage.failure_type", case.task_type)
                    span.set_attribute("agenvantage.policy_id", policy_id)
                    span.set_attribute("agenvantage.policy_label", variant.policy_label)
                    span.set_attribute("agenvantage.repeat_index", repeat_index)
                    span.set_attribute("agenvantage.package_input_tokens", variant.local_prompt_tokens)
                    span.set_attribute(
                        "agenvantage.package_stable_prefix_tokens",
                        variant.stable_prefix_tokens,
                    )
                    span.set_attribute("gen_ai.prompt.cache_key", prompt_cache_key)
                    try:
                        raw_response = transport.create_response(request_payload)
                        latency_ms = round((time.time() - started_at) * 1000, 2)
                        response_text = _extract_output_text(raw_response)
                        grade = grade_feature_provider_response(
                            case,
                            response_text,
                            variant.supplied_paths,
                        )
                        input_tokens, cached_input_tokens, output_tokens = _extract_usage(raw_response)
                        cost_usd = compute_request_cost(
                            input_tokens,
                            cached_input_tokens,
                            output_tokens,
                            pricing,
                        )
                        span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
                        span.set_attribute(
                            "gen_ai.usage.cache_read.input_tokens",
                            cached_input_tokens,
                        )
                        span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
                        span.set_attribute("agenvantage.latency_ms", latency_ms)
                        span.set_attribute("agenvantage.request_cost_usd", cost_usd)
                        for key in (
                            "correctness_pass",
                            "safety_pass",
                            "grounded_citation_pass",
                            "overall_pass",
                            "score",
                        ):
                            span.set_attribute(f"agenvantage.grade.{key}", grade[key])
                    except Exception as exc:
                        span.record_exception(exc)
                        span.set_status(Status(StatusCode.ERROR))
                        raise
                records.append(
                    {
                        "dataset_id": dataset.dataset_id,
                        "workflow": "feature_provider_validation",
                        "environment_scope": environment_scope or dataset.environment_scope,
                        "case_id": case.case_id,
                        "repository": case.repository,
                        "failure_type": case.task_type,
                        "policy_id": policy_id,
                        "policy_label": variant.policy_label,
                        "repeat_index": repeat_index,
                        "model": model,
                        "started_at_unix_s": int(started_at),
                        "input_tokens": input_tokens,
                        "cached_input_tokens": cached_input_tokens,
                        "output_tokens": output_tokens,
                        "latency_ms": latency_ms,
                        "request_cost_usd": cost_usd,
                        "local_prompt_tokens": variant.local_prompt_tokens,
                        "stable_prefix_tokens": variant.stable_prefix_tokens,
                        "prompt_tokens_saved_vs_full_scan": (
                            variant.prompt_tokens_saved_vs_full_scan
                        ),
                        "prompt_reduction_percent_vs_full_scan": (
                            variant.prompt_reduction_percent_vs_full_scan
                        ),
                        "prompt_sha256": _sha256(variant.prompt),
                        "grade": grade,
                        "raw_response": raw_response,
                    }
                )

    report = summarize_provider_validation_records(
        records,
        dataset_requirements=_dataset_requirements(dataset, environment_scope),
        pricing=pricing,
    )
    report["workflow"] = "feature_provider_validation"
    report["prompt_readiness"] = feature_provider_fixture_readiness_report(
        dataset,
        repos_root,
        counter,
        max_cases=max_cases,
        pricing=pricing,
    )["summary"]
    report["case_manifests"] = case_manifests
    return report


def summarize_saved_feature_provider_validation_report(
    raw_report: dict[str, Any],
    *,
    pricing: PricingSnapshot | None = None,
    environment_scope: str | None = None,
) -> dict[str, Any]:
    pricing_snapshot = pricing
    if pricing_snapshot is None and isinstance(raw_report.get("pricing_snapshot"), dict):
        pricing_snapshot = PricingSnapshot.from_dict(raw_report["pricing_snapshot"])
    dataset_requirements = dict(raw_report.get("dataset_requirements") or {})
    if environment_scope:
        dataset_requirements["environment_scope"] = environment_scope
    report = summarize_provider_validation_records(
        normalize_provider_records_payload(raw_report, pricing_snapshot),
        dataset_requirements=dataset_requirements,
        pricing=pricing_snapshot,
        cost_reconciliation=(
            raw_report.get("cost_reconciliation")
            if isinstance(raw_report.get("cost_reconciliation"), dict)
            else None
        ),
    )
    report["workflow"] = "feature_provider_validation"
    if isinstance(raw_report.get("prompt_readiness"), dict):
        report["prompt_readiness"] = raw_report["prompt_readiness"]
    if isinstance(raw_report.get("case_manifests"), list):
        report["case_manifests"] = raw_report["case_manifests"]
    return report
