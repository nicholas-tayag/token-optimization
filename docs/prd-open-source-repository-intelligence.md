# PRD: AgenVantage Open-Source Repository Intelligence Platform

Status: implementation specification  
Audience: senior engineers, maintainers, contributors, evaluators, and agent-platform integrators  
Target releases: `0.1.0-alpha` through `1.0.0`  
Last updated: July 11, 2026

## 1. Purpose

AgenVantage will be a local-first repository intelligence and context control
plane for coding agents. Given a repository and a software-engineering task, it
must identify the evidence an agent needs, package that evidence under a hard
token budget, expose uncertainty, support retrieval of omitted evidence, and
measure the total cost of completing the task.

The product is successful when an agent using AgenVantage completes real work
with comparable quality and materially lower total context consumption than the
same agent using its normal repository exploration workflow.

The product is not successful merely because it omits a large percentage of an
eligible repository corpus.

This document is the implementation and release contract. The broader product
thesis and research rationale remain in
[`prd-context-performance-platform.md`](prd-context-performance-platform.md).

## 2. Product Contract

### 2.1 User promise

For a supported repository and task, AgenVantage will:

1. inspect repository structure locally;
2. select exact, attributable implementation evidence;
3. stay within the declared context budget;
4. identify missing or ambiguous evidence;
5. let the agent retrieve additional evidence on demand;
6. record why each context item was selected; and
7. report token, latency, cost, and task-outcome measurements without
   presenting estimates as provider-billed facts.

### 2.2 Defensible public description

Until the `1.0` evidence gate passes, the public description is:

> AgenVantage is an experimental local-first context planner for coding agents.
> It builds inspectable, token-budgeted repository context and measures
> retrieval coverage against annotated tasks.

The project must not claim universal context sufficiency, provider-billed cost
savings, production latency improvements, or broad task-quality retention
without the corresponding evidence defined in this PRD.

### 2.3 North-star metric

```text
total input-equivalent tokens per successfully completed task
```

Input-equivalent tokens include normal input, cache-weighted input, retrieval
planner calls, context expansion, tool output returned to the model, retries,
and repair turns. Results must always be paired with task-success rate.

## 3. Current Baseline And Gaps

The current implementation includes deterministic scanning, token-budgeted
packing, line-addressed chunks, lexical and structural heuristics, repository
maps, feature-oriented evidence categories, persistent metadata, handoff JSON,
local traces, and benchmark harnesses.

Known release-blocking gaps include:

- the primary reduction baseline is an eligible full-corpus counterfactual,
  not a normal coding-agent workflow;
- the feature benchmark is small, hand-authored, and based on mutable sibling
  worktrees;
- strict rendered-evidence readiness is currently below its declared gate;
- repository indexing still performs eager whole-repository file work on warm
  runs;
- symbol and dependency extraction relies substantially on language heuristics;
- context assembly is primarily one-shot rather than iterative;
- provider validation does not yet establish completed-patch correctness;
- monorepo, build-target, ownership, and cross-language relationships are not
  first-class; and
- open-source governance, compatibility, threat-model, and release processes
  are incomplete.

These gaps are product requirements, not documentation footnotes.

## 4. Goals

### 4.1 Functional goals

- Support feature implementation, bug localization, code review, and
  repository explanation.
- Work on previously unseen public repositories without repository-specific
  ranking rules.
- Represent packages, files, symbols, references, tests, configuration, build
  targets, ownership, and change history.
- Produce an initial context package and support progressive retrieval.
- Preserve exact source provenance for every selected excerpt.
- Explain selection, exclusion, confidence, and missing evidence.
- Integrate through CLI, Python, JSON, and MCP-compatible interfaces.
- Remain useful without embeddings, a hosted service, or provider credentials.

### 4.2 Performance goals

- Never exceed the effective rendered token budget.
- Re-index only changed files and affected graph edges on warm runs.
- Keep warm planning latency below `500 ms` for the defined medium repository
  class on the reference machine.
- Keep peak memory bounded independently of total source size where practical.
- Reduce total input-equivalent tokens per successful task by at least `30%`
  against the normal-agent baseline before `1.0`.
- Keep task-success regression within `3` percentage points at `1.0`.

### 4.3 Open-source goals

