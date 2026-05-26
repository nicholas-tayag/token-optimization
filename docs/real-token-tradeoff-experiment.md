# Proving Real Token Tradeoffs

Prepared: May 25, 2026

## Objective

AgenVantage should support a narrow, defensible claim:

> Changing the context supplied to an agent can reduce measured API input
> cost and latency, while a task-specific evaluation suite checks whether
> grounded and safe task completion regresses.

The first provider-backed experiment should not attempt to prove that a full
enterprise platform exists. It should produce reproducible raw records,
summary statistics, and a clearly scoped result suitable for a portfolio
README and, only after completion, a resume bullet.

## Recommended Provider Sequence

### Phase 1: OpenAI Responses API

Use OpenAI first for the personal-utility version of the project:

- Prompt caching applies automatically to recent models, including GPT-4o
  and newer.
- Cache eligibility begins at `1,024` input tokens.
- Static instructions, examples, images, and tools should precede variable
  request content for exact-prefix matching.
- Response usage metadata exposes cached input tokens.
- Current standard pricing for `gpt-5.4-mini` is `$0.75` per million input
  tokens, `$0.075` per million cached input tokens, and `$4.50` per million
  output tokens.

Recommended model for the first experiment: `gpt-5.4-mini`.

Reason: it is inexpensive enough for repeated runs, while a successful cached
prefix is billed at one tenth of its normal input rate. Do not claim it is the
best model for task quality until it is evaluated.

### Phase 2: Amazon Bedrock With Claude

Add Bedrock only after the OpenAI experiment harness is reproducible:

- Bedrock caching uses explicit cache checkpoints.
- Bedrock responses expose `cacheReadInputTokens` and
  `cacheWriteInputTokens`.
- Bedrock `CountTokens` reports model-specific charged input-token counts for
  matching `Converse` or `InvokeModel` request input.
- Cache thresholds and TTLs are model dependent. AWS currently documents
  `1,024` minimum checkpoint tokens for Claude 3.7 Sonnet and `4,096` for
  Claude Sonnet 4.5, Claude Haiku 4.5, Claude Opus 4.5, and Claude Opus 4.6.

Reason: Bedrock provides an excellent enterprise/AWS extension and connects
to AI platform work, but it should not delay the first measured result.

### Not First: Vertex AI Context Caching

Vertex AI exposes `cachedContentTokenCount`, supports implicit and explicit
context caching, and documents a 90% discount for cached tokens on supported
Gemini 2.5-or-later caching paths. It is a credible later comparison, but the
minimum cache size is larger: `2,048` tokens for Gemini 2.0/2.5 and `4,096`
tokens for Gemini 3/3.1 models.

## Critical Change To Current Fixture

The current synthetic scenario is `388` locally tokenized input tokens. It can
demonstrate context selection, but it cannot demonstrate a real OpenAI prompt
cache hit because OpenAI requires a prompt of at least `1,024` tokens.

Create a provider experiment fixture with:

- `1,200-1,600` stable tokens of instructions, tool schemas, response schema,
  and an incident-response runbook;
- `600-1,200` optional retrieved evidence and distractor context; and
- a short variable incident question placed at the end for cache-aligned runs.

Keep all content synthetic or derived from public system documentation.

## Workload Recommendation

Use a synthetic on-call assistant grounded in the public OpenTelemetry Demo
e-commerce system rather than a generic legal chatbot. The OpenTelemetry Demo
officially documents feature flags that simulate operational failures,
including:

- `paymentServiceFailure`;
- `paymentServiceUnreachable`;
- `cartServiceFailure`;
- `productCatalogFailure`;
- `recommendationServiceCacheFailure`;
- `kafkaQueueProblems`;
- `llmRateLimitError`; and
- `failedReadinessProbe`.

This gives AgenVantage a recognizable public observability workload without
requiring private data or claiming that the demo is an enterprise deployment.

Each test case should contain a synthetic incident packet:

- one stable policy/runbook/tool prefix;
- relevant metrics, trace excerpts, deploy events, or logs;
- unrelated evidence components as distractors;
- an incident question; and
- human-authored expected fields.

Target the first serious dataset at `30` cases across at least `6` failure
types. A tiny smoke-test dataset of `6-10` cases is appropriate while building
the runner, but is not strong resume evidence.

## Four Policy Conditions

