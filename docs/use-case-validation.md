# Use-Case Validation And Grounding Sufficiency

Prepared: June 30, 2026

## What AgenVantage Actually Does Today

AgenVantage currently solves a narrow but real pre-inference problem:

- scan local repositories, including tracked files and untracked non-ignored
  worktree files;
- build and reuse a persistent local metadata index with file-level symbols and
  imports;
- split eligible files into line-addressable chunks;
- rank chunks with deterministic task-term matching, file-level metadata boosts,
  import-neighbor expansion, and light diversity penalties across files and
  repositories;
- optionally include git diff and recent commit-log provenance inside the same
  token budget for changed-behavior tasks;
- assemble a token-budgeted Markdown context package and JSON manifest; and
- report how much candidate repository context was omitted locally.

It does not yet prove downstream model quality, API cache hits, provider cost
reduction, latency reduction, or end-task answer quality.

## How To Verify The Grounded Content Is Still Sufficient

For this project, a grounded context package is sufficient only when all of the
following are true:

1. `expected_path_recall == 1.0`
2. `repo_recall == 1.0`
3. `grounding_sufficient_for_context == true`

The supporting metrics explain how robust that sufficiency is:

- `first_expected_path_rank` shows how early grounding appears.
- `grounding_file_density` shows how concentrated the selected files are around
  the real answer.
- `minimum_budget_for_grounding` shows the smallest budget that still keeps the
  required grounding.
- `grounding_budget_headroom_tokens` shows how much slack the tested budget had.
- `minimum_top_k_for_grounding_at_budget` shows whether the ranking policy only
  works because the candidate pool is large.

For changed-behavior tasks, file recall alone is not enough. Those tasks also
need git diff or commit-history evidence, so sufficiency now depends on both
grounding-file recall and provenance-section availability.

## Current Measured Results

Source of truth:

- benchmark runner: [`/Users/nicky/GithubRepos/token-optimization/benchmarks/use_case_validation.py`](/Users/nicky/GithubRepos/token-optimization/benchmarks/use_case_validation.py)
- generated snapshot: `artifacts/use-case-validation.json`

Validation run on June 30, 2026 after provenance and import-neighbor expansion:

- cases run: `7`
- verdicts: `7 pass`, `0 partial`, `0 fail`
- average candidate-context reduction: `94.27%`
- weighted candidate-context reduction: `96.21%`
- average expected-file recall: `1.0`
- weighted expected-file recall: `1.0`
- grounding sufficiency pass rate: `1.0`
- synthetic experiment stable-prefix gain: `+55` tokens
- synthetic experiment budgeted reduction: `388 -> 348` tokens (`10.31%`)

### Case Table

| Case | Verdict | Candidate -> Selected | Reduction | File Recall | Min Budget | Min Top-K |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `locate-mesh-extraction-flow` | `pass` | `40,747 -> 3,090` | `92.42%` | `1.0` | `1,864` | `20` |
| `explain-signalfoundry-upload-smoke-test` | `pass` | `39,063 -> 3,597` | `90.79%` | `1.0` | `1,292` | `20` |
| `validate-application-tracker-private-storage` | `pass` | `120,484 -> 3,116` | `97.41%` | `1.0` | `1,432` | `20` |
| `recommend-signalfoundry-edge-tests` | `pass` | `39,069 -> 3,103` | `92.06%` | `1.0` | `1,684` | `20` |
| `compare-cross-repo-server-hardening` | `pass` | `201,600 -> 4,921` | `97.56%` | `1.0` | `2,917` | `20` |
| `compare-cross-repo-input-flows` | `pass` | `201,587 -> 4,865` | `97.59%` | `1.0` | `4,865` | `20` |
| `changed-behavior-signalfoundry-upload-limit` | `pass` | `39,374 -> 3,138` | `92.03%` | `1.0` | `-` | `-` |

## What The Results Mean

The project now has hard evidence that it can reduce large local candidate
repository context by roughly `90%` to `98%` while still grounding most of the
tested explanation and comparison tasks.

The current implementation now includes both a persistent metadata index and
optional git provenance. After adding import-neighbor expansion, the latest
June 30, 2026 validation run reached `7 pass / 0 partial`, improved the
remaining cross-repo input-flow case to a pass, and kept changed-behavior as a
pass when provenance was enabled.

That evidence does not justify broader claims yet:

- The project does not currently prove end-to-end answer quality retention.
- The project does not currently prove real API-token savings or latency gains.
- The current benchmark suite is still small and hand-authored, so broader
  retrieval robustness is not yet proven outside these validated cases.

## Practical Verdict

Yes, AgenVantage solves the narrower problem of local repository-context
packaging for many real coding tasks, including a changed-behavior use case
when git provenance is enabled.

No, it does not yet solve the full "agent context overload" problem end-to-end.
The benchmark results support that more precise claim.

## How To Reproduce

```bash
/tmp/agenvantage-venv/bin/python benchmarks/use_case_validation.py \
  --output-json artifacts/use-case-validation.json \
  --output-md artifacts/use-case-validation.md

/tmp/agenvantage-venv/bin/python -m pytest tests/test_repo_context.py
```