- Install from PyPI and from source using documented, reproducible steps.
- Run locally on macOS, Linux, and Windows.
- Publish stable schemas, compatibility expectations, and migration policy.
- Make benchmark fixtures reproducible from pinned public revisions.
- Provide contribution, security, conduct, governance, and release guidance.
- Avoid requiring contributors to provide paid API credentials for core tests.

## 5. Non-Goals

- Replacing a coding agent or IDE.
- Guaranteeing the globally minimum sufficient context.
- Uploading repository content to an AgenVantage-hosted service by default.
- Automatically executing untrusted repository code during indexing.
- Treating embeddings as authoritative evidence.
- Optimizing token count at the expense of correctness.
- Claiming that full-repository ingestion is the default behavior of other
  coding agents.
- Supporting every programming language in the first stable release.

## 6. Personas And Workflows

### 6.1 Individual developer

The developer clones an unfamiliar repository, runs one command, inspects the
selected context and uncertainty, and passes the handoff to a coding agent.

### 6.2 Coding-agent integrator

The integrator uses the MCP or Python interface to query symbols, retrieve
evidence, expand context, and record task outcomes.

### 6.3 Open-source maintainer

The maintainer configures exclusions and build relationships, reproduces
benchmark results, and reviews context-selection traces without provider keys.

### 6.4 Evaluation engineer

The evaluator checks out pinned historical revisions, runs paired agent
policies, validates patches, and computes confidence intervals for task and
cost outcomes.

## 7. Repository Classes

Performance results must identify the repository class:

| Class | Tracked files | Eligible source | Example shape |
| --- | ---: | ---: | --- |
| Small | `<1,000` | `<100K` LOC | focused package or service |
| Medium | `1,000-20,000` | `100K-2M` LOC | framework or multi-package project |
| Large | `20,000-200,000` | `2M-20M` LOC | major monorepo |
| Very large | `>200,000` | `>20M` LOC | Chromium-scale repository |

The initial stable release targets small and medium repositories. Large
repository support is beta until its scale gates pass. Very large repositories
are explicitly experimental.

## 8. Required Architecture

```text
repository snapshot
        |
        v
safe file discovery -> incremental parser -> structural index
                                             |
task -> task classifier -> candidate retrieval + graph expansion
                                             |
                                  sufficiency controller
                                             |
                                  context-set optimizer
                                      /             \
                              initial handoff    retrieval API
                                      \             /
                                       coding agent
                                             |
                                      outcome telemetry
                                             |
                                    evaluation and tuning
```

The implementation must preserve boundaries between indexing, retrieval,
selection, rendering, integration, and evaluation. A component may not use
benchmark annotations at runtime.

## 9. Component Requirements

### 9.1 Safe repository discovery

Responsibilities:

- discover tracked and explicitly included files;
- honor `.gitignore`, AgenVantage configuration, and hard safety exclusions;
- identify binary, minified, generated, vendored, LFS, submodule, and symlinked
  content;
- avoid following paths outside the repository root;
- detect dirty worktrees and record the exact working-tree state;
- redact secrets before content reaches output artifacts; and
- never execute repository code during discovery or indexing.

Required outputs:

```json
{
  "repository_id": "...",
  "revision": "...",
  "dirty_digest": "...",
  "files": [],
  "exclusions": [],
  "warnings": []
}
```

### 9.2 Incremental structural index

The index must persist:

- file identity, content hash, language, role, size, and revision;
- package, workspace, module, and build-target membership;
- symbol definitions with exact ranges;
- references and imports with resolved and unresolved states;
- reverse imports and references;
- test definitions and test-to-production relationships;
- configuration, schema, route, command, and environment-key definitions;
- ownership and relevant repository instructions;
- git change frequency and co-change signals; and
- parser version and index schema version.

Index update behavior:

1. compare the current repository state to the indexed state;
2. invalidate changed, deleted, and renamed files;
3. parse only invalidated files;
4. update affected reverse edges;
5. preserve unchanged records; and
6. atomically publish the new index revision.

The index must recover from interruption without presenting a partially
updated revision as fresh.

### 9.3 Language adapters

The preferred parsing layer is Tree-sitter or an equivalent concrete syntax
tree implementation. Language-server integration may be added where it is
stable and optional.

Required stable-language sequence:

1. Python
2. TypeScript and JavaScript
3. Go
4. Java
5. Rust
6. C and C++

Each adapter must provide:

- symbol definitions;
- imports and module resolution;
- test identification;
- common entrypoint and framework landmarks;
- parser-error reporting; and
- fixture coverage for malformed and generated syntax.

