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

It supports three modes:

- `--dry-run`: validate that the synthetic fixture is cache-eligible and that
  the budgeted policies actually create token-selection pressure before any API
  call is made.
- `--replay <report.json>`: summarize previously recorded validation records and
  re-run the claim audit without another provider call.
- live mode with `--pricing` plus `OPENAI_API_KEY`: call the OpenAI Responses
  API, persist raw records, compute request cost from a versioned pricing
  snapshot, and grade structured JSON answers with deterministic checks.

Run the fixture readiness check with:

```bash
.venv/bin/python -m agenvantage validate-provider --dry-run --summary
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
  --summary
```

The claim audit is intentionally conservative. Without enough measured requests,
it should continue reporting that latency or broad quality-retention claims are
not yet supported.

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
