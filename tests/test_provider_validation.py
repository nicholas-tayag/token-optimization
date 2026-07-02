from __future__ import annotations

from pathlib import Path

from agenvantage.provider_validation import (
    PricingSnapshot,
    assemble_validation_packages,
    compute_request_cost,
    fixture_readiness_report,
    grade_provider_response,
    load_provider_validation_dataset,
    normalize_provider_records_payload,
    provider_validation_report_to_otel_export,
    reconcile_provider_costs,
    summarize_cost_api_buckets,
    summarize_normalized_provider_validation_payload,
    summarize_saved_provider_validation_report,
    summarize_provider_validation_records,
)
from agenvantage.tokenizer import TokenCounter


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "examples" / "provider_validation_cases.json"


def test_provider_fixture_is_cache_ready() -> None:
    dataset = load_provider_validation_dataset(FIXTURE)
    report = fixture_readiness_report(dataset, TokenCounter())

    assert report["case_count"] == 30
    assert report["environment_scope"] == "synthetic_local"
    assert report["all_cache_aligned_cases_cache_eligible"] is True
    assert report["all_budgeted_cache_aligned_cases_cache_eligible"] is True
    assert report["average_cache_aligned_stable_prefix_tokens"] >= 1024
    assert report["minimum_distinct_cases_for_broad_claim"] == 30
    assert report["minimum_failure_types_for_broad_claim"] == 6


def test_budgeted_cache_aligned_package_reduces_tokens() -> None:
    dataset = load_provider_validation_dataset(FIXTURE)
    case = dataset.cases[0]
    packages = assemble_validation_packages(dataset, case, TokenCounter())

    assert packages["full_unaligned"].input_tokens >= packages["budgeted_cache_aligned"].input_tokens
    assert (
        packages["full_cache_aligned"].stable_prefix_tokens
        >= dataset.minimum_cacheable_prefix_tokens
    )
    assert len({item.failure_type for item in dataset.cases}) >= 6


def test_grade_provider_response_checks_expected_fields() -> None:
    dataset = load_provider_validation_dataset(FIXTURE)
    case = dataset.cases[0]
    response_text = """
    {
      "affected_service": "checkoutservice",
      "suspected_failure": "payment_service_unreachable",
      "evidence_component_ids": ["case1-metric-checkout", "case1-trace-payment"],
      "recommended_next_action": "Verify paymentservice connectivity before requesting approval for any rollback.",
      "requires_approval": true,
      "tool_calls": [{"tool": "query_metrics", "arguments": {"service": "paymentservice", "metric": "timeout_rate", "window": "30m"}}]
    }
    """

    grade = grade_provider_response(
        case,
        response_text,
        {"case1-user-request", "case1-metric-checkout", "case1-trace-payment"},
    )

    assert grade.valid_json is True
    assert grade.correct_service is True
    assert grade.correct_failure is True
    assert grade.grounded_citation_pass is True
    assert grade.tool_selection is True
    assert grade.overall_pass is True


def test_compute_request_cost_uses_cached_discount() -> None:
    pricing = PricingSnapshot(
        provider="openai",
        model="gpt-test",
        captured_at="2026-06-30",
        source_url="https://developers.openai.com/api/docs/pricing",
        input_price_per_million=1.0,
        cached_input_price_per_million=0.1,
        output_price_per_million=2.0,
    )

    cost = compute_request_cost(
        input_tokens=2000,
        cached_input_tokens=1500,
        output_tokens=500,
        pricing=pricing,
    )

    assert cost == 0.00165


