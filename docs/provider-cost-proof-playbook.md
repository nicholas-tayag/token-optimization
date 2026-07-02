# Provider Cost Proof Playbook

Prepared: July 2, 2026

## Objective

Promote AgenVantage from:

> "My context packer made prompts smaller."

to:

> "When I gave the agent a smaller, cache-aligned context, the provider
> actually billed less per request."

This playbook defines the minimum artifact set and run sequence needed for the
`proved_real_api_cost_savings` claim in
[`benchmarks/claim_status.py`](../benchmarks/claim_status.py) to turn `True`.

## Telemetry Already In The Repo

AgenVantage already supports the following telemetry paths:

- Local OpenTelemetry spans for context assembly through `agenvantage demo`
  and `agenvantage run --trace-console`.
- Live provider-validation request spans through
  `agenvantage validate-provider --trace-console`.
- OTLP-style export from a saved provider-validation report through
  `agenvantage validate-provider --otel-export`.
- OTLP-style normalization of spans, logs, and per-request metrics through
  `agenvantage validate-provider --normalize`.
- Request-level cost estimation from a versioned pricing snapshot.
- Optional reconciliation against an OpenAI Costs API export through
  `--reconcile-costs`.

What the repo does **not** include yet is a checked-in real provider artifact.

## Minimum Artifact Set

To prove billed savings, collect and save all of the following under
`artifacts/`:

- `openai-pricing.json`
  Why: pins the exact rates used when estimating request-level cost.
- `provider-validation.json`
  Why: stores raw request records plus the summarized claim audit.
- `provider-validation-otel.json`
  Why: preserves the same run in portable OTLP-style telemetry.
- `openai-costs.json`
  Why: provides organization-recorded spend for reconciliation.

Optional but useful:

- exported logs or metric points from your observability stack if you want to
  prove the normalization path from external telemetry rather than from the
  saved report itself.

## Required Experiment Shape

Use the checked provider-validation fixture in
[`examples/provider_validation_cases.json`](../examples/provider_validation_cases.json)
or a larger compatible dataset.

The current strong-cost claim is most credible when:

- the same model is used across every compared policy;
- every case is run under the same inference settings;
- the compared policies include `full_unaligned` and
  `budgeted_cache_aligned`;
- the cache-aligned policies reuse the same stable prefix and
  `prompt_cache_key`;
- the run includes enough paired cases to keep the paired cost delta
  confidence interval below zero; and
- the OpenAI Costs API window covers the same run so the estimated total can be
  reconciled.

## Recommended Run Sequence

### 1. Prepare Pricing

```bash
cp examples/openai_pricing_template.json artifacts/openai-pricing.json
$EDITOR artifacts/openai-pricing.json
```

Record the exact model and pricing capture date used for the run.

### 2. Run The Live Validation

```bash
export OPENAI_API_KEY=...

.venv/bin/python -m agenvantage validate-provider \
  --pricing artifacts/openai-pricing.json \
  --records artifacts/provider-validation.json \
  --otel-export artifacts/provider-validation-otel.json \
  --trace-console \
  --summary
```

This does four important things at once:

- calls the provider;
- saves raw request records with token usage and deterministic grades;
- emits live OpenTelemetry request spans to the console; and
- writes an OTLP-style export that can be replayed later.

### 3. Export Organization Costs

Export the OpenAI Costs API over the same time window as the validation run and
save it as:

```text
artifacts/openai-costs.json
```

This repo now includes a helper script for that step:

```bash
export OPENAI_ADMIN_KEY=...

.venv/bin/python scripts/export_openai_costs.py \
  --start-time <unix-start> \
  --end-time <unix-end> \
  --output artifacts/openai-costs.json
```

What matters is that the saved export covers the experiment window and
includes the line item for the relevant model usage.

### 4. Reconcile Request Totals Against Provider Costs

```bash
.venv/bin/python -m agenvantage validate-provider \
  --replay artifacts/provider-validation.json \
  --pricing artifacts/openai-pricing.json \
  --reconcile-costs artifacts/openai-costs.json \
  --summary
```

If you started from exported OTLP telemetry instead of a saved report, use:

```bash
.venv/bin/python -m agenvantage validate-provider \
  --normalize artifacts/provider-validation-otel.json \
  --pricing artifacts/openai-pricing.json \
  --reconcile-costs artifacts/openai-costs.json \
  --records artifacts/provider-validation.json \
  --summary
```

### 5. Recompute Claim Status

```bash
.venv/bin/python benchmarks/claim_status.py
```

The claim is ready only when the resulting report says
`proved_real_api_cost_savings = True`.

## What The Claim Audit Actually Checks

The current implementation in
[`src/agenvantage/provider_validation.py`](../src/agenvantage/provider_validation.py)
requires:

- lower mean request cost than `full_unaligned`;
- a paired cost delta confidence interval below zero; and
- when reconciliation data is present, organization-recorded total cost within
  tolerance of the request-level estimate.

This means that smaller prompts alone are not enough. The saved report must
show that billed usage moved in the right direction.

## Strongest Promotion Path

Use the following progression for public claims:

1. Local-only:
   "AgenVantage reduces candidate repository context while preserving grounding
   on benchmarked coding tasks."
2. Provider-cost proof:
   "AgenVantage reduced provider-billed request cost versus a full-context
   baseline on a paired evaluation run."
3. Full end-to-end proof:
   add production-scoped latency and broad quality-retention evidence only
   after those artifacts also turn `True`.

## Anti-Patterns

Do not promote the cost claim based only on:

- local token counts;
- cache-eligible prefix length;
- a synthetic dry run;
- one or two hand-picked successful requests; or
- unreconciled request-level estimates when provider cost exports are available.

## Deliverable Checklist

Before updating a README, resume, or interview narrative, confirm that all of
these exist:

- `artifacts/openai-pricing.json`
- `artifacts/provider-validation.json`
- `artifacts/provider-validation-otel.json`
- `artifacts/openai-costs.json`
- `docs/claim-status.md` showing `proved_real_api_cost_savings = True`
