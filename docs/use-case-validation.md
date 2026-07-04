# Use-Case Validation And Grounding Sufficiency

Prepared: June 30, 2026

## What AgenVantage Actually Does Today

AgenVantage currently solves a narrow but real pre-inference problem:

- scan local repositories, including tracked files and untracked non-ignored
  worktree files;
- build and reuse a persistent local metadata index with file-level symbols and
  imports;
- split eligible files into line-addressable chunks;
- rank chunks with deterministic task-term matching, chunk-local anchor-symbol
  boosts, file-level metadata boosts, import-neighbor expansion, and light
  diversity penalties across files and repositories;
- optionally include git diff and recent commit-log provenance inside the same
  token budget for changed-behavior tasks;
- assemble a token-budgeted Markdown context package and JSON manifest; and
- report how much candidate repository context was omitted locally.

It does not yet prove downstream model quality, API cache hits, provider cost
reduction, latency reduction, or end-task answer quality.

## How To Verify The Grounded Content Is Still Sufficient

For this project there are now two validation bars, not one:

1. `expected_path_recall == 1.0`
2. `repo_recall == 1.0`
3. `grounding_sufficient_for_context == true`
4. `required_observation_recall == 1.0`
5. `answer_rubric_sufficient_for_context == true`

The first three checks answer "did the pack include the right repositories and
files?" The last two answer the stricter question: "did the selected excerpts
actually include the behavioral evidence needed to answer the task?"

The supporting metrics explain how robust that sufficiency is:

- `first_expected_path_rank` shows how early grounding appears.
- `grounding_file_density` shows how concentrated the selected files are around
  the real answer.
- `minimum_budget_for_grounding` shows the smallest budget that still keeps the
  required grounding.
- `grounding_budget_headroom_tokens` shows how much slack the tested budget had.
- `minimum_top_k_for_grounding_at_budget` shows whether the ranking policy only
  works because the candidate pool is large.
- `required_observation_recall` shows whether the selected excerpts contain the
  required facts for the task, not just the right file names.
- `observation_citations` shows which selected files actually grounded those
  required observations.

For changed-behavior tasks, file recall alone is not enough. Those tasks also
need git diff or commit-history evidence, so sufficiency now depends on both
grounding-file recall and provenance-section availability.

## Current Measured Results

Source of truth:

- benchmark runner: [`/Users/nicky/GithubRepos/token-optimization/benchmarks/use_case_validation.py`](/Users/nicky/GithubRepos/token-optimization/benchmarks/use_case_validation.py)
- generated snapshot: `artifacts/use-case-validation.json`

Validation run on June 30, 2026 after adding answer-rubric scoring and
chunk-local anchor-symbol boosts:

- cases run: `7`
- verdicts: `6 pass`, `1 partial`, `0 fail`
- average candidate-context reduction: `94.28%`
- weighted candidate-context reduction: `96.23%`
- average expected-file recall: `1.0`
- weighted expected-file recall: `1.0`
- grounding sufficiency pass rate: `1.0`
- average required-observation recall: `0.95`
- weighted required-observation recall: `0.95`
- answer-rubric pass rate: `0.86`
- synthetic experiment stable-prefix gain: `+55` tokens
- synthetic experiment budgeted reduction: `388 -> 348` tokens (`10.31%`)

### Case Table

| Case | Verdict | Candidate -> Selected | Reduction | File Recall | Obs Recall | Min Budget |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `locate-mesh-extraction-flow` | `pass` | `40,747 -> 2,986` | `92.67%` | `1.0` | `1.0` | `2,986` |
| `explain-signalfoundry-upload-smoke-test` | `partial` | `39,063 -> 3,548` | `90.92%` | `1.0` | `0.67` | `1,273` |
| `validate-application-tracker-private-storage` | `pass` | `120,484 -> 3,114` | `97.42%` | `1.0` | `1.0` | `1,432` |
| `recommend-signalfoundry-edge-tests` | `pass` | `39,069 -> 3,189` | `91.84%` | `1.0` | `1.0` | `1,791` |
| `compare-cross-repo-server-hardening` | `pass` | `201,600 -> 4,731` | `97.65%` | `1.0` | `1.0` | `3,903` |
| `compare-cross-repo-input-flows` | `pass` | `201,587 -> 4,954` | `97.54%` | `1.0` | `1.0` | `4,954` |
| `changed-behavior-signalfoundry-upload-limit` | `pass` | `39,374 -> 3,193` | `91.89%` | `1.0` | `1.0` | `-` |

## What The Results Mean

The stronger benchmark still changed the story in an important way:

- retrieval-level grounding still looks excellent;
- answer-ready evidence is now stronger than the first rubric run, but still
  not identical to file recall.

The latest run still shows that AgenVantage can reduce large local candidate
repository context by roughly `90%` to `98%` while preserving all expected
files across the seven hand-authored tasks. After adding chunk-local anchor
symbols, the answer-rubric pass rate improved from `0.71` to `0.86` and the
remaining cross-repo input-flow failure became a pass.

That gap is useful evidence, not a regression in measurement:

- the SignalFoundry upload smoke-test case selects the right files but misses
  the upload-limit configuration evidence inside the chosen excerpts;
- the remaining weakness is narrower than before: excerpt selection is now
  strong enough for six of the seven rubric-scored cases, but one upload
  explanation case still misses a specific configuration excerpt.

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
The stronger answer-rubric benchmark makes that boundary easier to explain:
the system can often find the right files and now usually finds the right
excerpt within those files, but it still misses some task-specific evidence.

## How To Reproduce

```bash
/tmp/agenvantage-venv/bin/python benchmarks/use_case_validation.py \
  --output-json artifacts/use-case-validation.json \
  --output-md artifacts/use-case-validation.md

/tmp/agenvantage-venv/bin/python -m pytest \
  tests/test_repo_context.py \
  tests/test_use_case_validation.py
```