def test_claim_audit_requires_latency_and_quality_evidence() -> None:
    pricing = PricingSnapshot(
        provider="openai",
        model="gpt-test",
        captured_at="2026-06-30",
        source_url="https://developers.openai.com/api/docs/pricing",
        input_price_per_million=1.0,
        cached_input_price_per_million=0.1,
        output_price_per_million=2.0,
    )
    dataset = load_provider_validation_dataset(FIXTURE)
    baseline_grade = {
        "correctness_pass": True,
        "safety_pass": True,
        "grounded_citation_pass": True,
        "overall_pass": True,
        "score": 1.0,
    }
    candidate_grade = {
        "correctness_pass": True,
        "safety_pass": True,
        "grounded_citation_pass": True,
        "overall_pass": True,
        "score": 1.0,
    }
    records = [
        {
            "policy_id": "full_unaligned",
            "case_id": "baseline-case",
            "failure_type": "payment_service_unreachable",
            "latency_ms": 900.0,
            "input_tokens": 2200,
            "cached_input_tokens": 0,
            "output_tokens": 220,
            "request_cost_usd": 0.00264,
            "grade": baseline_grade,
        },
        {
            "policy_id": "budgeted_cache_aligned",
            "case_id": "candidate-case",
            "failure_type": "payment_service_unreachable",
            "latency_ms": 700.0,
            "input_tokens": 1800,
            "cached_input_tokens": 1200,
            "output_tokens": 220,
            "request_cost_usd": 0.00156,
            "grade": candidate_grade,
        },
    ]

    report = summarize_provider_validation_records(records, dataset=dataset, pricing=pricing)

    assert report["claim_audit"]["real_api_cost_savings"]["supported"] is False
    assert report["claim_audit"]["latency_improvement"]["supported"] is False
    assert report["claim_audit"]["latency_improvement_in_production"]["supported"] is False
    assert report["claim_audit"]["broad_quality_retention"]["supported"] is False
    assert report["claim_audit"]["end_to_end_context_overload"]["supported"] is False
    assert report["evidence_readiness"]["production_scope_ready"] is False
    assert report["evidence_readiness"]["latency_sample_requirement_met"] is False
    assert report["evidence_readiness"]["broad_case_requirement_met"] is False
    assert report["evidence_readiness"]["record_completeness"]["complete_grade_record_count"] == 2


def test_claim_audit_cost_savings_requires_paired_case_evidence() -> None:
    pricing = PricingSnapshot(
        provider="openai",
        model="gpt-test",
        captured_at="2026-06-30",
        source_url="https://developers.openai.com/api/docs/pricing",
        input_price_per_million=1.0,
        cached_input_price_per_million=0.1,
        output_price_per_million=2.0,
    )
    dataset = load_provider_validation_dataset(FIXTURE)
    perfect_grade = {
        "correctness_pass": True,
        "safety_pass": True,
        "grounded_citation_pass": True,
        "overall_pass": True,
        "score": 1.0,
    }
    case = dataset.cases[0]
    records = [
        {
            "policy_id": "full_unaligned",
            "case_id": case.case_id,
            "failure_type": case.failure_type,
            "latency_ms": 900.0,
            "input_tokens": 2200,
            "cached_input_tokens": 0,
            "output_tokens": 220,
            "request_cost_usd": 0.00264,
            "grade": perfect_grade,
        },
        {
            "policy_id": "budgeted_cache_aligned",
            "case_id": case.case_id,
            "failure_type": case.failure_type,
            "latency_ms": 700.0,
            "input_tokens": 1700,
            "cached_input_tokens": 1200,
            "output_tokens": 220,
            "request_cost_usd": 0.00136,
            "grade": perfect_grade,
        },
    ]

    report = summarize_provider_validation_records(records, dataset=dataset, pricing=pricing)

    assert report["paired_case_comparison"]["overlapping_case_count"] == 1
    assert report["claim_audit"]["real_api_cost_savings"]["supported"] is True
    assert report["claim_audit"]["latency_improvement"]["supported"] is False


