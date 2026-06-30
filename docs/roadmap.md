# AgenVantage Roadmap

## Project Narrative

AgenVantage begins as a developer tool for my own AI-assisted coding work:

1. Ask a coding question about one of my local public repositories.
2. Select useful files and chunks under a measured token budget.
3. Inspect the package before sending it to a model.
4. Compare API responses and provider-reported token usage only after the
   local planner is useful on real tasks.
5. Add broader governance or observability integrations only when experiments
   justify them.

## Milestone 0: Local Repository Context Packaging

Status: implemented.

- Scan eligible tracked source and documentation files in a selected local
  repository while excluding dependency folders and `.env` content.
- Split source files into line-addressable chunks and rank them against a
  coding question.
- Assemble a Markdown package and JSON decision manifest under a token budget.
- Record local candidate-context reduction, without describing it as billed
  API savings.
- Preserve the original synthetic policy comparison as a controlled secondary
  fixture.

Exit evidence:

- tests cover repository selection and policy behavior;
- real runs package relevant context from a public personal repository.

## Prioritized Backlog From June 30, 2026 Validation

These are the next implementation tasks for future runs. They are ordered by
measured impact, not by generic feature parity.

### Backlog 1: Git Provenance In Pack Output

Status: partially implemented.

Why this is first:

- changed-behavior tasks require repository-change provenance, not just current
  file content;
- the missing capability is repository-change provenance, not more file text.

Tasks:

- add optional `--include-diff` support to `agenvantage pack`;
- add optional `--include-log` support to `agenvantage pack`;
- store diff and log sections separately from repository file chunks in the
  manifest;
- track provenance token counts independently from repository-context token
  counts;
- add changed-behavior benchmark cases that only pass when provenance is
  present;
- document that changed-behavior tasks are unsupported unless provenance is
  packaged.

Current state:

- `agenvantage pack` now supports `--include-diff` and `--include-log`;
- provenance sections are counted inside the pack budget and recorded
  separately in the manifest;
- the changed-behavior benchmark now passes when provenance is enabled;
- provenance-aware path expansion, recency signals, and blame-level evidence
  are still future work.

References:

- [`/Users/nicky/GithubRepos/token-optimization/docs/context-tooling-landscape.md`](/Users/nicky/GithubRepos/token-optimization/docs/context-tooling-landscape.md)
- [`/Users/nicky/GithubRepos/token-optimization/docs/use-case-validation.md`](/Users/nicky/GithubRepos/token-optimization/docs/use-case-validation.md)

### Backlog 2: Symbol-Aware Retrieval

Status: partially implemented.

Why this is second:

- one cross-repo hardening benchmark still misses
  `application-tracker/server.mjs`;
- the present ranker is still mostly term-based and path-based.

Tasks:

- extract top-level exported symbols, route handlers, test names, and likely
  entrypoint identifiers from eligible files;
- expand query matching with identifier-aware signals instead of plain task
  terms alone;
- bias ranking toward files that define matched symbols and files referenced by
  matched tests;
- preserve deterministic ranking as the baseline for comparison;
- add benchmark cases that require symbol-aware retrieval to ground correctly.

Additional research-backed facets:

- build a persistent symbol table rather than deriving symbols ad hoc on each
  run;
- index import links and test-to-code relationships so retrieval can expand
  from anchor files to implementation neighbors;
- keep explicit evidence-type diversity so the pack is not dominated by only
  tests or only implementation files.

Current state:

- file-level symbol and import metadata is now cached persistently outside the
  worktree and used as a ranking boost;
- chunk-local anchor symbols now prioritize nearby function/route-style
  definitions so later chunks inside large files can inherit the right local
  intent;
- local import-target resolution now allows imported-helper expansion during
  selection, which was enough to make the current cross-repo input-flow
  benchmark pass;
- a deeper structural graph, reverse-reference expansion, test-to-
  implementation expansion, and symbol-to-line anchoring layer is still future
  work.

Measured reason this still matters:

- the chunk-local anchor change removed the previous cross-repo input-flow
  failure, but one upload explanation case still misses a specific
  configuration excerpt.

### Backlog 3: Task-Shaped Selection Controls

Why this is third:

- the core packer works, but task-specific controls are still weak;
- benchmarking is easier when the pack command can constrain eligible context.

Tasks:

- add include-glob and exclude-glob filters to `agenvantage pack`;
- add task presets such as `explain`, `review`, `compare`, and `change`;
- allow preselected path lists from stdin or a file;
- optionally emit line-number-heavy and XML-style context outputs;
- record active filters and preset choice in the manifest.

Additional research-backed facets:

- add task presets that alter retrieval recipes, not just output formatting;
- support iterative retrieval for compound tasks such as `compare` and
  `change`;
- add explicit stopping checks once required grounding files and repos are
  present.
