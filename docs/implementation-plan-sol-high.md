# Sol High Implementation Plan

## Objective

Make AgenVantage a local, cost-aware context planner for coding agents. The
planner should find the smallest evidence set that is sufficient to begin a
task, preserve a recovery path when evidence is incomplete, and report token,
cost, latency, and quality trade-offs with enough provenance to audit them.

The current baseline already provides a useful foundation:

- persistent file metadata, hashes, symbols, imports, reverse imports, and
  landmarks in `repo_index.py`;
- deterministic task-term, path, symbol, import, role, and landmark ranking in
  `repo_context.py`;
- bounded feature-oriented packing with edit/test/config/supporting evidence;
- session artifacts that separate stable context from dynamic task content;
- MCP preparation and bounded context expansion;
- local token accounting, provider-validation records, and OpenTelemetry
  project-owned context events.

The plan keeps retrieval local and deterministic by default. More expensive
retrieval or agent calls are optional policies, not prerequisites for normal
use.

## Current Iteration Status

Implemented in this iteration:

- guarded metadata fast-path reuse using size, mtime, ctime, and file identity,
  with content-hash fallback whenever metadata changes;
- index telemetry for metadata checks, hashes avoided, and fast-path reuse;
- standalone weighted reciprocal-rank fusion with deterministic tie-breaking;
- deterministic package routing to configured low, standard, and high model
  tiers, including context-sufficiency escalation, explicit escalation reasons,
  and override reporting; and
- automatic orchestration routing to `gpt-5.6-luna`, `gpt-5.6-terra`, or
  `gpt-5.6-sol` through configuration rather than provider calls in the router.

Measured locally on the current 137-file repository, the guarded warm-index
path avoided 137 content hashes and took 18.04 ms versus 21.25 ms for forced
hash validation, a 15.11% index-stage reduction. This is not an end-to-end pack
latency claim. Reciprocal-rank fusion remains disconnected from the default
retrieval path until a channel ablation demonstrates a quality improvement.

## Design Principles

1. **Index once, reuse often.** Repository scanning and structural extraction
   must be incremental and content-hash keyed.
2. **Explore before packing.** Candidate discovery should be separate from the
   final token-budget decision so exploration quality and packing efficiency
   can be measured independently.
3. **Exact evidence first.** Paths, symbols, line ranges, tests, config, and
   identifiers must remain text-grounded. Semantic or fuzzy retrieval may add
   candidates but must not replace exact anchors.
4. **Optimize total request economics.** A shorter prompt is not automatically
   cheaper if it destroys cache reuse or causes extra retrieval turns. Report
   prompt tokens, planner overhead, cache-eligible prefix size, and follow-up
   expansion cost together.
5. **No silent uncertainty.** Every pack records selected evidence, omitted
   candidates, missing signals, ranking policy, index freshness, and the reason
   for any escalation.
6. **Promote only measured improvements.** A new retrieval policy must beat the
   deterministic baseline on quality at a comparable or lower token and latency
   budget.

## Target Architecture

```text
repository files
      |
      v
incremental index: hashes -> chunks, symbols, imports, roles, landmarks
      |
      v
query planner: task shape + exact anchors + evidence requirements
      |
      v
candidate retrieval: lexical + structural + optional semantic channels
      |
      v
rank fusion: normalized channel scores + diversity + dependency expansion
      |
      v
hierarchical packer: file -> symbol -> line span -> neighbor/provenance
      |
      v
handoff/session: stable prefix + dynamic task + recovery path + telemetry
```

## Roadmap

### Phase 0: Baseline and measurement contract

**Status:** establish before changing ranking behavior.

Freeze a reproducible baseline using the existing feature benchmark and real
repository fixtures. Every later experiment compares the same task, repository
revision, tokenizer, top-k, and budget against the current deterministic
policy.

Required measurements:

- full scanned-corpus tokens;
- packed input tokens and selected chunk count;
- index build time, reused-file count, rebuilt-file count, and index bytes;
- time split into indexing, exploration, ranking, and packing;
- edit-target, test-target, and required-observation recall;
- answer-plan or downstream task-quality score where a model evaluation is
  available;
- expansion rate, missing-signal rate, and planner-overhead tokens;
- estimated cost using a versioned pricing snapshot, clearly labeled as an
  estimate until provider usage is recorded.