def test_saved_provider_report_preserves_dataset_requirements_for_replay() -> None:
    dataset = load_provider_validation_dataset(FIXTURE)
    pricing = PricingSnapshot(
        provider="openai",
        model="gpt-test",
        captured_at="2026-07-01",
        source_url="https://developers.openai.com/api/docs/pricing",
        input_price_per_million=1.0,
        cached_input_price_per_million=0.1,
        output_price_per_million=2.0,
    )
    perfect_grade = {
        "correctness_pass": True,
        "safety_pass": True,
        "grounded_citation_pass": True,
        "overall_pass": True,
        "score": 1.0,
    }
    records = []
    for index, case in enumerate(dataset.cases):
        records.append(
            {
                "policy_id": "full_unaligned",
                "case_id": case.case_id,
                "failure_type": case.failure_type,
                "latency_ms": 920.0,
                "input_tokens": 2200,
                "cached_input_tokens": 0,
                "output_tokens": 220,
                "request_cost_usd": 0.00264,
                "grade": perfect_grade,
            }
        )
        records.append(
            {
                "policy_id": "budgeted_cache_aligned",
                "case_id": case.case_id,
                "failure_type": case.failure_type,
                "latency_ms": 700.0,
                "input_tokens": 1700,
                "cached_input_tokens": 1200,
                "output_tokens": 220,
                "request_cost_usd": 0.00136,
                "grade": perfect_grade,
            }
        )

    saved_report = summarize_provider_validation_records(
        records,
        dataset=dataset,
        pricing=pricing,
        cost_reconciliation={
            "estimated_request_level_total_cost_usd": 0.12,
            "recorded_organization_total_cost_usd": 0.12,
            "difference_usd": 0.0,
            "difference_ratio_vs_estimate": 0.0,
            "time_window_overlap": True,
        },
    )
    replay_report = summarize_saved_provider_validation_report(saved_report)

    assert replay_report["dataset_requirements"]["minimum_distinct_cases_for_broad_claim"] == 30
    assert replay_report["claim_audit"]["real_api_cost_savings"]["supported"] is True
    assert replay_report["claim_audit"]["broad_quality_retention"]["supported"] is True
    assert replay_report["claim_audit"]["latency_improvement_in_production"]["supported"] is False
    assert replay_report["cost_reconciliation"]["difference_usd"] == 0.0
    assert replay_report["paired_case_comparison"]["overlapping_case_count"] == 30
    assert (
        replay_report["paired_case_comparison"]["metrics"]["request_cost_usd"]["mean_delta"] < 0
    )


def test_normalize_plain_provider_records_computes_costs() -> None:
    pricing = PricingSnapshot(
        provider="openai",
        model="gpt-test",
        captured_at="2026-07-02",
        source_url="https://developers.openai.com/api/docs/pricing",
        input_price_per_million=1.0,
        cached_input_price_per_million=0.1,
        output_price_per_million=2.0,
    )

    records = normalize_provider_records_payload(
        [
            {
                "case_id": "checkout-payment-connectivity-a",
                "policy_id": "budgeted_cache_aligned",
                "failure_type": "payment_service_unreachable",
                "latency_ms": 712.4,
                "usage": {
                    "input_tokens": 1700,
                    "output_tokens": 220,
                    "input_tokens_details": {"cached_tokens": 1200},
                },
            }
        ],
        pricing,
    )

    assert records[0]["input_tokens"] == 1700
    assert records[0]["cached_input_tokens"] == 1200
    assert records[0]["output_tokens"] == 220
    assert records[0]["request_cost_usd"] == 0.00106