Use a small factorial experiment so the source of savings is visible.

| Policy | Context Selection | Prefix Ordering | What It Isolates |
| --- | --- | --- | --- |
| `full_unaligned` | All components | Variable request before reusable evidence | Cold/no-cache-friendly baseline |
| `full_cache_aligned` | All components | Stable reusable content first | Cache benefit without removing evidence |
| `budgeted_unaligned` | Selected components only | Variable request before selected evidence | Token-selection benefit without caching |
| `budgeted_cache_aligned` | Selected components only | Stable reusable content first | Combined best candidate |

The existing `full`, `cache_aligned`, and `budgeted` policies are a good local
start, but provider experiments should name the two variables explicitly.

## Run Protocol

### Cache Experiment

For each cache-aligned context version:

1. Construct a stable prefix exceeding the provider's cache minimum.
2. Issue one seed request and capture its raw usage data.
3. Wait for that response before issuing warm requests; a cache entry is not
   available until the initial response has begun or completed.
4. Issue warm requests with the identical stable prefix and a varying incident
   question at the end.
5. Capture total input tokens, cached input tokens, output tokens, model,
   status, and wall-clock latency for every request.

For OpenAI, reuse a consistent `prompt_cache_key` for requests that share a
prefix. Keep a prefix/key stream below the documented approximate `15`
requests-per-minute overflow threshold or pace requests conservatively.

Use at least `10` completed warm requests per context version for exploratory
results. For README-quality latency summaries, use at least `30` warm
requests per compared policy and report this sample size.

### Context Selection Experiment

For each incident case:

1. Run every policy with the same model and inference settings.
2. Randomize policy execution order per case so time-dependent effects do not
   always favor one policy.
3. Store raw request, normalized context manifest, raw response, usage
   metadata, and grader results.
4. Repeat each policy/case combination at least `3` times when measuring
   quality, because generated outputs can vary.

Cache and selection should be reported separately:

- cache alignment can reduce charged input cost while leaving semantic
  content unchanged;
- budgeting removes context and therefore needs quality and safety checks.

## Metrics To Capture

### Provider Usage

For every model request, store:

- `experiment_id`, `case_id`, `policy_id`, `context_version`;
- provider and exact model name;
- timestamp and request latency in milliseconds;
- total input tokens;
- cached input tokens;
- cache-write input tokens when the provider exposes them;
- output tokens;
- request outcome/error; and
- raw provider usage response for auditing.

For OpenTelemetry GenAI spans, the current specification defines:

- `gen_ai.usage.input_tokens`;
- `gen_ai.usage.output_tokens`;
- `gen_ai.usage.cache_read.input_tokens`;
- `gen_ai.usage.cache_creation.input_tokens`; and
- operation duration metrics such as `gen_ai.client.operation.duration`.

The OpenTelemetry GenAI conventions are still marked `Development`, so record
the convention version used by the implementation. Datadog currently
documents ingestion for OpenTelemetry GenAI `1.37+` traces and token usage.

### Cost Formula

For an OpenAI request:

```text
uncached_input_tokens = total_input_tokens - cached_input_tokens

request_cost_usd =
    (uncached_input_tokens * input_price_per_token)
  + (cached_input_tokens * cached_input_price_per_token)
  + (output_tokens * output_price_per_token)
```

Store the pricing snapshot and source date with an experiment run. Prices can
change, so a report must state which published rates it used.

For Bedrock, follow AWS's total-input rule:

```text
total_input_tokens =
    inputTokens + cacheReadInputTokens + cacheWriteInputTokens
```

Then apply that model's published standard-input, cache-read, cache-write,
and output rates from the pricing snapshot used for the run.

### Performance Summary

Report:

- cache-hit rate for eligible warm calls;
- cached-token coverage: `cached_input_tokens / total_input_tokens`;
- total and input-only cost per completed case;
- input cost reduction versus `full_unaligned`;
- p50 and p95 request latency;
- p50 and p95 time to first chunk only if streaming is implemented; and
- token/cost reduction stratified by policy and incident type.

Do not use a single successful cached call as evidence of a latency claim.

## Quality And Safety Evaluation

Require model output in a structured JSON shape:

```json
{
  "affected_service": "checkout",
  "suspected_failure": "payment_service_unreachable",
  "evidence_component_ids": ["trace-2", "metric-1"],
  "recommended_next_action": "inspect payment endpoint configuration",
  "requires_approval": true,
  "tool_calls": [
    {"tool": "query_metrics", "arguments": {"service": "checkout"}}
  ]
}
```

Use deterministic graders first:

| Check | Scoring Method |
| --- | --- |
| Correct affected service | Exact match |
| Correct failure type | Exact match from allowed labels |
| Evidence citation recall | Expected cited IDs present |
| Hallucinated evidence | Cited IDs absent from supplied context |
| Unsafe production change | Rule-based forbidden-action check |
| Approval requirement | Exact Boolean check on high-risk cases |
| Tool selection | Tool name and required argument match |

An optional LLM judge can assess explanation completeness or groundedness, but
it should be blinded to the policy name and calibrated against a manually
reviewed sample. OpenAI's evaluation guidance explicitly recommends
task-specific evals, automation where possible, human calibration, and
comparisons rather than open-ended impressions.

## Acceptance Rule For A Portfolio Claim

Declare the tolerance before running the final experiment:

```text
mean_request_cost_reduction > 0
AND safety_pass_rate >= full_unaligned_safety_pass_rate
AND correctness_pass_rate >= full_unaligned_correctness_pass_rate - 0.02
AND grounded_citation_pass_rate >= full_unaligned_grounded_citation_pass_rate - 0.02
```

Also report sample sizes and confidence intervals or paired bootstrap
intervals. If the quality threshold fails, that is still a valuable result:
the README should identify which context was removed and what behavior
regressed.

## Minimal Build Sequence

1. Fix or defer the current uncommitted dashboard until its report metrics and
   packaging behavior are correct.
2. Expand the fixture schema into a dataset format with expected answer fields.
3. Add the four explicit policy conditions.
4. Implement an OpenAI provider runner that persists raw responses and
   normalized usage records.
5. Build deterministic graders and a CSV/JSON summary command.
6. Run a small smoke experiment on `6-10` cases.
7. Refine cases and run the final `30`-case experiment with repeats.
8. Add OpenTelemetry GenAI inference spans and optionally export them to
   Datadog after local experiment results are trustworthy.

## Resume-Ready Evidence, After It Exists

An eventual bullet should be generated from recorded experiment output, for
example:

```text
Built AgenVantage, an OpenTelemetry-instrumented context optimization harness
for synthetic incident-response agents; reduced measured API input cost by X%
across N evaluated cases while maintaining Y% grounded-safety pass rate.
```

Do not fill in `X`, `N`, or `Y` until they are generated by reproducible
experiment artifacts.

## Official Sources

- OpenAI, Prompt caching:
  <https://developers.openai.com/api/docs/guides/prompt-caching>
- OpenAI, API pricing:
  <https://developers.openai.com/api/docs/pricing>
- OpenAI, Evaluation best practices:
  <https://developers.openai.com/api/docs/guides/evaluation-best-practices>
- OpenAI, Working with evals:
  <https://developers.openai.com/api/docs/guides/evals>
- Anthropic, Prompt caching:
  <https://platform.claude.com/docs/en/build-with-claude/prompt-caching>
- Anthropic, Token counting:
  <https://platform.claude.com/docs/en/build-with-claude/token-counting>
- AWS, Amazon Bedrock prompt caching:
  <https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html>
- AWS, Amazon Bedrock `CountTokens`:
  <https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_CountTokens.html>
- Google Cloud, Vertex AI context caching:
  <https://cloud.google.com/vertex-ai/generative-ai/docs/context-cache/context-cache-overview>
- OpenTelemetry, GenAI semantic conventions:
  <https://opentelemetry.io/docs/specs/semconv/gen-ai/>
- OpenTelemetry, GenAI client spans:
  <https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/>
- OpenTelemetry, GenAI metrics:
  <https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-metrics/>
- OpenTelemetry, Demo feature-flag scenarios:
  <https://opentelemetry.io/docs/demo/scenarios/>
- Datadog, LLM Observability:
  <https://docs.datadoghq.com/llm_observability/>
- Datadog, Cost monitoring:
  <https://docs.datadoghq.com/llm_observability/monitoring/cost/>
- Datadog, OpenTelemetry instrumentation:
  <https://docs.datadoghq.com/llm_observability/instrumentation/otel_instrumentation/>
