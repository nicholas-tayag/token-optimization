# Research Basis

Prepared: May 25, 2026

## Design Question

AgenVantage begins with a personal question: which parts of an agent request
consume input tokens, and which context can be omitted or arranged more
efficiently without removing required instructions?

The portfolio direction is to test that question rigorously before expanding
into provider calls, evaluation, security rules, or dashboards.

## Current Official Findings

### Prompt Caching Rewards Stable Prefixes

OpenAI documents that cache hits require exact prompt-prefix matches and
recommends placing static instructions and examples before variable content.
Its API exposes cached token usage in response usage metadata for supported
requests.

Amazon Bedrock documents optional prompt caching with cache checkpoints and
returns cache read and cache write input-token fields in supported responses.
Bedrock also exposes `CountTokens`, which reports the model-specific number of
tokens a matching inference request would process before inference occurs.

Implication for AgenVantage:

- Measure stable-prefix size separately from total selected context.
- Do not describe stable-prefix measurements as cache hits until a provider
  experiment captures actual usage metadata.
- Add Bedrock `CountTokens` or provider-reported token usage in a later
  provider integration instead of treating all local tokenizers as identical.

### Telemetry Has a Standards Path, With Caution

OpenTelemetry Generative AI semantic conventions currently have development
status. Current conventions cover GenAI model operations, input/output token
usage, cache input-token metrics, tool execution, and Model Context Protocol
operations.

Implication for AgenVantage:

- v0 emits project-owned `agenvantage.context.*` attributes for local context
  assembly rather than incorrectly representing assembly as a model inference.
- A later inference adapter can emit the documented `gen_ai.*` attributes
  using an explicitly recorded semantic-convention version.

### Datadog Relevance Is Measurable, Not Cosmetic

Datadog LLM Observability documents cost analysis from token counts attached
to LLM or embedding spans, breakdowns by model and prompt version, and managed
agent evaluations for tool selection, tool argument correctness, and goal
completeness. Datadog also documents mapping OpenTelemetry GenAI semantic
conventions into LLM Observability.

Implication for AgenVantage:

- The project should eventually compare context policy versions through spans
  and evaluations, rather than adding a dashboard before meaningful data
  exists.
- Useful future metrics include cached and non-cached input tokens, estimated
  cost, latency, tool-selection correctness, and task-completion pass rate.

## MVP Boundary

The initial implementation measures:

- input tokens per assembled context package;
- stable-prefix tokens for future provider caching experiments;
- included and excluded components for each policy; and
- reduction against a full-context baseline.

It deliberately does not claim:

- real cache reads or cost savings;
- latency improvements;
- answer-quality or security pass rates; or
- Datadog deployment.

## Primary Sources

- OpenAI, Prompt caching: <https://platform.openai.com/docs/guides/prompt-caching>
- AWS, Prompt caching for faster model inference:
  <https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html>
- AWS, `CountTokens` API:
  <https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_CountTokens.html>
- OpenTelemetry, GenAI semantic conventions:
  <https://opentelemetry.io/docs/specs/semconv/gen-ai/>
- OpenTelemetry, MCP semantic conventions:
  <https://opentelemetry.io/docs/specs/semconv/gen-ai/mcp/>
- Datadog, LLM Observability:
  <https://docs.datadoghq.com/llm_observability/>
- Datadog, LLM Observability cost monitoring:
  <https://docs.datadoghq.com/llm_observability/monitoring/cost/>
- Datadog, OpenTelemetry instrumentation:
  <https://docs.datadoghq.com/llm_observability/instrumentation/otel_instrumentation/>

Note: the preferred OpenAI developer-documentation MCP tool was unavailable in
this session, so the official OpenAI documentation URL above was used directly.