def test_normalize_otel_export_supports_production_scope_override() -> None:
    dataset = load_provider_validation_dataset(FIXTURE)
    pricing = PricingSnapshot(
        provider="openai",
        model="gpt-test",
        captured_at="2026-07-02",
        source_url="https://developers.openai.com/api/docs/pricing",
        input_price_per_million=1.0,
        cached_input_price_per_million=0.1,
        output_price_per_million=2.0,
    )
    spans = []
    for case in dataset.cases:
        spans.append(
            {
                "name": "gen_ai.request",
                "startTimeUnixNano": "1000000000",
                "endTimeUnixNano": "1950000000",
                "attributes": [
                    {"key": "agenvantage.case_id", "value": {"stringValue": case.case_id}},
                    {
                        "key": "agenvantage.failure_type",
                        "value": {"stringValue": case.failure_type},
                    },
                    {"key": "agenvantage.policy_id", "value": {"stringValue": "full_unaligned"}},
                    {"key": "gen_ai.request.model", "value": {"stringValue": "gpt-test"}},
                    {"key": "gen_ai.usage.input_tokens", "value": {"intValue": "2200"}},
                    {"key": "gen_ai.usage.output_tokens", "value": {"intValue": "220"}},
                    {
                        "key": "agenvantage.grade.correctness_pass",
                        "value": {"boolValue": True},
                    },
                    {"key": "agenvantage.grade.safety_pass", "value": {"boolValue": True}},
                    {
                        "key": "agenvantage.grade.grounded_citation_pass",
                        "value": {"boolValue": True},
                    },
                    {"key": "agenvantage.grade.overall_pass", "value": {"boolValue": True}},
                    {"key": "agenvantage.grade.score", "value": {"doubleValue": 1.0}},
                ],
            }
        )
        spans.append(
            {
                "name": "gen_ai.request",
                "startTimeUnixNano": "1000000000",
                "endTimeUnixNano": "1730000000",
                "attributes": [
                    {"key": "agenvantage.case_id", "value": {"stringValue": case.case_id}},
                    {
                        "key": "agenvantage.failure_type",
                        "value": {"stringValue": case.failure_type},
                    },
                    {
                        "key": "agenvantage.policy_id",
                        "value": {"stringValue": "budgeted_cache_aligned"},
                    },
                    {"key": "gen_ai.request.model", "value": {"stringValue": "gpt-test"}},
                    {"key": "gen_ai.usage.input_tokens", "value": {"intValue": "1700"}},
                    {
                        "key": "gen_ai.usage.cache_read.input_tokens",
                        "value": {"intValue": "1200"},
                    },
                    {"key": "gen_ai.usage.output_tokens", "value": {"intValue": "220"}},
                    {
                        "key": "agenvantage.grade.correctness_pass",
                        "value": {"boolValue": True},
                    },
                    {"key": "agenvantage.grade.safety_pass", "value": {"boolValue": True}},
                    {
                        "key": "agenvantage.grade.grounded_citation_pass",
                        "value": {"boolValue": True},
                    },
                    {"key": "agenvantage.grade.overall_pass", "value": {"boolValue": True}},
                    {"key": "agenvantage.grade.score", "value": {"doubleValue": 1.0}},
                ],
            }
        )

    report = summarize_normalized_provider_validation_payload(
        {"resourceSpans": [{"scopeSpans": [{"spans": spans}]}]},
        dataset=dataset,
        pricing=pricing,
        environment_scope="production",
    )

    assert report["environment_scope"] == "production"
    assert report["claim_audit"]["real_api_cost_savings"]["supported"] is True
    assert report["claim_audit"]["latency_improvement"]["supported"] is True
    assert report["claim_audit"]["latency_improvement_in_production"]["supported"] is True
    assert report["claim_audit"]["broad_quality_retention"]["supported"] is True
    assert report["claim_audit"]["end_to_end_context_overload"]["supported"] is True
    assert report["evidence_readiness"]["production_scope_ready"] is True
    assert report["evidence_readiness"]["latency_sample_requirement_met"] is True
    assert report["paired_case_comparison"]["overlapping_case_count"] == 30
    request_cost_interval = report["paired_case_comparison"]["metrics"]["request_cost_usd"][
        "confidence_interval"
    ]
    assert request_cost_interval is not None
    assert request_cost_interval["upper"] < 0
    latency_interval = report["paired_case_comparison"]["metrics"]["latency_ms"][
        "confidence_interval"
    ]
    assert latency_interval is not None
    assert latency_interval["upper"] < 0


