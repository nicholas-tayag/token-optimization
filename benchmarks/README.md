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