**Gate:** no optimization is accepted without a before/after report and an
  explanation of quality changes.

### Phase 1: Incremental indexing and local query plane

**Priority:** highest. **Expected payoff:** lower repeated-run latency and
  lower exploration overhead.

Extend the existing persistent index rather than creating a second service.

Implementation steps:

1. Persist per-file content hash, size, mtime, language, role, chunk hashes,
   symbol occurrences with line ranges, imports, reverse imports, and
   landmarks.
2. Reuse unchanged file records and unchanged chunk records. Rebuild only
   changed files and update reverse-import edges affected by those files.
3. Add a local exact-query layer for path, identifier, regex, and normalized
   content-term hits. Return line-addressed anchors, not only file matches.
4. Store index format and extractor versions. Invalidate only incompatible
   records; do not silently mix stale structural metadata with current text.
5. Expose freshness and reuse statistics in the pack manifest and telemetry.

Complexity target:

- cold indexing: `O(F + B)` over eligible file bytes `B`;
- warm indexing: `O(C + E_delta)` where `C` is changed-file bytes and
  `E_delta` is affected import-edge maintenance;
- query lookup: `O(H + K log K)` for `H` exact hits and `K` candidates;
- memory: `O(F + S + E)` for files, symbols, and local import edges.

Avoid scanning every file for every query after the index is warm.

**Gate:** at least 90% of unchanged files reused on a no-op run, measurable
warm-index speedup on the benchmark repositories, and no recall regression.

### Phase 2: Hybrid rank fusion

**Priority:** high. **Expected payoff:** better recall for vocabulary mismatch
  while preserving exact retrieval precision.

Keep the existing deterministic ranker as the control. Add optional retrieval
channels:

- exact lexical/path/identifier hits;
- structural symbol and import hits;
- BM25-style local term ranking over indexed chunks;
- optional embedding or late-reranking channel only when explicitly enabled.

Fuse channels with normalized scores and deterministic tie-breaking. A first
implementation can use weighted reciprocal rank fusion rather than allowing
raw scores from different channels to compete directly. Exact identifier hits
receive a hard-preservation rule: a semantic result cannot displace an exact
required anchor solely because of a higher fuzzy score.

Required ablations:

- deterministic baseline;
- lexical plus structural;
- lexical plus structural plus BM25;
- optional hybrid with semantic reranking.

Record per-channel contribution, candidate overlap, selected evidence type,
and score margin. Disable a channel automatically when it adds tokens without
improving recall or quality.

**Gate:** improve required-observation recall or answer-plan quality by a
  predefined margin at no more than 5% additional packed tokens and no more
  than 20% additional local retrieval latency.

### Phase 3: Hierarchical retrieval and evidence expansion

**Priority:** high. **Expected payoff:** higher evidence precision with fewer
  full-file reads.

Represent retrieval as a bounded hierarchy:

1. repository and file anchors;
2. symbol, route, command, test, or config anchors;
3. exact line spans around anchors;
4. local import, reverse-import, test, and provenance neighbors;
5. controlled rehydration of omitted source when the evidence contract is not
   satisfied.

The explorer returns an evidence plan before the packer consumes tokens. Each
task shape declares required slots, for example edit target, test target,
configuration, supporting implementation, or change provenance. Expansion is
triggered only by a missing slot, low confidence margin, or an explicit
request for more evidence.

Use the existing MCP `expand_context` behavior as the recovery mechanism. The
first pack must state what is missing instead of guessing.

**Gate:** reduce packed tokens for the same required-observation recall, while
  keeping exact-identifier probes at zero regressions and bounding expansion
  turns per task.

### Phase 4: Cost-aware subagent orchestration

**Priority:** medium-high. **Expected payoff:** avoid spending a large model
  call on work that can be done by indexed retrieval or a small specialist.

Partition work by cost and reversibility. Subagents receive a bounded handoff,
not the entire repository.

Recommended roles:

- `indexer`: inspect freshness and update local metadata; no model call;
- `explorer`: identify candidate paths, symbols, and evidence slots;
- `retrieval-judge`: compare a small candidate set and flag uncertainty;
- `implementation-planner`: produce edit/test plan from the packed evidence;
- `quality-reviewer`: inspect diffs, tests, and acceptance criteria;
- `manager`: reconcile artifacts and decide whether escalation is justified.

