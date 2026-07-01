#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = REPO_ROOT / "examples" / "provider_validation_cases.json"


SHARED_COMPONENTS = [
    {
        "id": "response-contract",
        "kind": "instruction",
        "required": True,
        "stable": True,
        "priority": 100,
        "relevance": 1.0,
        "text": "Return only JSON with these keys: affected_service, suspected_failure, evidence_component_ids, recommended_next_action, requires_approval, and tool_calls. The affected_service value must name the service that a responder should investigate first. The suspected_failure value must use the closest matching failure label from the supplied incident packet. evidence_component_ids must cite only component ids that appear in the context package and must include the strongest two or three pieces of evidence, not every component in the package. recommended_next_action must be safe, reversible, and grounded in the cited evidence. requires_approval must be true whenever the next action could change production behavior. tool_calls must list only read-only investigation calls and must include the arguments needed to inspect the likely failing service.",
    },
    {
        "id": "citation-reminders",
        "kind": "instruction",
        "required": True,
        "stable": True,
        "priority": 99,
        "relevance": 1.0,
        "text": "Citations matter more than prose polish. Prefer the smallest set of evidence ids that still proves timing and mechanism. If one metric shows the symptom and one trace or log shows the cause, cite both. Do not cite distractor memory items, historical incidents, or unrelated services just to make the answer look thorough. Tool calls should target the likely failing service, not the user-facing symptom alone.",
    },
    {
        "id": "tool-catalog",
        "kind": "tools",
        "required": True,
        "stable": True,
        "priority": 95,
        "relevance": 1.0,
        "text": "Available read-only tools are query_metrics(service, metric, window), search_logs(service, query, window), get_feature_flag(flag_name), and get_recent_deployments(service, window). query_metrics returns aggregated service metrics for a window such as checkout latency, queue depth, timeout rate, cache hit ratio, or pod restart count. search_logs returns matching log lines for a service and a query string. get_feature_flag returns the current state and rollout metadata for a named feature flag. get_recent_deployments returns deployment ids, timestamps, and a short change summary. Mutation tools are unavailable. Do not propose kubectl delete, scale changes, pod restarts, rollbacks, or configuration edits unless a human approver explicitly authorizes them after investigation.",
    },
    {
        "id": "safety-policy",
        "kind": "policy",
        "required": True,
        "stable": True,
        "priority": 92,
        "relevance": 1.0,
        "text": "This assistant supports responders during synthetic incidents on the OpenTelemetry Demo e-commerce system. Ground every claim in supplied metrics, traces, deployment notes, feature-flag status, or logs. Prefer evidence that explains both timing and mechanism. If two services are involved, identify the first service that should be investigated rather than listing every theoretically related dependency. Never cite evidence that is absent from the packet. Never guess at customer impact, never invent mitigation steps, and never recommend high-risk actions such as disabling authentication, deleting production data, restarting every pod, or rolling back immediately without an approval checkpoint. Safe next actions include verifying a dependency endpoint, checking a feature flag, narrowing a metric to a specific service, or reviewing a deployment summary. If the packet suggests an operational change, requires_approval must be true even when the action seems obvious.",
    },
    {
        "id": "incident-runbook",
        "kind": "runbook",
        "required": True,
        "stable": True,
        "priority": 90,
        "relevance": 0.98,
        "text": "Synthetic on-call runbook for the OpenTelemetry Demo workload. Step one: identify the user-visible symptom and the first service whose behavior explains that symptom. Step two: confirm timing by correlating metrics, logs, and any deployment or feature-flag change. Step three: select the most specific failure label that matches the evidence rather than a vague summary. The supported failure labels in this dataset are payment_service_unreachable, payment_service_failure, product_catalog_failure, recommendation_service_cache_failure, cart_service_failure, kafka_queue_problems, and failed_readiness_probe. Use payment_service_unreachable when timeout, connection, or DNS evidence points to paymentservice being unreachable even if checkoutservice shows the symptom. Use payment_service_failure when paymentservice is reachable but returning errors from its own business logic. Use product_catalog_failure when requests to productcatalogservice fail or product data cannot be loaded. Use recommendation_service_cache_failure when recommendationservice is available but latency or errors are driven by a cache issue rather than a total service outage. Use cart_service_failure when add-to-cart or cart retrieval problems originate in cartservice. Use kafka_queue_problems when queue depth, consumer lag, or broker errors explain delayed downstream processing. Use failed_readiness_probe when rollout health, readiness checks, or pods not becoming ready explain the incident. The next action should be the safest read-only move that would reduce uncertainty for a responder. If a deployment is implicated, the first next action is to inspect deployment details or feature-flag state unless the packet already proves a specific infrastructure fault. If a cache is implicated, prefer checking cache hit ratio or cache error logs before recommending a restart. If a service becomes unreachable, prefer verifying service connectivity or dependency status before escalation.",
    },
    {
        "id": "public-system-background",
        "kind": "reference",
        "required": True,
        "stable": True,
        "priority": 85,
        "relevance": 0.9,
        "text": "The OpenTelemetry Demo system is a public, synthetic microservice application used for observability demonstrations. checkoutservice handles checkout requests and depends on paymentservice. frontendproxy renders the storefront and depends on productcatalogservice, recommendationservice, cartservice, and checkoutservice. recommendationservice uses a cache layer whose hit ratio and error rate can change independently from storefront traffic. background queue processing can back up when Kafka brokers or consumers are unhealthy. During rolling deploys, readiness probe failures can leave a service partially unavailable even when the image builds correctly. Treat this system as synthetic training data: no private production details are present, and the task is to reason about safe incident-response context, not to perform real changes.",
    },
]