def test_normalize_otel_metrics_supports_production_scope_override() -> None:
    dataset = load_provider_validation_dataset(FIXTURE)
    pricing = PricingSnapshot(
        provider="openai",
        model="gpt-test",
        captured_at="2026-07-02",
        source_url="https://developers.openai.com/api/docs/pricing",
        input_price_per_million=1.0,
        cached_input_price_per_million=0.1,
        output_price_per_million=2.0,
    )
    metric_points = {
        "gen_ai.client.operation.duration": [],
        "gen_ai.usage.input_tokens": [],
        "gen_ai.usage.cache_read.input_tokens": [],
        "gen_ai.usage.output_tokens": [],
    }

    for index, case in enumerate(dataset.cases):
        for policy_id, latency_ms, input_tokens, cached_input_tokens, output_tokens in (
            ("full_unaligned", 950.0, 2200, 0, 220),
            ("budgeted_cache_aligned", 730.0, 1700, 1200, 220),
        ):
            timestamp_ns = str((1736643600 + index + (0 if policy_id == "full_unaligned" else 3600)) * 1_000_000_000)
            attributes = [
                {"key": "agenvantage.case_id", "value": {"stringValue": case.case_id}},
                {"key": "agenvantage.failure_type", "value": {"stringValue": case.failure_type}},
                {"key": "agenvantage.policy_id", "value": {"stringValue": policy_id}},
                {"key": "gen_ai.request.model", "value": {"stringValue": "gpt-test"}},
                {"key": "agenvantage.grade.correctness_pass", "value": {"boolValue": True}},
                {"key": "agenvantage.grade.safety_pass", "value": {"boolValue": True}},
                {
                    "key": "agenvantage.grade.grounded_citation_pass",
                    "value": {"boolValue": True},
                },
                {"key": "agenvantage.grade.overall_pass", "value": {"boolValue": True}},
                {"key": "agenvantage.grade.score", "value": {"doubleValue": 1.0}},
            ]
            metric_points["gen_ai.client.operation.duration"].append(
                {
                    "timeUnixNano": timestamp_ns,
                    "asDouble": latency_ms,
                    "attributes": attributes,
                }
            )
            metric_points["gen_ai.usage.input_tokens"].append(
                {
                    "timeUnixNano": timestamp_ns,
                    "asInt": str(input_tokens),
                    "attributes": attributes,
                }
            )
            metric_points["gen_ai.usage.cache_read.input_tokens"].append(
                {
                    "timeUnixNano": timestamp_ns,
                    "asInt": str(cached_input_tokens),
                    "attributes": attributes,
                }
            )
            metric_points["gen_ai.usage.output_tokens"].append(
                {
                    "timeUnixNano": timestamp_ns,
                    "asInt": str(output_tokens),
                    "attributes": attributes,
                }
            )

    report = summarize_normalized_provider_validation_payload(
        {
            "resourceMetrics": [
                {
                    "scopeMetrics": [
                        {
                            "metrics": [
                                {
                                    "name": metric_name,
                                    "unit": "ms" if metric_name == "gen_ai.client.operation.duration" else "1",
                                    "gauge": {"dataPoints": data_points},
                                }
                                for metric_name, data_points in metric_points.items()
                            ]
                        }
                    ]
                }
            ]
        },
        dataset=dataset,
        pricing=pricing,
        environment_scope="production",
    )

    assert report["environment_scope"] == "production"
    assert report["claim_audit"]["real_api_cost_savings"]["supported"] is True
    assert report["claim_audit"]["latency_improvement"]["supported"] is True
    assert report["claim_audit"]["latency_improvement_in_production"]["supported"] is True
    assert report["claim_audit"]["broad_quality_retention"]["supported"] is True
    assert report["claim_audit"]["end_to_end_context_overload"]["supported"] is True
    assert report["paired_case_comparison"]["overlapping_case_count"] == 30


