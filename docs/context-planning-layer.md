# AgenVantage Context Planning Layer

Prepared: May 25, 2026

## Core Idea

Add a pre-inference layer between an agent application and the model API:

```text
user task + candidate context + available tools
                    |
                    v
          AgenVantage Context Planner
                    |
                    v
     measured context package + manifest + trace
                    |
                    v
                   LLM
```

The layer does not need to generate an answer. It determines what the model
is allowed and required to see, what is worth including under a budget, and
how the selected material should be ordered for caching and traceability.

This is stronger than a post-hoc token dashboard because it actively changes
the input while recording why each decision was made.

## What The Layer Optimizes

The wrong goal is:

```text
minimize tokens at any cost
```

The useful goal is:

```text
minimize measured request cost and irrelevant context
subject to required policy, grounding, and safety checks passing
```

A shorter prompt that omits the only useful diagnostic signal is not an
optimization. A longer stable prefix that produces measured cache reads may
cost less than a shorter uncached prompt. AgenVantage should make that
tradeoff visible.

## Pipeline Stages

### 1. Candidate Inventory

Represent every possible input as a context component:

- system and safety instructions;
- role or access policy;
- response schema;
- tool or MCP definitions;
- runbooks;
- retrieved documents, logs, metrics, and trace excerpts;
- conversation memory; and
- the current user request.

Each component receives metadata such as:

```json
{
  "id": "runbook-checkout-latency",
  "kind": "runbook",
  "required": false,
  "stable": true,
  "sensitivity": "public",
  "priority": 80,
  "estimated_tokens": 612,
  "source": "otel-demo-public-docs"
}
```

### 2. Hard Inclusion And Exclusion Rules

Before relevance scoring, enforce deterministic constraints:

- always include system safety instructions and output schema;
- always include the current user task;
- exclude context not allowed for the user role;
- redact secrets or synthetic sensitive markers;
- block write-capable tools for read-only incident investigations; and
- reserve enough output tokens for the expected JSON response.

For a portfolio project, these rules should be simple configuration and unit
tests, not a broad security product claim.

### 3. Tool Context Filtering

Tool definitions can consume substantial input context. Do not send every
tool schema on every request.

Implement two local comparison policies:

- `all_tools`: include every synthetic tool schema;
- `selected_tools`: include only tools tagged for the task category, such as
  incident read-only queries.

Later, provider-native experiments can compare this local planner against
documented tool-search features:

- OpenAI `tool_search` defers tools until needed, is designed to preserve the
  prompt cache, and supports deferred functions, namespaces, and MCP servers
  on supported GPT-5.4-or-later models.
- Anthropic tool search loads needed tool definitions on demand instead of
  loading the full catalog up front. Anthropic documents that tool definition
  context in a representative multi-server setup can reach approximately
  `55k` tokens and that tool search typically reduces that context by more
  than `85%`.

Those provider figures are motivation, not AgenVantage results. The project
must measure its own test catalog.

### 4. Evidence Retrieval And Ranking

Select only evidence that can help answer the request:

1. Use deterministic keyword/BM25 retrieval first.
2. Optionally add embedding similarity later.
3. Rank evidence by relevance, source trust, recency within the synthetic
   incident, and token cost.
4. Exclude low-value distractors when the budget is exhausted.

Do not begin with an LLM-based selector. A selector call introduces new token
cost, latency, nondeterminism, and evaluation requirements before the core
experiment is working.

### 5. Budget Allocation

Allocate a fixed input budget by category:

```text
required instructions and schema       always included
tool definitions                        selected under tool budget
retrieved evidence                      selected under evidence budget
conversation or incident memory         summarized or omitted under memory budget
user task                               always included
```

Record each excluded component with a machine-readable reason:

```json
{
  "id": "old-unrelated-incident",
  "decision": "excluded",
  "reason": "below relevance cutoff after evidence budget filled",
  "estimated_tokens_saved": 143
}
```

### 6. Cache-Aware Serialization

Once content is selected, order it intentionally:

```text
stable policy + stable schema + stable runbook + stable tool definitions
dynamic retrieved evidence + memory excerpt + current user request
```

For OpenAI caching experiments, the shared exact prefix must reach at least
`1,024` tokens. For Bedrock or Anthropic experiments, respect the chosen
model's documented cache threshold.

This stage should compare:

- a naive ordering that interleaves dynamic content before reusable sections;
- a cache-aligned ordering with the same semantic content; and
- a budgeted cache-aligned ordering that also removes low-value context.

### 7. Decision Manifest And Observability

Before making the model call, persist a manifest:

```json
{
  "policy": "budgeted_cache_aligned",
  "included_components": ["policy", "tools-readonly", "metric-1", "request"],
  "excluded_components": [{"id": "noise-2", "reason": "token_budget"}],
  "locally_estimated_input_tokens": 1432,
  "stable_prefix_tokens": 1087
}
```

After the call, join it with provider-reported usage:

```json
{
  "provider_input_tokens": 1435,
  "provider_cached_input_tokens": 1056,
  "provider_output_tokens": 128,
  "latency_ms": 711
}
```

Emit an assembly span for the planning decision and a GenAI inference span for
the actual model request. Do not label local token estimates as provider usage.

## When An Additional LLM Call Makes Sense

The pre-inference layer should initially avoid LLM calls. Later, an additional
small-model call may be justified for:

- summarizing a long memory once and reusing the summary across many future
  turns;
- compressing tool results after they have already informed the workflow; or
- extracting structured evidence from a document reused across many tasks.

Measure the break-even point:

```text
extra_planner_cost <
    cost_saved_across_future_requests
AND quality_and_safety_do_not_regress
```

Do not use an LLM just to select context for a single small request; that is
likely to increase cost and complicate the claim.

## Implementation Steps In This Repository

### Next Code Milestone

Extend the current component and policy system with:

1. `sensitivity`, `source`, and `task_tags` fields on context components.
2. A `ContextPlan` manifest recording inclusion decisions and reasons.
3. `all_tools` versus `selected_tools` policy behavior.
4. Explicit `full_unaligned`, `full_cache_aligned`,
   `budgeted_unaligned`, and `budgeted_cache_aligned` policies.
5. Tests proving required/safe content is never removed and policy manifests
   explain exclusions.

This work does not require an API key and should occur before provider calls.

### Following Milestone

Add an OpenAI Responses API adapter that:

- accepts the planned context package;
- records raw response usage, cached tokens, model, and request latency;
- computes cost from a versioned pricing snapshot;
- emits OpenTelemetry inference spans; and
- writes reproducible experiment artifacts.

### Later Milestone

Compare the local tool-selection layer against provider-native deferred tool
loading on a large synthetic tool catalog. This demonstrates why context
engineering matters beyond trimming documents.

## Official Source Basis

- OpenAI, Tool search:
  <https://developers.openai.com/api/docs/guides/tools-tool-search>
- OpenAI, Prompt caching:
  <https://developers.openai.com/api/docs/guides/prompt-caching>
- Anthropic, Tool search tool:
  <https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool>
- Anthropic, Context editing:
  <https://platform.claude.com/docs/en/build-with-claude/context-editing>
- Anthropic, Prompt caching:
  <https://platform.claude.com/docs/en/build-with-claude/prompt-caching>
- OpenTelemetry, GenAI semantic conventions:
  <https://opentelemetry.io/docs/specs/semconv/gen-ai/>