- add per-task excerpt rules such as "for compare, require at least one cited
  evidence span per repository."

Current measured gap:

- the remaining partial case shows that `explain`-style tasks can still miss a
  needed configuration excerpt even when the right file is selected, so task
  presets should still control what evidence types must be present.

### Backlog 4: Incremental Indexing And Search Contexts

Why this is fourth:

- repeated scans will become wasteful as repository sets grow;
- multi-repo tasks will benefit from named reusable groups.

Tasks:

- cache file hashes and chunk metadata locally;
- skip unchanged files between runs when rebuilding chunk candidates;
- add named search-context or repo-group configuration for recurring tasks;
- expose freshness metadata in the manifest;
- benchmark scan-time improvements separately from retrieval-quality changes.

Additional research-backed facets:

- store chunk hashes, symbol metadata, and optional embeddings by content hash;
- add a local lexical index for cheap path, regex, and symbol search before any
  embedding lookup;
- keep repository-index build cost separate from prompt-token cost in reports.

### Backlog 5: Optional Hybrid Retrieval

Why this is fifth:

- semantic retrieval may help, but it is not the clearest fix for the current
  measured failures;
- provenance and symbol retrieval should land first.

Tasks:

- keep deterministic term ranking as the default baseline;
- add optional BM25-style retrieval and compare it against the benchmark suite;
- add optional embedding-based reranking behind an explicit feature flag;
- only promote a hybrid retriever after measured benchmark improvement.

Additional research-backed facets:

- prefer hybrid retrieval over semantic-only retrieval;
- consider late-interaction or reranking designs before replacing lexical
  retrieval;
- keep ablation experiments that compare lexical-only, semantic-only, and
  hybrid retrieval.

### Backlog 6: Richer Reporting And Safety Metrics

Why this is sixth:

- better reporting improves diagnosis and trust;
- it does not by itself fix the current retrieval limitations.

Tasks:

- report per-file and per-repo token share in the manifest;
- report why high-ranked chunks lost budget slots;
- add token-tree style summaries for selected versus omitted context;
- add sensitive-content screening for packed files and future provenance
  sections;
- graph covered versus uncovered task concepts in the report output.

Additional research-backed facets:

- add leave-one-file-out ablation to estimate whether each selected file was
  actually necessary;
- report retrieval sufficiency separately from answer sufficiency;
- reserve stronger "task solved" claims for future answer-level evaluation
  milestones.

Current state:

- retrieval sufficiency and answer-rubric sufficiency are now reported
  separately in the use-case benchmark;
- the latest measured result is `6 pass / 1 partial / 0 fail` with
  `0.86` answer-rubric pass rate;
- generated-answer and unsupported-claim scoring is still future work.

## Milestone 1: Provider-Reported Usage

- Add an OpenAI Responses API adapter for selected repository-context tasks.
- Record actual input, output, and cached input token metadata where exposed.
- Expand stable instructions/context to satisfy caching eligibility and run
  repeated-prefix experiments against fixed coding tasks.
- Capture request latency and calculate cost only from published/provider
  metadata recorded alongside each experiment.

Exit evidence:

- reproducible experiment command and raw output artifacts;
- measured cache-hit and latency comparison, or a documented negative result.

## Milestone 2: Coding Task Evaluations

Status: partially implemented.

- Add a human-authored set of tasks drawn from completed public repository
  behavior: locating an implementation, explaining a known test case,
  identifying changed behavior, and recommending edge-case tests.
- Evaluate expected file citations, required behavioral observations, and
  unsupported-claim avoidance.
- Gate input-cost reduction against a declared answer-correctness tolerance.

Current state:

- the repository now has seven real local validation cases;
- expected-file recall, repository recall, and minimum-budget grounding are
  implemented;
- required-observation scoring against selected excerpts is implemented;
- chunk-local anchor-symbol scoring improved the measured answer-rubric pass
  rate from `0.71` to `0.86`;
- unsupported-claim scoring for generated answers is still not implemented.

Success rule:

```text
token_or_cost_reduction > 0
AND unsupported_claim_pass_rate >= baseline_unsupported_claim_pass_rate
AND correctness_pass_rate >= baseline_correctness_pass_rate - declared_tolerance
```

## Milestone 3: Observable Workflow And Later Extensions

- Emit documented OpenTelemetry GenAI spans for real inference operations.
- Add MCP/tool selection or sensitive-context policy rules only when the local
  coding workflow provides a genuine need for them.
- Export telemetry to a local collector and optionally Datadog if access and
  cost make sense for a documented demonstration.
- Build a minimal report or dashboard from real experiment telemetry.

## Resume Rule

No resume bullet should claim API token reduction, cost savings, latency
improvement, quality retention, enterprise controls, or Datadog integration
until the corresponding experiment has been run and its artifacts are
committed or reproducible.