def test_normalize_combined_otel_logs_and_metrics_merges_fields() -> None:
    pricing = PricingSnapshot(
        provider="openai",
        model="gpt-test",
        captured_at="2026-07-02",
        source_url="https://developers.openai.com/api/docs/pricing",
        input_price_per_million=1.0,
        cached_input_price_per_million=0.1,
        output_price_per_million=2.0,
    )
    payload = {
        "resourceLogs": [
            {
                "scopeLogs": [
                    {
                        "logRecords": [
                            {
                                "timeUnixNano": "1736643600000000000",
                                "attributes": [
                                    {
                                        "key": "agenvantage.case_id",
                                        "value": {"stringValue": "checkout-payment-connectivity-a"},
                                    },
                                    {
                                        "key": "agenvantage.failure_type",
                                        "value": {"stringValue": "payment_service_unreachable"},
                                    },
                                    {
                                        "key": "agenvantage.policy_id",
                                        "value": {"stringValue": "budgeted_cache_aligned"},
                                    },
                                    {
                                        "key": "agenvantage.grade.correctness_pass",
                                        "value": {"boolValue": True},
                                    },
                                    {
                                        "key": "agenvantage.grade.safety_pass",
                                        "value": {"boolValue": True},
                                    },
                                    {
                                        "key": "agenvantage.grade.grounded_citation_pass",
                                        "value": {"boolValue": True},
                                    },
                                    {
                                        "key": "agenvantage.grade.overall_pass",
                                        "value": {"boolValue": True},
                                    },
                                    {
                                        "key": "agenvantage.grade.score",
                                        "value": {"doubleValue": 1.0},
                                    },
                                ],
                            }
                        ]
                    }
                ]
            }
        ],
        "resourceMetrics": [
            {
                "scopeMetrics": [
                    {
                        "metrics": [
                            {
                                "name": "gen_ai.client.operation.duration",
                                "unit": "ms",
                                "gauge": {
                                    "dataPoints": [
                                        {
                                            "timeUnixNano": "1736643600000000000",
                                            "asDouble": 730.0,
                                            "attributes": [
                                                {
                                                    "key": "agenvantage.case_id",
                                                    "value": {"stringValue": "checkout-payment-connectivity-a"},
                                                },
                                                {
                                                    "key": "agenvantage.failure_type",
                                                    "value": {
                                                        "stringValue": "payment_service_unreachable"
                                                    },
                                                },
                                                {
                                                    "key": "agenvantage.policy_id",
                                                    "value": {"stringValue": "budgeted_cache_aligned"},
                                                },
                                                {
                                                    "key": "gen_ai.request.model",
                                                    "value": {"stringValue": "gpt-test"},
                                                },
                                            ],
                                        }
                                    ]
                                },
                            },
                            {
                                "name": "gen_ai.usage.input_tokens",
                                "unit": "1",
                                "gauge": {
                                    "dataPoints": [
                                        {
                                            "timeUnixNano": "1736643600000000000",
                                            "asInt": "1700",
                                            "attributes": [
                                                {
                                                    "key": "agenvantage.case_id",
                                                    "value": {"stringValue": "checkout-payment-connectivity-a"},
                                                },
                                                {
                                                    "key": "agenvantage.failure_type",
                                                    "value": {
                                                        "stringValue": "payment_service_unreachable"
                                                    },
                                                },
                                                {
                                                    "key": "agenvantage.policy_id",
                                                    "value": {"stringValue": "budgeted_cache_aligned"},
                                                },
                                            ],
                                        }
                                    ]
                                },
                            },
                            {
                                "name": "gen_ai.usage.cache_read.input_tokens",
                                "unit": "1",
                                "gauge": {
                                    "dataPoints": [
                                        {
                                            "timeUnixNano": "1736643600000000000",
                                            "asInt": "1200",
                                            "attributes": [
                                                {
                                                    "key": "agenvantage.case_id",
                                                    "value": {"stringValue": "checkout-payment-connectivity-a"},
                                                },
                                                {
                                                    "key": "agenvantage.failure_type",
                                                    "value": {
                                                        "stringValue": "payment_service_unreachable"
                                                    },
                                                },
                                                {
                                                    "key": "agenvantage.policy_id",
                                                    "value": {"stringValue": "budgeted_cache_aligned"},
                                                },
                                            ],
                                        }
                                    ]
                                },
                            },
                            {
                                "name": "gen_ai.usage.output_tokens",
                                "unit": "1",
                                "gauge": {
                                    "dataPoints": [
                                        {
                                            "timeUnixNano": "1736643600000000000",
                                            "asInt": "220",
                                            "attributes": [
                                                {
                                                    "key": "agenvantage.case_id",
                                                    "value": {"stringValue": "checkout-payment-connectivity-a"},
                                                },
                                                {
                                                    "key": "agenvantage.failure_type",
                                                    "value": {
                                                        "stringValue": "payment_service_unreachable"
                                                    },
                                                },
                                                {
                                                    "key": "agenvantage.policy_id",
                                                    "value": {"stringValue": "budgeted_cache_aligned"},
                                                },
                                            ],
                                        }
                                    ]
                                },
                            },
                        ]
                    }
                ]
            }
        ],
    }

    records = normalize_provider_records_payload(payload, pricing)

    assert len(records) == 1
    assert records[0]["policy_id"] == "budgeted_cache_aligned"
    assert records[0]["latency_ms"] == 730.0
    assert records[0]["input_tokens"] == 1700
    assert records[0]["cached_input_tokens"] == 1200
    assert records[0]["output_tokens"] == 220
    assert records[0]["request_cost_usd"] == 0.00106
    assert records[0]["grade"]["overall_pass"] is True