Unsupported languages must fall back to lexical chunking with an explicit
capability warning.

### 9.4 Repository graph

Required node types:

- repository, workspace, package, module, build target;
- file, symbol, test, route, command;
- configuration key, schema, documentation, and owner.

Required edge types:

- contains, defines, references, imports;
- implements, inherits, calls;
- tests, configures, generates;
- owns and commonly-changes-with.

Every edge must carry provenance and confidence. Heuristic edges must never be
indistinguishable from parser-confirmed edges.

### 9.5 Task and evidence planner

The planner must classify task shape and produce required evidence slots.

Minimum task recipes:

| Task | Required evidence |
| --- | --- |
| Feature | implementation, caller/entrypoint, state or contract, tests, relevant config |
| Bug | failing behavior, execution path, state/contract, regression test, recent provenance |
| Review | changed code, callers, tests, contract/config, ownership/instructions |
| Explain | entrypoint, core implementation, data flow, configuration |

Planner output must include concepts, exact identifiers, paths, required slots,
optional slots, grounding state, and ambiguity.

### 9.6 Candidate retrieval

Retrieval must support independent channels:

- exact path and identifier lookup;
- BM25 or equivalent lexical retrieval;
- symbol definition and reference lookup;
- graph-neighbor expansion;
- test and configuration relationships;
- repository history and co-change retrieval; and
- optional semantic retrieval.

Ranks should be combined with a deterministic method such as reciprocal-rank
fusion before introducing a learned ranker. Each candidate must retain channel
scores and selection reasons.

Embeddings may generate candidates but may not replace exact source evidence.
The core product must function with semantic retrieval disabled.

### 9.7 Sufficiency controller

The controller must decide among:

- `sufficient`: all mandatory evidence slots have attributable selected
  evidence;
- `partial`: some slots are weak or ambiguous and expansion is available;
- `insufficient_budget`: required evidence exists but cannot fit;
- `insufficient_repository`: required evidence is absent or cannot be found;
- `unsupported`: repository or language capability is inadequate.

Sufficiency must be calculated from selected immutable excerpts, not proposed
change-surface paths or reread files. Every covered slot must cite chunk IDs.

### 9.8 Context-set optimizer

The optimizer must:

- reserve space for mandatory evidence classes;
- deduplicate overlapping and semantically redundant excerpts;
- account for rendered delimiters and instructions;
- enforce one exact rendered-token budget;
- favor evidence diversity over repeated high-scoring snippets;
- preserve exact line and revision provenance; and
- return an insufficiency state when mandatory evidence cannot fit.

The invariant is:

```text
rendered_prompt_tokens <= effective_budget
```

This invariant must hold for every successful pack operation.

### 9.9 Progressive retrieval API

Required operations:

- `find_symbol(query, scope)`
- `find_references(symbol_id, scope)`
- `find_tests(target_id)`
- `find_config(query, scope)`
- `expand_dependencies(node_id, direction, depth)`
- `read_chunk(chunk_id)`
- `rehydrate(block_id)`
- `explain_selection(pack_id, chunk_id)`
- `report_missing(pack_id)`

Responses must be bounded, attributable, deterministic for an immutable index,
and safe for direct model consumption.

### 9.10 Context renderer and handoff

Supported outputs:

- human-readable Markdown;
- stable JSON manifest;
- agent handoff JSON; and
- MCP responses.

Every handoff must include repository revision, task, effective budget,
tokenizer, selected excerpts, reasons, evidence-slot coverage, missing signals,
index freshness, and measurement scope.

JSON schemas must be versioned. Breaking schema changes require a major version
or an explicit migration path.

### 9.11 Telemetry and outcomes

The telemetry model must distinguish:

- local estimated tokens;
- provider-reported input, cached-input, reasoning, and output tokens;
- estimated cost from a versioned price table;
- reconciled provider cost;
- retrieval and tool-call tokens;
- indexing and planning latency;
- model latency;
- task success and test outcomes; and
- retries, repairs, and context expansions.

No field may silently mix local estimates and provider observations.

## 10. Public Interfaces

### 10.1 CLI

Required stable commands:

```text
agenvantage index <repo>
agenvantage status <repo>
agenvantage pack <repo> --task <task> --preset <preset>
agenvantage query <repo> <operation> ...
agenvantage serve --mcp
agenvantage benchmark <suite>
agenvantage doctor
```