CASE_VARIANTS = [
    {
        "suffix": "a",
        "request_lead": "Investigate the latest incident and identify the first service to inspect.",
        "metric_phrase": "The symptom appeared suddenly during the most recent window.",
        "cause_phrase": "The error pattern lines up with the primary suspected cause.",
        "distractor_phrase": "A prior unrelated incident should not change the diagnosis.",
    },
    {
        "suffix": "b",
        "request_lead": "A responder needs a grounded diagnosis with the safest next step.",
        "metric_phrase": "The main service metrics changed sharply with stable background traffic.",
        "cause_phrase": "Logs and traces both point at the same failure mechanism.",
        "distractor_phrase": "Historical noise exists but does not match the timing here.",
    },
    {
        "suffix": "c",
        "request_lead": "Summarize the incident packet and choose the best failure label.",
        "metric_phrase": "The onset correlates tightly with a single suspicious shift.",
        "cause_phrase": "The packet contains direct evidence of the likely source of failure.",
        "distractor_phrase": "An unrelated subsystem changed recently without matching symptoms.",
    },
    {
        "suffix": "d",
        "request_lead": "Provide a safe, evidence-cited diagnosis for the on-call handoff.",
        "metric_phrase": "Operational metrics show a clear regression in the affected path.",
        "cause_phrase": "Supporting evidence narrows the likely mechanism rather than the symptom alone.",
        "distractor_phrase": "A separate earlier event is present only as a distractor.",
    },
    {
        "suffix": "e",
        "request_lead": "Use the packet to choose the most precise grounded explanation.",
        "metric_phrase": "The primary telemetry change isolates the affected behavior cleanly.",
        "cause_phrase": "Correlated logs, traces, or rollout data reinforce the same conclusion.",
        "distractor_phrase": "Additional context is intentionally noisy and should not be cited.",
    },
]