def test_evidence_readiness_tracks_missing_grade_fields() -> None:
    dataset = load_provider_validation_dataset(FIXTURE)
    records = [
        {
            "policy_id": "full_unaligned",
            "case_id": dataset.cases[0].case_id,
            "failure_type": dataset.cases[0].failure_type,
            "latency_ms": 950.0,
            "input_tokens": 2200,
            "cached_input_tokens": 0,
            "output_tokens": 220,
            "request_cost_usd": 0.00264,
        },
        {
            "policy_id": "budgeted_cache_aligned",
            "case_id": dataset.cases[0].case_id,
            "failure_type": dataset.cases[0].failure_type,
            "latency_ms": 730.0,
            "input_tokens": 1700,
            "cached_input_tokens": 1200,
            "output_tokens": 220,
            "request_cost_usd": 0.00136,
        },
    ]

    report = summarize_provider_validation_records(records, dataset=dataset)

    completeness = report["evidence_readiness"]["record_completeness"]
    assert completeness["missing_grade"] == 2
    assert completeness["complete_grade_record_count"] == 0
    assert completeness["complete_record_count"] == 0


def test_summarize_cost_api_buckets_and_reconcile_provider_costs() -> None:
    dataset = load_provider_validation_dataset(FIXTURE)
    case = dataset.cases[0]
    report = summarize_provider_validation_records(
        [
            {
                "policy_id": "full_unaligned",
                "case_id": case.case_id,
                "failure_type": case.failure_type,
                "started_at_unix_s": 1736643600,
                "latency_ms": 950.0,
                "input_tokens": 2200,
                "cached_input_tokens": 0,
                "output_tokens": 220,
                "request_cost_usd": 0.00264,
                "grade": {
                    "correctness_pass": True,
                    "safety_pass": True,
                    "grounded_citation_pass": True,
                    "overall_pass": True,
                    "score": 1.0,
                },
            },
            {
                "policy_id": "budgeted_cache_aligned",
                "case_id": case.case_id,
                "failure_type": case.failure_type,
                "started_at_unix_s": 1736643660,
                "latency_ms": 730.0,
                "input_tokens": 1700,
                "cached_input_tokens": 1200,
                "output_tokens": 220,
                "request_cost_usd": 0.00136,
                "grade": {
                    "correctness_pass": True,
                    "safety_pass": True,
                    "grounded_citation_pass": True,
                    "overall_pass": True,
                    "score": 1.0,
                },
            },
        ],
        dataset=dataset,
    )
    costs_payload = {
        "data": [
            {
                "object": "bucket",
                "start_time": 1736640000,
                "end_time": 1736726400,
                "results": [
                    {
                        "object": "organization.costs.result",
                        "amount": {"value": 0.004, "currency": "usd"},
                        "line_item": "responses",
                        "project_id": "proj_eval",
                    }
                ],
            }
        ]
    }

    cost_summary = summarize_cost_api_buckets(costs_payload)
    assert cost_summary["total_cost_usd"] == 0.004
    assert cost_summary["project_ids"] == ["proj_eval"]

    reconciliation = reconcile_provider_costs(report, costs_payload)
    assert reconciliation["estimated_request_level_total_cost_usd"] == 0.004
    assert reconciliation["recorded_organization_total_cost_usd"] == 0.004
    assert reconciliation["difference_usd"] == 0.0
    assert reconciliation["time_window_overlap"] is True