CLI requirements:

- machine-readable mode for every command;
- documented exit codes;
- no interactive prompt in automation mode;
- actionable errors with remediation;
- stable configuration precedence; and
- no network access unless explicitly enabled.

### 10.2 Python API

The Python API must expose typed interfaces for indexing, planning, retrieval,
rendering, and telemetry. CLI code must consume these interfaces rather than
maintaining a second implementation path.

### 10.3 MCP server

The MCP server must expose bounded repository-intelligence tools rather than a
single unrestricted file-dump tool. It must support workspace allowlists,
response-size limits, cancellation, timeouts, and structured errors.

## 11. Configuration

Configuration precedence:

```text
CLI flags > environment variables > repository config > user config > defaults
```

Repository configuration must be safe to inspect but untrusted. It may define
paths, language adapters, package boundaries, and test relationships. It may
not execute commands during indexing.

Secrets and provider credentials must remain outside checked-in repository
configuration.

## 12. Evaluation Requirements

### 12.1 Evaluation layers

1. Unit correctness: parsing, indexing, ranking, budgeting, rendering.
2. Retrieval quality: selected file and selected-region evidence coverage.
3. Planning quality: mandatory evidence-slot coverage and uncertainty.
4. Agent outcomes: completed patch, acceptance tests, regressions, review.
5. Economics: total tokens, cost, tool calls, retries, and latency per
   successful task.

Passing a lower layer must not be described as passing a higher layer.

### 12.2 Frozen public benchmark

Each case must pin:

- repository URL and immutable commit;
- task source and license-compatible task text;
- expected upstream patch or independently authored acceptance tests;
- test and validation commands;
- supported language capabilities;
- time, token, and tool limits; and
- fixture digest.

Tuning and evaluation cases must be disjoint. Repository-specific heuristics
derived from evaluation answers are prohibited.

### 12.3 Baselines

Required comparisons:

- normal coding agent with its standard repository tools;
- normal agent plus a compact repository map;
- AgenVantage initial pack plus progressive retrieval;
- full eligible corpus only where technically feasible, clearly labeled as a
  counterfactual stress baseline; and
- optional manually curated context as an approximate upper bound.

All paired runs must use the same model, model version, system instructions,
output budget, tools, timeout, and task text.

### 12.4 Required metrics

Retrieval:

- selected edit-, test-, config-, and region-level recall;
- evidence-slot coverage;
- precision or irrelevant-context rate;
- first relevant rank;
- context expansion rate; and
- unsupported or insufficient rate.

Outcome:

- acceptance-test pass rate;
- regression-test pass rate;
- patch correctness review;
- task completion rate; and
- success with confidence intervals.

Performance:

- total tokens per successful task;
- provider cost per successful task;
- cold and warm index latency;
- planning latency;
- end-to-end latency;
- peak memory and index size; and
- files reparsed per warm update.

### 12.5 Statistical requirements

- Report sample counts and per-repository distributions.
- Use paired analysis for paired agent runs.
- Report confidence intervals for task success and cost deltas.
- Avoid summing repeated full-repository counterfactuals as realized savings.
- Preserve raw, privacy-safe run records needed to reproduce aggregates.

## 13. Release Gates

### 13.1 `0.1.0-alpha`

- Python and TypeScript/JavaScript supported.
- Hard rendered-token budget invariant passes property tests.
- Exact excerpts are included in immutable manifests.
- Core test suite passes without provider credentials.
- CLI installs and runs on macOS and Linux.
- Selection reasons and missing evidence are visible.
- At least 12 existing annotated cases run reproducibly.
- Documentation uses counterfactual terminology correctly.
- License, contributing guide, code of conduct, security policy, and issue
  templates exist.

### 13.2 `0.5.0-beta`

- Incremental index and repository graph are production paths.
- Python, TypeScript/JavaScript, and Go adapters pass conformance suites.
- MCP retrieval API is documented and versioned.
- At least 30 frozen tasks across five public repositories.
- Selected-evidence sufficiency is at least `90%`.
- Edit-target recall is at least `95%`.
- Test/config evidence recall is at least `90%`.
- Warm medium-repository planning p50 is below `500 ms`.
- Budget violations and secret leaks are zero in release suites.

### 13.3 `1.0.0`