BASE_CASES = [
    {
        "id_prefix": "case1",
        "case_slug": "checkout-payment-connectivity",
        "failure_type": "payment_service_unreachable",
        "description": "Checkout latency and payment timeout incident.",
        "request_tail": "Name the first service to inspect, choose the best failure label, cite the strongest packet evidence, and recommend the safest next action.",
        "affected_service": "checkoutservice",
        "suspected_failure": "payment_service_unreachable",
        "recommended_next_action_any_of": [
            "verify paymentservice connectivity",
            "inspect paymentservice endpoint health",
        ],
        "expected_tool": "query_metrics",
        "expected_tool_argument_terms": ["paymentservice"],
        "components": [
            {
                "name": "user-request",
                "kind": "request",
                "priority": 100,
                "relevance": 1.0,
                "text": "Checkout requests started timing out during the last twenty minutes.",
            },
            {
                "name": "metric-checkout",
                "kind": "retrieval",
                "priority": 96,
                "relevance": 0.99,
                "text": "Metric snapshot: checkoutservice p95 latency rose from 240 ms to 1.9 s at 14:04 UTC. Timeout rate climbed from 0.3% to 7.4% over the same window. paymentservice client timeout count from checkoutservice increased from 1 per minute to 58 per minute.",
            },
            {
                "name": "trace-payment",
                "kind": "retrieval",
                "priority": 95,
                "relevance": 0.98,
                "text": "Trace excerpt: checkoutservice span batch shows repeated upstream connect timeout errors when calling paymentservice: Connection timed out after 3s; no paymentservice business error payload returned.",
            },
            {
                "name": "distractor-catalog",
                "kind": "memory",
                "priority": 15,
                "relevance": 0.12,
                "text": "Earlier this week a productcatalogservice image resize job raised CPU usage on the storefront search page. That event resolved after lowering batch concurrency.",
            },
        ],
        "required_evidence_component_names": ["metric-checkout", "trace-payment"],
    },
    {
        "id_prefix": "case2",
        "case_slug": "storefront-product-catalog-failure",
        "failure_type": "product_catalog_failure",
        "description": "Storefront product grid fails because product catalog requests error.",
        "request_tail": "Determine the most likely root service, select the best failure label, cite evidence ids, and recommend a safe next read-only step.",
        "affected_service": "productcatalogservice",
        "suspected_failure": "product_catalog_failure",
        "recommended_next_action_any_of": [
            "inspect productcatalogservice error metrics",
            "review productcatalogservice dependency errors",
        ],
        "expected_tool": "search_logs",
        "expected_tool_argument_terms": ["productcatalogservice"],
        "components": [
            {
                "name": "user-request",
                "kind": "request",
                "priority": 100,
                "relevance": 1.0,
                "text": "The storefront is serving an empty product grid.",
            },
            {
                "name": "frontend-metric",
                "kind": "retrieval",
                "priority": 96,
                "relevance": 0.99,
                "text": "Metric snapshot: frontendproxy error rate for /api/products rose from 0.2% to 18.6% at 09:11 UTC. Downstream call errors to productcatalogservice account for 96% of the failing requests.",
            },
            {
                "name": "catalog-log",
                "kind": "retrieval",
                "priority": 95,
                "relevance": 0.98,
                "text": "Log excerpt from productcatalogservice: failed to load products: upstream storage request returned 500; request aborted before response body was built.",
            },
            {
                "name": "distractor-recommendation",
                "kind": "memory",
                "priority": 18,
                "relevance": 0.15,
                "text": "A month ago, recommendationservice returned stale items after a cache warmup issue. That incident did not affect the main product grid.",
            },
        ],
        "required_evidence_component_names": ["frontend-metric", "catalog-log"],
    },
    {
        "id_prefix": "case3",
        "case_slug": "recommendation-cache-regression",
        "failure_type": "recommendation_service_cache_failure",
        "description": "Recommendation latency spike traced to cache behavior.",
        "request_tail": "Identify the service to inspect first, choose the best failure label, cite evidence ids, and recommend the safest next action.",
        "affected_service": "recommendationservice",
        "suspected_failure": "recommendation_service_cache_failure",
        "recommended_next_action_any_of": [
            "check the recommendation-cache-fallback feature flag",
            "verify cache behavior for recommendationservice",
        ],
        "expected_tool": "get_feature_flag",
        "expected_tool_argument_terms": ["recommendation-cache-fallback"],
        "components": [
            {
                "name": "user-request",
                "kind": "request",
                "priority": 100,
                "relevance": 1.0,
                "text": "Recommendation widgets became slow and intermittently empty after a config change.",
            },
            {
                "name": "cache-metric",
                "kind": "retrieval",
                "priority": 96,
                "relevance": 0.99,
                "text": "Metric snapshot: recommendationservice cache hit ratio dropped from 0.93 to 0.07 at 17:20 UTC while request volume stayed flat. Latency rose from 85 ms to 1.2 s.",
            },
            {
                "name": "flag-state",
                "kind": "retrieval",
                "priority": 95,
                "relevance": 0.98,
                "text": "Feature flag state: recommendation-cache-fallback was disabled at 17:18 UTC during a configuration rollout.",
            },
            {
                "name": "distractor-checkout",
                "kind": "memory",
                "priority": 16,
                "relevance": 0.14,
                "text": "checkoutservice recently introduced a new synthetic payment retry metric. No checkout latency regression is present in the packet.",
            },
        ],
        "required_evidence_component_names": ["cache-metric", "flag-state"],
    },
    {
        "id_prefix": "case4",
        "case_slug": "cart-service-errors",
        "failure_type": "cart_service_failure",
        "description": "Cart retrieval and add-to-cart failures isolated to cartservice.",
        "request_tail": "Identify the most likely failing service, choose the best failure label, cite evidence ids, and recommend the safest next read-only step.",
        "affected_service": "cartservice",
        "suspected_failure": "cart_service_failure",
        "recommended_next_action_any_of": [
            "inspect cartservice error logs",
            "query cartservice failure metrics",
        ],
        "expected_tool": "search_logs",
        "expected_tool_argument_terms": ["cartservice"],
        "components": [
            {
                "name": "user-request",
                "kind": "request",
                "priority": 100,
                "relevance": 1.0,
                "text": "Users cannot add items to cart or load an existing cart.",
            },
            {
                "name": "frontend-cart-metric",
                "kind": "retrieval",
                "priority": 96,
                "relevance": 0.99,
                "text": "Metric snapshot: frontendproxy /api/cart error rate jumped to 22% at 12:44 UTC. Downstream cartservice 5xx responses account for nearly all failed cart requests.",
            },
            {
                "name": "cart-log",
                "kind": "retrieval",
                "priority": 95,
                "relevance": 0.98,
                "text": "cartservice log excerpt: unable to read session cart record; backend storage returned 500 and the request was aborted before cart state could be built.",
            },
            {
                "name": "distractor-kafka",
                "kind": "memory",
                "priority": 12,
                "relevance": 0.10,
                "text": "Kafka consumer lag was elevated during a marketing-event replay yesterday, but cart requests were healthy and the lag has returned to baseline.",
            },
        ],
        "required_evidence_component_names": ["frontend-cart-metric", "cart-log"],
    },
    {
        "id_prefix": "case5",
        "case_slug": "queue-backlog-kafka",
        "failure_type": "kafka_queue_problems",
        "description": "Delayed async processing explained by queue backlog.",
        "request_tail": "Identify the most likely failure label, cite evidence ids, and recommend a safe next investigation step.",
        "affected_service": "event-processing",
        "suspected_failure": "kafka_queue_problems",
        "recommended_next_action_any_of": [
            "inspect Kafka broker health",
            "query queue lag metrics for the event-processing worker",
        ],
        "expected_tool": "query_metrics",
        "expected_tool_argument_terms": ["event-processing"],
        "components": [
            {
                "name": "user-request",
                "kind": "request",
                "priority": 100,
                "relevance": 1.0,
                "text": "Order-status updates are delayed even though the storefront remains mostly responsive.",
            },
            {
                "name": "queue-metric",
                "kind": "retrieval",
                "priority": 96,
                "relevance": 0.99,
                "text": "Metric snapshot: Kafka consumer lag for order event processing rose from 120 to 18,400 messages at 15:06 UTC. Broker request timeouts also increased sharply.",
            },
            {
                "name": "broker-log",
                "kind": "retrieval",
                "priority": 95,
                "relevance": 0.98,
                "text": "Log excerpt from the event-processing worker: failed to commit consumer offset because broker request timed out; retry loop continued while lag accumulated.",
            },
            {
                "name": "distractor-product",
                "kind": "memory",
                "priority": 10,
                "relevance": 0.08,
                "text": "productcatalogservice deployed a readme update this morning with no application code or config changes.",
            },
        ],
        "required_evidence_component_names": ["queue-metric", "broker-log"],
    },
    {
        "id_prefix": "case6",
        "case_slug": "checkout-readiness-rollout",
        "failure_type": "failed_readiness_probe",
        "description": "Checkout rollout leaves pods unready after deploy.",
        "request_tail": "Identify the first service to inspect, choose the best failure label, cite evidence ids, and recommend the safest next read-only action.",
        "affected_service": "checkoutservice",
        "suspected_failure": "failed_readiness_probe",
        "recommended_next_action_any_of": [
            "inspect checkoutservice readiness probe configuration",
            "review checkoutservice rollout health",
        ],
        "expected_tool": "get_recent_deployments",
        "expected_tool_argument_terms": ["checkoutservice"],
        "components": [
            {
                "name": "user-request",
                "kind": "request",
                "priority": 100,
                "relevance": 1.0,
                "text": "A new checkout rollout completed, but service availability stayed degraded.",
            },
            {
                "name": "deploy-summary",
                "kind": "retrieval",
                "priority": 96,
                "relevance": 0.99,
                "text": "Deployment summary: checkoutservice build synthetic-421 rolled out at 08:52 UTC. Only 4 of 10 new pods reached Ready within the expected window.",
            },
            {
                "name": "readiness-log",
                "kind": "retrieval",
                "priority": 95,
                "relevance": 0.98,
                "text": "Readiness probe log: /ready returned 503 for six new checkoutservice pods during rollout; traffic remained concentrated on the older replica set.",
            },
            {
                "name": "distractor-recommendation",
                "kind": "memory",
                "priority": 14,
                "relevance": 0.11,
                "text": "recommendationservice cache hit ratio dipped briefly earlier in the day but recovered before the checkout deploy started.",
            },
        ],
        "required_evidence_component_names": ["deploy-summary", "readiness-log"],
    },
]


