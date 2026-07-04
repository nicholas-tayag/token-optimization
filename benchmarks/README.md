## Use-Case Validation

`use_case_validation.py` checks whether AgenVantage's selected repository
context is still sufficient for the task after token reduction.

It measures more than "did the right file appear":

- `expected_path_recall`: whether all expected grounding files were selected.
- `repo_recall`: whether all expected repositories were represented.
- `required_observation_recall`: whether the selected excerpts contain the
  task's required behavioral observations, not just the right file paths.
- `grounding_sufficient_for_context`: strict pass signal for explanation and
  comparison tasks when the required files and repositories are present.
- `answer_rubric_sufficient_for_context`: stronger pass signal that requires
  both grounding sufficiency and full required-observation coverage.
- `first_expected_path_rank`: how early a grounding file appears.
- `grounding_file_density`: how much of the selected file set is actually
  grounding-relevant.
- `minimum_budget_for_grounding`: smallest token budget that still preserves
  the required grounding at the current ranking policy.
- `minimum_top_k_for_grounding_at_budget`: smallest candidate-pool size that
  still grounds the task at the tested budget.

Changed-behavior tasks now require provenance support. They pass only when the
selected package includes both the relevant files and git diff/log evidence.

Run it with:

```bash
/tmp/agenvantage-venv/bin/python benchmarks/use_case_validation.py \
  --output-json artifacts/use-case-validation.json \
  --output-md artifacts/use-case-validation.md
```

The generated `artifacts/` files are ignored from git. Commit durable findings
to `docs/` instead of relying on ignored benchmark output.

## Feature-Work Validation

`feature_work_validation.py` checks the Phase 1 feature-work promise: given a
local repo and a natural-language feature request, `agenvantage pack --preset
feature` should expose enough context to begin implementation on the first
prompt.

The benchmark fixture contains `12` manually annotated tasks across
`token-optimization`, `mesh`, `signalfoundry`, and `application-tracker`. It
separates retrieval sufficiency from answer-plan sufficiency and reports:

- `edit_target_recall`: whether the feature change surface names the expected
  implementation files.
- `test_target_recall`: whether the feature change surface names expected test
  files.
- `required_observation_recall`: whether selected excerpts expose required
  behavior, config, or test observations.
- `answer_plan_pass_rate`: deterministic pass signal that the pack includes
  edit targets, test signals, and enough required observations for an agent to
  start work.
- `median_token_reduction_percent`: selected context reduction versus scanned
  eligible repository context.
- `median_full_scan_prompt_tokens`: median tokens in a rendered prompt that
  includes the user task plus every scanned eligible source chunk.
- `median_packed_prompt_tokens`: median tokens in the actual packed prompt that
  would be handed to a coding agent.
- `median_prompt_tokens_saved_vs_full_scan`: median token savings for the
  prompt a user would actually send after AgenVantage packs context.
- `missing_signal_warning_rate`: how often the planner explicitly reports
  insufficient signals instead of inventing certainty.

Run it with:

```bash
.venv/bin/python benchmarks/feature_work_validation.py \
  --output-json artifacts/feature-work-validation.json \
  --output-md artifacts/feature-work-validation.md \
  --summary
```

Current local result from July 2, 2026:

- edit-target recall: `1.0`
- test-target recall: `0.8333`
- required-observation recall: `1.0`
- answer-plan pass rate: `0.8333`
- median token reduction: `90.93%`
- median full-scan prompt: `80,921.5` tokens
- median packed prompt: `5,892.5` tokens
- median prompt tokens saved: `74,985.0`
- total prompt tokens saved across 12 cases: `1,251,666`
- acceptance pass: `true`

## Provider Validation

`agenvantage validate-provider` is the claim-audit path for the stronger
questions that the repository could not previously answer:

- real provider cost reduction;
- latency improvement;
- downstream answer-quality retention; and
- end-to-end support for the broader "context overload" claim.

It supports five modes:

- `--dry-run`: validate that the synthetic fixture is cache-eligible and that
  the budgeted policies actually create token-selection pressure before any API
  call is made.
- `--normalize <payload.json>`: convert raw request records or OTLP-style
  spans, logs, or per-request metrics into a claim-auditable
  provider-validation artifact.