- At least 100 held-out tasks across languages and repository classes.
- Paired normal-agent and AgenVantage-agent runs are reproducible.
- Task-success regression is no more than `3` percentage points.
- Median total input-equivalent token reduction is at least `30%`.
- Provider cost claims use provider-reported or reconciled records.
- Public APIs and schemas follow semantic-versioning policy.
- Supported platforms pass CI and installation smoke tests.
- Threat model and security response process have been reviewed.

## 14. Security And Trust Requirements

Threats include malicious repository text, prompt injection in source or docs,
path traversal, symlink escape, secret exposure, oversized files, parser denial
of service, decompression bombs, poisoned repository configuration, unsafe MCP
workspace access, and telemetry leakage.

Required controls:

- treat all repository content as untrusted data;
- separate repository evidence from agent instructions in rendered formats;
- never execute discovered commands without explicit user or agent approval;
- constrain reads to allowed workspace roots;
- cap file, chunk, response, graph-expansion, and parse sizes;
- redact before persistence and rendering;
- provide telemetry opt-in and local retention controls;
- publish a vulnerability reporting process; and
- include adversarial repositories in the release suite.

## 15. Reliability And Compatibility

- Index writes must be atomic and recoverable.
- Cancellation must leave no index marked fresh incorrectly.
- Unsupported syntax must degrade to lexical retrieval with warnings.
- Dirty-worktree state must be included in pack identity.
- Deterministic mode must reproduce ranking for the same index, task, and
  configuration.
- Schema and index migrations must be tested from the previous supported
  version.
- The project must document its Python and operating-system support windows.

## 16. Open-Source Project Requirements

Before the first public release, add:

- an OSI-approved license selected by the maintainer;
- `CONTRIBUTING.md` with development setup, architecture, tests, benchmarks,
  and pull-request expectations;
- `CODE_OF_CONDUCT.md`;
- `SECURITY.md` with private disclosure instructions;
- governance and maintainer responsibilities;
- issue and pull-request templates;
- architecture decision record process;
- changelog and semantic-versioning policy;
- CI for supported Python and operating-system versions;
- dependency and secret scanning;
- reproducible package publication; and
- benchmark data licensing and provenance documentation.

The README must separate current capabilities, experimental capabilities,
future work, and unsupported claims.

## 17. Engineering Workstreams

Workstreams are designed for independent senior ownership.

### WS1: Measurement integrity and budget correctness

Deliverables:

- exact rendered-budget enforcement;
- immutable selected-excerpt scoring;
- metric terminology and schema cleanup;
- benchmark revision/digest pinning; and
- property and regression tests.

Exit gate: no budget violations and the feature suite reports only evidence
present in the emitted handoff.

### WS2: Incremental index foundation

Deliverables:

- SQLite schema and migration framework;
- atomic revisions;
- changed-file invalidation;
- parser adapter contract; and
- cold/warm performance harness.

Exit gate: a no-change warm run reparses zero source files.

### WS3: Python and TypeScript structural adapters

Deliverables:

- Tree-sitter integration;
- definitions, references, imports, tests, and landmarks;
- module resolution; and
- adapter conformance fixtures.

Exit gate: parser-confirmed graph edges replace heuristics for supported
constructs while preserving explicit fallbacks.

### WS4: Repository graph and build boundaries

Deliverables:

- typed graph storage and query layer;
- Python package and npm workspace adapters;
- test/config relationships; and
- graph provenance and confidence.

Exit gate: feature retrieval can move from an exact anchor to implementation,
caller, test, and configuration evidence through attributable edges.

### WS5: Retrieval and sufficiency controller

Deliverables:

- multi-channel retrieval;
- deterministic rank fusion;
- task evidence recipes;
- adaptive expansion; and
- context-set optimization.

Exit gate: frozen retrieval suite meets beta recall and sufficiency targets.

### WS6: Progressive retrieval and integrations

Deliverables:

- bounded query service;
- MCP server;
- typed Python API;
- cancellation and limits; and
- integration examples.

Exit gate: a clean agent can complete a task using the initial pack and MCP
expansion without unrestricted repository dumping.

### WS7: Public evaluation and economics

Deliverables:

- frozen historical-task dataset;
- normal-agent baseline runner;
- patch/test grader;
- provider-usage importer; and
- statistical report generator.

Exit gate: beta and `1.0` claims are generated from reproducible artifacts.

### WS8: Open-source readiness

Deliverables:

- governance and community files;
- CI and release automation;
- security threat model;
- packaging and compatibility matrix; and
- contributor-oriented architecture documentation.