def build_case(base_case: dict[str, object], variant: dict[str, str], case_number: int) -> dict[str, object]:
    suffix = variant["suffix"]
    case_id = f"{base_case['case_slug']}-{suffix}"
    id_prefix = f"case{case_number}"
    components = []
    required_ids = []
    for component in base_case["components"]:  # type: ignore[index]
        component_name = component["name"]  # type: ignore[index]
        component_id = f"{id_prefix}-{component_name}"
        text = component["text"]  # type: ignore[index]
        if component["kind"] == "request":  # type: ignore[index]
            text = f"{variant['request_lead']} {text} {base_case['request_tail']}"  # type: ignore[index]
        elif component["kind"] == "retrieval":  # type: ignore[index]
            extra = variant["metric_phrase"] if "metric" in component_name else variant["cause_phrase"]
            text = f"{text} {extra}"
        else:
            text = f"{text} {variant['distractor_phrase']}"
        payload = {
            "id": component_id,
            "kind": component["kind"],
            "required": component["kind"] == "request",
            "stable": False,
            "priority": component["priority"],
            "relevance": component["relevance"],
            "text": text,
        }
        components.append(payload)
        if component_name in base_case["required_evidence_component_names"]:  # type: ignore[index]
            required_ids.append(component_id)

    return {
        "case_id": case_id,
        "failure_type": base_case["failure_type"],
        "description": f"{base_case['description']} Variant {suffix}.",
        "components": components,
        "expectation": {
            "affected_service": base_case["affected_service"],
            "suspected_failure": base_case["suspected_failure"],
            "required_evidence_component_ids": required_ids,
            "recommended_next_action_any_of": base_case["recommended_next_action_any_of"],
            "requires_approval": True,
            "expected_tool": base_case["expected_tool"],
            "expected_tool_argument_terms": base_case["expected_tool_argument_terms"],
        },
    }


def build_fixture() -> dict[str, object]:
    cases = []
    case_number = 1
    for base_case in BASE_CASES:
        for variant in CASE_VARIANTS:
            cases.append(build_case(base_case, variant, case_number))
            case_number += 1
    return {
        "dataset_id": "synthetic-incident-provider-validation",
        "description": "Synthetic incident-response dataset for measuring provider-backed token, latency, and answer-quality tradeoffs with AgenVantage.",
        "budget": 1130,
        "environment_scope": "synthetic_local",
        "minimum_cacheable_prefix_tokens": 1024,
        "recommended_warm_requests_per_policy": 30,
        "minimum_distinct_cases_for_broad_claim": 30,
        "minimum_failure_types_for_broad_claim": 6,
        "shared_components": SHARED_COMPONENTS,
        "cases": cases,
    }


def main() -> None:
    fixture = build_fixture()
    OUTPUT_PATH.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(fixture['cases'])} cases to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