- `--replay <report.json>`: summarize previously recorded validation records and
  re-run the claim audit without another provider call.
- live mode with `--pricing` plus `OPENAI_API_KEY`: call the OpenAI Responses
  API, persist raw records, compute request cost from a versioned pricing
  snapshot, and grade structured JSON answers with deterministic checks.
- `--otel-export <payload.json>`: emit the summarized provider-validation report
  as an OTLP-style span export so the same evidence can be replayed through the
  normalization path or shared with observability tooling.

Run the fixture readiness check with:

```bash
.venv/bin/python -m agenvantage validate-provider --dry-run --summary
```

The checked synthetic fixture now contains `30` distinct cases across `6`
failure types, which means the broad-workload sample-width precondition is
available locally before any live provider run. Regenerate it with:

```bash
.venv/bin/python scripts/generate_provider_validation_cases.py
```

Run a live experiment only after creating a real pricing snapshot and exporting
an API key:

```bash
cp examples/openai_pricing_template.json artifacts/openai-pricing.json
$EDITOR artifacts/openai-pricing.json
export OPENAI_API_KEY=...
.venv/bin/python -m agenvantage validate-provider \
  --pricing artifacts/openai-pricing.json \
  --records artifacts/provider-validation.json \
  --otel-export artifacts/provider-validation-otel.json \
  --trace-console \
  --summary
```

If the live run happened elsewhere and you only have saved usage artifacts,
normalize them into the same provider-validation shape:

```bash
.venv/bin/python -m agenvantage validate-provider \
  --normalize artifacts/provider-otel-export.json \
  --pricing artifacts/openai-pricing.json \
  --environment-scope production \
  --reconcile-costs artifacts/openai-costs.json \
  --records artifacts/provider-validation.json \
  --summary
```

Use `--environment-scope production` only when the exported telemetry actually
came from a production-scoped deployment. The claim audit stays conservative:
the artifact still needs enough requests, lower measured cost, and no material
quality regression before the stronger claims can turn `True`.

The claim audit is intentionally conservative. Without enough measured requests,
it should continue reporting that latency or broad quality-retention claims are
not yet supported. The provider summary now also prints an evidence-readiness
section so you can see whether the artifact is blocked by sample count,
environment scope, missing grader fields, or incomplete case pairing. It also
prints paired case deltas with bootstrap confidence intervals for cost,
latency, and quality metrics so the result can be defended case-by-case rather
than only through aggregate means. Those paired intervals now back the stronger
cost, latency, and broad-quality support gates when a provider artifact is
present. If you also export the OpenAI Costs API, `--reconcile-costs` compares
the request-level estimated experiment total against organization-recorded
costs over the supplied window. Saved provider reports now preserve that
reconciliation block on replay, and a bad reconciliation can block the strong
cost-savings support claim.

If you already have a saved provider-validation report and want to verify that
its OTLP export preserves the same claim-audit outcome, write the export and
round-trip it through normalization:

```bash
.venv/bin/python -m agenvantage validate-provider \
  --replay artifacts/provider-validation.json \
  --otel-export artifacts/provider-validation-otel.json \
  --summary

.venv/bin/python -m agenvantage validate-provider \
  --normalize artifacts/provider-validation-otel.json \
  --pricing artifacts/openai-pricing.json \
  --environment-scope production \
  --records artifacts/provider-validation-roundtrip.json \
  --summary
```

For the concrete billed-cost proof sequence, including how to reconcile the
saved report against an OpenAI Costs API export, see
[docs/provider-cost-proof-playbook.md](../docs/provider-cost-proof-playbook.md).

## Modality Tradeoff Validation

`modality_tradeoff_validation.py` validates the local pxpipe-style artifact
pipeline after AgenVantage has already selected the relevant repository
context. It can write PNG context pages, factsheets, and recoverable source
manifests. It still does not send images to a provider, so the token deltas are
estimated rather than provider-billed savings.

The estimator is intentionally conservative:

- exact edit, test, config, supporting, secret-like, hash, and UUID-bearing
  chunks stay text;