Exit gate: an external contributor can install, reproduce tests, select an
issue, and submit a compatible change without private guidance.

## 18. Pull Request Quality Contract

Every performance or retrieval pull request must include:

- the hypothesis and affected component;
- before/after metrics using the same frozen cases;
- quality and budget-gate results;
- cold/warm runtime and memory impact when applicable;
- changed public schemas or compatibility concerns;
- failure cases and rollback strategy; and
- tests that fail without the change.

A token reduction that lowers evidence coverage is a regression unless the
tradeoff was explicitly approved and versioned.

## 19. Decision Records Required

The following decisions require ADRs before stable implementation:

- index storage engine and migration policy;
- parser and Tree-sitter distribution strategy;
- graph representation and query interface;
- rank-fusion algorithm and score explainability;
- embedding provider and local/offline behavior;
- MCP trust and workspace permission model;
- telemetry retention and privacy defaults;
- benchmark dataset licensing; and
- project license and governance model.

## 20. Immediate Execution Plan

The next implementation sequence is:

1. finish WS1 and restore the strict feature-readiness gate without lowering
   its threshold;
2. freeze the current repositories at explicit revisions and stop treating
   mutable local worktrees as durable evidence;
3. implement the WS2 index schema and prove zero-reparse no-change updates;
4. implement Python and TypeScript structural adapters;
5. create ten pinned historical feature tasks across one Python and one
   TypeScript repository;
6. add repository graph expansion and evidence-slot sufficiency;
7. expose bounded progressive retrieval through MCP;
8. run the first paired normal-agent versus AgenVantage-agent study; and
9. publish `0.1.0-alpha` only after its release gate is fully green.

## 21. Definition Of Done

The ideal-state project is complete when an independent evaluator can:

1. install AgenVantage from a public release;
2. check out a pinned, previously unseen public repository and task;
3. reproduce the index and context decisions;
4. run equivalent control and treatment coding agents;
5. verify completed patches and tests;
6. inspect exact context, retrieval, token, cost, and latency records; and
7. confirm that AgenVantage reduced total task cost without exceeding the
   declared quality tolerance.

Anything less may still be useful, but must be described according to the
specific evaluation layer it has passed.

## 22. Optional Graphify Integration

Graphify may be supported as an optional external graph candidate provider. It
must not replace AgenVantage's planner, immutable source excerpts, hard token
budget, sufficiency controller, telemetry, or task-level evaluation.

Integration contract:

- invoke a pinned Graphify release out of process only when explicitly enabled;
- consume versioned `graph.json` output through an AgenVantage-owned adapter;
- preserve source path, source range, edge direction, relation, and
  `EXTRACTED`/`INFERRED`/`AMBIGUOUS` confidence;
- use exact-symbol seeds plus bounded one- and two-hop traversal;
- rank extracted edges above inferred edges and ambiguous edges last;
- treat community membership as a diversity signal, not primary relevance;
- resolve every graph candidate back to current immutable source text;
- recount all selected text with AgenVantage's tokenizer; and
- fall back deterministically when Graphify is absent, stale, incompatible, or
  exceeds its timeout or output limit.

Graphify, NetworkX, NumPy, and its language grammars must remain optional. The
core install must not import Graphify private APIs, ingest semantic media,
modify agent instruction files, or load a complete report into a model prompt.

Implementation packets:

1. Add a versioned graph-backend protocol and read-only Graphify JSON adapter.
2. Run an ablation on the current feature suite plus at least ten pinned
   cross-file tasks from two larger repositories.
3. Proceed only if graph candidates improve region/evidence recall by at least
   five percentage points or reduce missing-context expansions by at least
   `25%`, without edit/test recall regression.
4. Add native bounded confidence-weighted traversal and deterministic rank
   fusion only after the ablation establishes value.
5. Integrate graph expansion into the planner only for partial, ambiguous, or
   explicitly cross-file tasks.

The feature-level acceptance gate is at least six paired implementations with
no task-success loss and either `>=10%` fewer total input-equivalent tokens per
successful task or fewer recovery turns. Graphify's own retrieval benchmarks
must not be presented as AgenVantage feature-work evidence.

Graphify is MIT licensed. Any copied implementation requires attribution and
license-notice preservation. Prefer independently implementing the bounded
adapter and retrieval contract. AgenVantage must select and publish its own
license before vendoring or redistributing third-party code.