Use lower-cost models only for bounded, structured tasks with a strict output
schema. Keep the manager and final quality decision on a stronger model when
the user requests implementation. Do not invoke a subagent for a query already
resolved by exact index evidence.

For every delegation, record:

- model and request tokens;
- context tokens supplied;
- planner/subagent latency;
- result validity and retry count;
- whether the result changed the final selected context;
- estimated cost and cost avoided by not using a larger model.

**Gate:** delegated work must reduce total end-to-end token/cost expenditure
  or improve quality at the same cost. A subagent that only duplicates local
  ranking is removed.

### Phase 5: Provider-backed economics and quality validation

**Priority:** after Phases 1-4 are stable.

Compare full unaligned, full cache-aligned, budgeted unaligned, and budgeted
cache-aligned policies using actual provider usage metadata where available.
Keep local estimates separate from billed results.

For repeated sessions, measure:

- total input tokens;
- cached input tokens and cache writes;
- output tokens;
- provider latency;
- billed request cost;
- planner and subagent overhead;
- task quality, citation correctness, and unsupported-claim rate.

The primary metric is net cost per successful task, not prompt-size
reduction. A policy fails if it saves input tokens but causes enough retries,
expansions, or quality regressions to increase net cost.

**Gate:** report confidence intervals across repeated tasks and repositories;
  do not claim production savings from theoretical price multiplication alone.

## Subagent Execution Model

The manager should partition work into independent, low-context tasks and pass
only the relevant files plus acceptance criteria. Suggested wave structure:

- Wave 1: one index specialist and one benchmark specialist inspect separate
  paths and produce implementation notes only.
- Wave 2: one retrieval specialist implements incremental query/index changes;
  one metrics specialist adds instrumentation and reports.
- Wave 3: one fusion specialist implements optional ranking; one reviewer runs
  ablations and checks regressions.
- Wave 4: one orchestration specialist wires cost-aware delegation; one
  quality reviewer validates end-to-end behavior.

Each worker handoff should contain the task, allowed paths, token budget,
expected artifact, and explicit non-goals. Workers should return structured
results rather than long narrative context. The manager rejects out-of-scope
changes, missing tests, invalid schemas, and claims unsupported by artifacts.

## Risks And Trade-offs

- **Incremental invalidation bugs:** stale symbols or import edges can be worse
  than a slow scan. Use extractor/version hashes and periodic full rebuilds.
- **Hybrid-score complexity:** semantic channels can improve recall while
  reducing precision and increasing latency. Keep them opt-in and require
  ablations.
- **Hierarchical over-expansion:** neighbor traversal can recreate the original
  context overload. Enforce depth, token, and evidence-slot budgets.
- **Cache alignment versus freshness:** stable prefixes can preserve cache reuse
  while omitting changed evidence. Hash the context and invalidate sessions
  when relevant files or policy versions change.
- **Subagent overhead:** orchestration can cost more than it saves. Measure
  manager, worker, retry, and expansion tokens as one request budget.
- **Benchmark overfitting:** hand-authored tasks may reward known terms. Add
  held-out repository revisions and naturally occurring tasks before making
  broad claims.
- **Privacy and provenance:** local indexing should exclude secrets and make
  external provider transmission explicit. Preserve source paths, hashes, and
  license metadata where content leaves the machine.

## Explicit Non-Goals

- Replacing IDE-native indexing, code navigation, or file editing.
- Building a hosted repository mirror or background cloud service.
- Making embeddings or an external vector database mandatory.
- Using an LLM to scan the entire repository before every request.
- Treating image context as exact implementation evidence.
- Claiming billed provider savings from local token estimates.
- Claiming task completion from retrieval recall alone.
- Automatically modifying user repositories without explicit authorization.
- Adding dashboards or onboarding before retrieval economics and quality are
  reliable.

## Definition Of Done For The Next Release

The release is ready for broader open-source testing when:

- warm indexing reuses unchanged content and exposes freshness metadata;
- exact and structural search return line-addressed anchors;
- baseline and hybrid policies are reproducibly comparable;
- hierarchical packing reports evidence slots, omissions, and expansions;
- subagent calls are bounded and included in total cost accounting;
- feature benchmark quality does not regress and held-out tasks are included;
- local token savings, estimated cost, provider usage, and answer quality are
  labeled separately;
- all documented non-goals remain true.
