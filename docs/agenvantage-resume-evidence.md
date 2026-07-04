# AgenVantage Resume Evidence

Last updated: 2026-07-02

## Project Scope

AgenVantage is a local coding-agent context planner. It reduces repository
context overload before a model call by scanning local repositories, indexing
structural metadata, selecting feature-relevant context, and emitting a compact
agent handoff payload.

Current implemented scope:

- CLI command: `agenvantage pack`
- Feature preset: `agenvantage pack --preset feature --task "..."`
- Machine-readable handoff: `--handoff-json`
- Deterministic retrieval: lexical/path/symbol/test/config matching, local
  import expansion, reverse-import expansion, and category-aware final packing
- Persistent index metadata: file hashes, symbols, line-addressed symbol
  occurrences, local imports, and reverse local imports
- Feature-work output: likely edit targets, test targets, config targets,
  supporting targets, missing signals, selected chunks, rendered prompt, and
  prompt-token accounting
- Validation harness: 12 manually annotated feature-work tasks across
  `token-optimization`, `mesh`, `signalfoundry`, and `application-tracker`

Out of scope for verified resume claims right now:

- Real provider-billed API cost savings
- Production latency reduction
- Broad downstream answer-quality retention across many workloads
- Embedding/vector search or background indexing daemon

## Verified Metrics

Feature-work benchmark:

- Cases: `12`
- Repositories: `token-optimization`, `mesh`, `signalfoundry`,
  `application-tracker`
- Edit-target recall: `1.0`
- Test-target recall: `0.8333`
- Selected edit-target recall: `1.0`
- Selected test-target recall: `1.0`
- Required-observation recall: `1.0`
- Answer-plan pass rate: `0.8333`
- Mean selected chunk count: `14.08`
- Missing-signal warning rate: `0.5833`
- Median full-scan prompt: `80,648.5` tokens
- Median packed prompt: `5,938.5` tokens
- Median prompt tokens saved: `74,760.0`
- Median prompt reduction: `90.91%`
- Total prompt tokens saved across 12 cases: `1,067,887`
- Acceptance result: `passed`

Feature-provider dry-run with pricing snapshot:

- Cases: `12`
- Median full-scan prompt: `80,968.5` tokens
- Median AgenVantage packed prompt: `5,952.5` tokens
- Median prompt reduction: `90.85%`
- Median estimated full-scan cold input cost: `$0.02024212`
- Median estimated packed warm input cost: `$0.00015538`
- Median estimated warm input savings: `99.23%`
- Total estimated full-scan cold input cost: `$0.30612`
- Total estimated packed warm input cost: `$0.00184323`
- Cost scope: theoretical input-only estimate from a saved pricing snapshot,
  not provider-billed usage

Session cache-readiness benchmark:

- Cases: `12`
- Cache-eligible rate: `1.0`
- Median stable prefix: `5,907.5` tokens
- Median dynamic packet: `89.5` tokens
- Median full-scan prompt: `80,946.5` tokens
- Median reusable prefix: `98.47%`
- Median estimated warm reduction versus full scan: `99.86%`
- Total estimated warm tokens saved versus full scan: `1,236,065`
- Acceptance result: `passed`

Practical Mesh feature validation:

- Feature: add memory-search diagnostics to `POST /api/memory/search`
- User prompt: `39` tokens
- Full-scan prompt: `40,797` tokens
- AgenVantage packed prompt: `5,630` tokens
- Prompt tokens saved: `35,167`
- Prompt reduction: `86.2%`
- Selected chunks: `14`
- Selected expected edit/test files:
  - `server.js`
  - `scripts/server-smoke-test.js`
- Context-assisted agent result: implemented and passed validation
- Plain no-context baseline agent result: implemented and passed validation
- Final Mesh PR: <https://github.com/nicholas-tayag/mesh/pull/1>

Four practical repository feature runs:

| Repository | Full-scan prompt | Packed prompt | Tokens saved | Reduction |
|---|---:|---:|---:|---:|
| `token-optimization` | `178,592` | `5,709` | `172,883` | `96.8%` |
| `mesh` | `40,761` | `5,976` | `34,785` | `85.34%` |
| `signalfoundry` | `39,638` | `4,784` | `34,854` | `87.93%` |
| `application-tracker` | `120,539` | `5,941` | `114,598` | `95.07%` |

