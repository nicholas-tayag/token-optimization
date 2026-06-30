## Use-Case Validation

`use_case_validation.py` checks whether AgenVantage's selected repository
context is still sufficient for the task after token reduction.

It measures more than "did the right file appear":

- `expected_path_recall`: whether all expected grounding files were selected.
- `repo_recall`: whether all expected repositories were represented.
- `grounding_sufficient_for_context`: strict pass signal for explanation and
  comparison tasks that do not require git-history provenance.
- `first_expected_path_rank`: how early a grounding file appears.
- `grounding_file_density`: how much of the selected file set is actually
  grounding-relevant.
- `minimum_budget_for_grounding`: smallest token budget that still preserves
  the required grounding at the current ranking policy.
- `minimum_top_k_for_grounding_at_budget`: smallest candidate-pool size that
  still grounds the task at the tested budget.

Changed-behavior tasks are intentionally marked partial even with full file
recall, because the current `pack` flow does not include git diff or commit
history evidence.

Run it with:

```bash
/tmp/agenvantage-venv/bin/python benchmarks/use_case_validation.py \
  --output-json artifacts/use-case-validation.json \
  --output-md artifacts/use-case-validation.md
```

The generated `artifacts/` files are ignored from git. Commit durable findings
to `docs/` instead of relying on ignored benchmark output.