- gist-level background chunks are rendered into deterministic dense PNG pages
  only when the provider-profile estimate beats text after factsheet overhead;
- each imaged block gets deterministic factsheet text and a recoverable source
  file keyed by a stable `rec_...` identifier.
- CLI runs default to `--modality-profile auto`; unsupported forced
  model/profile pairs stay text-only.

Run it with:

```bash
.venv/bin/python benchmarks/modality_tradeoff_validation.py \
  --output-json artifacts/modality-tradeoff-validation.json \
  --output-md artifacts/modality-tradeoff-validation.md \
  --summary
```

Recover exact text for an imaged block with:

```bash
.venv/bin/python -m agenvantage rehydrate \
  --manifest artifacts/modality-context-images/<case-id>/manifest.json \
  --verify

.venv/bin/python -m agenvantage rehydrate \
  --manifest artifacts/modality-context-images/<case-id>/manifest.json \
  --list

.venv/bin/python -m agenvantage rehydrate \
  --manifest artifacts/modality-context-images/<case-id>/manifest.json \
  --id rec_...
```

Current local result from July 4, 2026:

- cases: `12`
- full-scan median text prompt: `80,968.5` tokens
- full-scan median theoretical image prompt: `19,044.0` tokens
- full-scan theoretical modality reduction before safety gate: `76.22%`
- full-scan image-candidate rate after safety gate: `0.0`
- packed median text prompt: `5,939.5` tokens
- packed median theoretical image prompt: `4,761.0` tokens
- packed theoretical modality reduction before safety gate: `19.84%`
- packed image-candidate rate after safety gate: `0.1667`
- median retrieval tokens saved before modality: `74,985.0`
- total retrieval tokens saved across 12 cases: `1,251,666`
- total rough-estimator incremental modality tokens saved after packing: `2,501`
- artifact image case rate: `0.25`
- total artifact images written: `3`
- total recoverable blocks: `36`
- total artifact incremental tokens saved after packing: `3,099`
- median end-to-end safe candidate reduction: `91.5%`

Interpretation: retrieval still does nearly all of the work. The implemented
artifact layer adds selective upside on background/gist chunks, but correctly
leaves exact coding evidence in text and makes every imaged block recoverable.

## Claim Status

To collapse the current evidence into one durable, human-readable answer about
what the repository can honestly claim today, run:

```bash
.venv/bin/python benchmarks/claim_status.py
```

That command reads the checked use-case artifact, inspects the provider
fixture, optionally summarizes a saved provider-validation artifact, and writes
[docs/claim-status.md](../docs/claim-status.md).

For the external docs, papers, and repositories that most directly unblock the
currently unsupported claims, see
[docs/proof-resource-pack.md](../docs/proof-resource-pack.md) and the
machine-readable manifest
[examples/proof_resources.json](../examples/proof_resources.json).

## Session Cache Validation

`session_cache_validation.py` checks the repeated feature-work cache layout
before any provider spend. It reuses the feature-work fixture, creates one
cache-aware feature session per case, emits one follow-up task packet, and
reports:

- `cache_eligible_rate`: whether stable prefixes meet the configured cache
  threshold.
- `median_stable_prefix_tokens`: reusable prefix size.
- `median_dynamic_packet_tokens`: per-turn task packet size after the prefix is
  frozen.
- `median_reusable_prefix_percent`: proportion of the follow-up prompt that can
  remain stable.
- `median_estimated_warm_reduction_percent_vs_full_scan`: theoretical warm-call
  input-token reduction versus a full-scan prompt.

Run it with:

```bash
.venv/bin/python benchmarks/session_cache_validation.py \
  --output-json artifacts/session-cache-validation.json \
  --output-md artifacts/session-cache-validation.md \
  --summary
```

Current local result from the production-path implementation:

- cases: `12`
- cache-eligible rate: `1.0`
- median stable prefix: `5,907.5` tokens
- median dynamic packet: `89.5` tokens
- median reusable prefix: `98.47%`
- median estimated warm reduction versus full scan: `99.86%`
- total estimated warm tokens saved versus full scan: `1,236,065`
- acceptance pass: `true`

These are cache-layout readiness metrics. Actual cache hits and billed savings
still require live provider usage records.