Regression/use-case benchmark:

- Cases: `7`
- Verdicts: `4 pass`, `3 partial`
- Average context reduction: `94.39%`
- Median context reduction: `92.67%`
- Weighted context reduction: `96.26%`
- Average expected-path recall: `0.96`
- Grounding sufficiency pass rate: `0.86`
- Average required-observation recall: `0.83`
- Answer-rubric pass rate: `0.57`

Automated test validation:

- `./.venv/bin/pytest`: `91 passed`
- Feature-work validation acceptance: `passed`
- Session cache-readiness validation acceptance: `passed`
- Mesh feature validation:
  - `node --check server.js`: passed
  - `node --check scripts/server-smoke-test.js`: passed
  - `node scripts/server-smoke-test.js`: passed

## Resume-Safe Claims

Strongest single bullet:

```text
Built AgenVantage, a deterministic context-planning CLI for coding agents that indexes local repositories and emits feature-specific handoff JSON; validated across 12 tasks on 4 repos, reducing median grounded prompt size from 80.6K to 5.9K tokens (90.9%) while achieving 100% edit-target recall and 83.3% test-target recall.
```

More implementation-focused bullet:

```text
Developed a feature-work context planner with line-addressed symbol indexing, reverse-import expansion, and category-aware packing for edit files, tests, config, and supporting code; passed a 12-case benchmark with 100% required-observation recall and 83.3% answer-plan pass rate.
```

Practical validation bullet:

```text
Validated AgenVantage on a real Mesh feature by reducing a full-scan coding-agent prompt from 40,797 to 5,630 tokens (86.2% saved) while preserving the edit and test context needed to implement and smoke-test the change.
```

More conservative public-project bullet:

```text
Built and benchmarked a local repository-context optimizer for coding agents, cutting median feature-task prompt size by 90.9% across 12 annotated tasks while maintaining full edit-target recall and full required-observation coverage.
```

Cache-readiness bullet:

```text
Added cache-aware feature sessions that split stable repository context from per-turn task packets; validated 12 local feature sessions with 5.9K-token median stable prefixes, 89.5-token median dynamic packets, and 98.47% reusable-prefix share.
```

## Interview Explanation

Short version:

```text
The project attacks coding-agent context overload. Instead of pasting an entire
repo into a model, AgenVantage scans the repo locally, indexes symbols and
imports, identifies the likely edit and test surface for a feature request, and
emits a compact handoff prompt. I validated it on 12 real feature-work tasks
across four personal repos. The median full-scan prompt was about 80.6K tokens;
the median packed prompt was about 5.9K tokens, a 90.9% reduction, while still
finding all expected edit files and all required behavior observations.
```

If asked whether it proves API cost savings:

```text
Not yet as a billed-provider claim. What I have proven is local prompt-token
reduction with retrieval sufficiency. The next validation step is live provider
telemetry showing that smaller, cache-aligned prompts produce lower billed input
cost without a quality regression.
```

If asked why the Mesh plain-agent baseline also succeeded:

```text
That was an important control. A strong agent can still solve a small feature by
exploring the repo itself. The value AgenVantage proved there was not higher
code quality; it was supplying the needed repo context upfront in 5,630 tokens
instead of a 40,797-token full scan, while still producing a passing
implementation.
```

## Source Artifacts

- Feature benchmark JSON: `artifacts/feature-work-validation.json`
- Feature benchmark Markdown: `artifacts/feature-work-validation.md`
- Feature provider dry-run report:
  `artifacts/feature-provider-validation-dry-run.json`
- Session cache-readiness JSON: `artifacts/session-cache-validation.json`
- Session cache-readiness Markdown: `artifacts/session-cache-validation.md`
- Mesh context-vs-plain comparison:
  `artifacts/mesh-context-vs-plain-feature-validation.md`
- Feature benchmark fixture: `examples/feature_work_validation_cases.json`
- Feature benchmark runner: `benchmarks/feature_work_validation.py`
- Session cache benchmark runner: `benchmarks/session_cache_validation.py`
- Mesh PR: <https://github.com/nicholas-tayag/mesh/pull/1>
- AgenVantage PR: <https://github.com/nicholas-tayag/token-optimization/pull/2>
