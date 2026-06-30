# Repository Context Retrieval Research And Implementation Plan

Prepared: June 30, 2026

## Why Repository Agents Do Not Send The Whole Repo

Systems such as Cursor and GitHub Copilot generally separate repository
understanding into two stages:

1. build indexes or cached metadata ahead of time or in the background;
2. retrieve and pack only a small task-shaped subset at answer time.

This matters because indexing cost and prompt-token cost are different.
Repository-scale systems spend work on chunking, hashing, symbol extraction,
lexical indexing, and often embeddings so that each future question sends only
the relevant subset.

Relevant official sources:

- Cursor, secure codebase indexing:
  <https://cursor.com/blog/secure-codebase-indexing>
- Cursor, fast regex search:
  <https://cursor.com/blog/fast-regex-search>
- GitHub Copilot repository indexing:
  <https://docs.github.com/en/copilot/concepts/indexing-repositories-for-copilot-chat>
- GitHub Copilot spaces:
  <https://docs.github.com/en/copilot/concepts/context/spaces>

## Core Research Findings To Translate Into AgenVantage

### 1. Long Context Alone Is Not A Reliable Strategy

Even when models accept long inputs, retrieval and ordering still matter.
The "Lost in the Middle" paper shows that model performance degrades when
relevant information is buried in long context windows rather than placed in
salient positions.

Implication for AgenVantage:

- do not treat larger context windows as a substitute for retrieval;
- keep measuring early-rank placement and compact task packs;
- explicitly test whether important files appear early enough to remain useful.

Source:

- "Lost in the Middle: How Language Models Use Long Contexts"
  <https://arxiv.org/abs/2307.03172>

### 2. Repository Retrieval Should Be Iterative, Not One-Shot

RepoCoder shows that repository-level code completion improves when retrieval
is iterative: retrieve context, generate or reason, then retrieve again using
new signals instead of relying on a single initial search.

Implication for AgenVantage:

- add optional multi-pass retrieval for harder tasks;
- allow the first pass to discover anchor files or symbols;
- use those anchors to expand to neighbors such as tests, handlers, helpers,
  and persistence layers.

Source:

- "RepoCoder: Repository-Level Code Completion Through Iterative Retrieval and
  Generation" <https://arxiv.org/abs/2303.12570>

### 3. Structural Relations Matter Beyond Surface Text

GraphCodeBERT shows that code understanding improves when structural relations
such as data flow are represented alongside plain token sequences.

Implication for AgenVantage:

- pure term matching is not enough for many tasks;
- extract and index structure such as exported symbols, route names, imports,
  and test targets;
- eventually experiment with graph-aware reranking features even if the first
  implementation stays deterministic.

Source:

- "GraphCodeBERT: Pre-training Code Representations with Data Flow"
  <https://openreview.net/forum?id=jLoC4ez43PZ>

### 4. Hybrid Retrieval Usually Beats A Single Method

Dense retrieval, lexical retrieval, and late interaction each solve different
 failure modes. Dense Passage Retrieval established the dense baseline, while
 ColBERT showed a practical late-interaction design that preserves token-level
 matching strength during retrieval.

Implication for AgenVantage:

- keep a cheap lexical path for exact names, paths, and symbols;
- add optional embeddings for semantic recall;
- consider reranking rather than replacing lexical retrieval outright.

Sources:

- "Dense Passage Retrieval for Open-Domain Question Answering"
  <https://arxiv.org/abs/2004.04906>
- "ColBERT: Efficient and Effective Passage Search via Contextualized Late
  Interaction over BERT" <https://arxiv.org/abs/2004.12832>

### 5. Diversity Control Matters In Packed Context

Classic information-retrieval work on Maximal Marginal Relevance shows why
rankers should balance relevance and novelty instead of repeatedly selecting
near-duplicate evidence.

Implication for AgenVantage:

- keep the current diversity penalty idea;
- upgrade it to explicit diversity controls across files, repositories,
  symbols, and evidence type;
- avoid wasting budget on multiple chunks that repeat the same concept.

Source:

- "The Use of MMR, Diversity-Based Reranking for Reordering Documents and
  Producing Summaries"
  <https://www.cs.cmu.edu/~jgc/publication/The_Use_MMR_Diversity_Based_LTMIR_1998.pdf>

### 6. Index Freshness And Incremental Sync Are First-Class

Cursor publicly describes chunk-content caching and similarity-hash-based reuse.
Continue documents an index-sync design based on Merkle trees and content-based
addressing for incremental updates.

Implication for AgenVantage:

- do not rebuild every chunk on every run once indexing lands;
- cache chunk hashes, symbol metadata, and optional embeddings;
- update only changed files;
- keep retrieval metadata reproducible and inspectable.

Sources:

- Cursor, secure codebase indexing:
  <https://cursor.com/blog/secure-codebase-indexing>
- Continue sync/index design:
  <https://github.com/continuedev/continue/blob/main/sync/src/README.md>

