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
- `--normalize <payload.json>`: convert raw request records or OTLP-style span
  exports into a claim-auditable provider-validation artifact.
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