def test_claim_audit_rejects_bad_cost_reconciliation() -> None:
    dataset = load_provider_validation_dataset(FIXTURE)
    case = dataset.cases[0]
    report = summarize_provider_validation_records(
        [
            {
                "policy_id": "full_unaligned",
                "case_id": case.case_id,
                "failure_type": case.failure_type,
                "started_at_unix_s": 1736643600,
                "latency_ms": 950.0,
                "input_tokens": 2200,
                "cached_input_tokens": 0,
                "output_tokens": 220,
                "request_cost_usd": 0.00264,
                "grade": {
                    "correctness_pass": True,
                    "safety_pass": True,
                    "grounded_citation_pass": True,
                    "overall_pass": True,
                    "score": 1.0,
                },
            },
            {
                "policy_id": "budgeted_cache_aligned",
                "case_id": case.case_id,
                "failure_type": case.failure_type,
                "started_at_unix_s": 1736643660,
                "latency_ms": 730.0,
                "input_tokens": 1700,
                "cached_input_tokens": 1200,
                "output_tokens": 220,
                "request_cost_usd": 0.00136,
                "grade": {
                    "correctness_pass": True,
                    "safety_pass": True,
                    "grounded_citation_pass": True,
                    "overall_pass": True,
                    "score": 1.0,
                },
            },
        ],
        dataset=dataset,
        cost_reconciliation={
            "estimated_request_level_total_cost_usd": 0.004,
            "recorded_organization_total_cost_usd": 0.01,
            "difference_usd": 0.006,
            "difference_ratio_vs_estimate": 1.5,
            "time_window_overlap": True,
        },
    )

    assert report["claim_audit"]["real_api_cost_savings"]["supported"] is False


def test_provider_validation_otel_export_round_trips() -> None:
    dataset = load_provider_validation_dataset(FIXTURE)
    pricing = PricingSnapshot(
        provider="openai",
        model="gpt-test",
        captured_at="2026-07-02",
        source_url="https://developers.openai.com/api/docs/pricing",
        input_price_per_million=1.0,
        cached_input_price_per_million=0.1,
        output_price_per_million=2.0,
    )
    perfect_grade = {
        "correctness_pass": True,
        "safety_pass": True,
        "grounded_citation_pass": True,
        "overall_pass": True,
        "score": 1.0,
    }
    records = []
    for index, case in enumerate(dataset.cases):
        records.append(
            {
                "policy_id": "full_unaligned",
                "case_id": case.case_id,
                "failure_type": case.failure_type,
                "started_at_unix_s": 1736643600 + index,
                "latency_ms": 950.0,
                "input_tokens": 2200,
                "cached_input_tokens": 0,
                "output_tokens": 220,
                "request_cost_usd": 0.00264,
                "grade": perfect_grade,
            }
        )
        records.append(
            {
                "policy_id": "budgeted_cache_aligned",
                "case_id": case.case_id,
                "failure_type": case.failure_type,
                "started_at_unix_s": 1736647200 + index,
                "latency_ms": 730.0,
                "input_tokens": 1700,
                "cached_input_tokens": 1200,
                "output_tokens": 220,
                "request_cost_usd": 0.00136,
                "grade": perfect_grade,
            }
        )

    report = summarize_provider_validation_records(
        records,
        dataset=dataset,
        dataset_requirements={"environment_scope": "production"},
        pricing=pricing,
    )
    otel_export = provider_validation_report_to_otel_export(report)
    replay = summarize_normalized_provider_validation_payload(
        otel_export,
        dataset=dataset,
        pricing=pricing,
        environment_scope="production",
    )

    assert replay["claim_audit"]["real_api_cost_savings"]["supported"] is True
    assert replay["claim_audit"]["latency_improvement_in_production"]["supported"] is True
    assert replay["claim_audit"]["broad_quality_retention"]["supported"] is True
    assert replay["paired_case_comparison"]["overlapping_case_count"] == 30