### 7. Evaluation Needs Both Retrieval Metrics And End-Task Metrics

DevEval focuses on repository-level coding tasks and demonstrates that whole
repository reasoning is a distinct benchmark problem. SWE-bench provides a
useful downstream repair benchmark for issue-resolution behavior.

Implication for AgenVantage:

- keep retrieval evaluation separate from answer evaluation;
- measure file recall, rank, minimum grounding budget, and sufficiency;
- add downstream answer scoring or issue-resolution tasks only after retrieval
  quality is stable.

Sources:

- "DevEval: A Manually-Annotated Code Generation Benchmark Aligned with Real-
  World Code Repositories" <https://arxiv.org/abs/2405.19856>
- "SWE-bench: Can Language Models Resolve Real-World GitHub Issues?"
  <https://arxiv.org/abs/2310.06770>

## Additional Facets AgenVantage Should Cover

These are the implementation dimensions that go beyond the current roadmap
bullets.

### Lexical Retrieval Plane

Needed because:

- exact paths, test names, symbols, flags, and config keys are often best found
  with lexical search;
- lexical lookup is cheap and usually local.

Implementation ideas:

- trigram or regex index over file text and paths;
- symbol-name lookup table;
- configurable path and extension filters;
- exact-match boosts for files, symbols, and tests.

### Structural Metadata Plane

Needed because:

- repo tasks often depend on relations such as "this test covers that route" or
  "this entrypoint imports that storage layer."

Implementation ideas:

- symbol table per file;
- import adjacency graph;
- route and CLI command extraction;
- test-to-implementation links when derivable;
- future call-graph heuristics where cheap.

### Provenance Plane

Needed because:

- changed-behavior tasks need proof of what changed, not just present-day code.

Implementation ideas:

- staged and working-tree diff capture;
- optional recent commit log capture;
- blame or recency metadata as a later extension;
- separate provenance token accounting from file-content accounting.

### Query Planning Plane

Needed because:

- many coding tasks are compound: "compare", "trace", "recommend tests", and
  "identify changed behavior" should not all use the same retrieval recipe.

Implementation ideas:

- classify tasks into presets such as `explain`, `review`, `compare`, `change`;
- expand queries with task-specific anchors;
- perform iterative retrieval for hard multi-repo questions;
- optionally stop retrieval early when sufficiency checks pass.

### Sufficiency Verification Plane

Needed because:

- retrieval success is not binary once the candidate set is small.

Implementation ideas:

- leave-one-file-out ablation on selected packs;
- minimum-budget and minimum-top-k search, already started;
- future required-observation rubrics for generated answers;
- unsupported-claim checks for answer validation.

### Privacy And Safety Plane

Needed because:

- repository context tools can surface secrets, private data, or irrelevant
  personal files if scanning is too permissive.

Implementation ideas:

- explicit sensitive-content screening before packing;
- allowlist and denylist controls by path pattern;
- separate policy handling for repository files versus diffs and logs;
- reproducible manifest notes when content is excluded for safety.

## Recommended AgenVantage Architecture

### Phase 1: Deterministic Indexed Retrieval

Build first:

- persistent local file/chunk index;
- chunk hash cache;
- symbol and import extraction;
- lexical search over paths, symbols, and content;
- task presets for `explain`, `compare`, `review`, and `change`.

Why first:

- this is cheaper and easier to validate than embeddings;
- it directly addresses current benchmark failures.

### Phase 2: Provenance And Graph Expansion

Build next:

- diff and log inclusion;
- test-to-code and import-neighbor expansion;
- explicit evidence-type diversity in ranking;
- richer manifest reporting for why files were selected.

Why second:

- changed-behavior tasks are currently underpowered;
- multi-file reasoning needs explicit expansion paths.

### Phase 3: Optional Semantic Retrieval

Build only after Phases 1 and 2 are measurable:

- embedding cache keyed by chunk hash;
- hybrid lexical plus embedding retrieval;
- optional reranking stage;
- retrieval-quality comparison against the benchmark suite.

Why third:

- semantic retrieval is useful, but it should be introduced only after the
  deterministic baseline is well understood.

### Phase 4: Answer-Level Evaluation

Build after retrieval is stable:

- answer-generation evaluation using packed context only;
- required-observation and unsupported-claim rubrics;
- benchmark tasks mapped to real repository behaviors.

Why fourth:

- this is the point where AgenVantage can begin making stronger claims about
  end-task sufficiency rather than retrieval sufficiency alone.

## Immediate Implementation Recommendations

If the next future runs should maximize real progress, the order should be:

1. add a persistent lexical and metadata index
2. add git diff and commit-log provenance
3. add symbol-aware and graph-aware expansion
4. add task presets and iterative retrieval
5. add optional embedding-backed reranking
6. add answer-level sufficiency evaluation

That sequence best matches the current benchmark evidence and the external
research.
