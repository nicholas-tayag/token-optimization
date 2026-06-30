# GitHub Landscape And Ranked Improvement Priorities

Prepared: June 30, 2026

This review compares AgenVantage's current local context-packaging flow against
adjacent repository-context projects and asks a narrower question than generic
"feature parity": which ideas best address the new measured gap between file
recall and answer-ready excerpt selection?

Reviewed repositories and public systems:

- [microsoft/fastcontext](https://github.com/microsoft/fastcontext)
- [zilliztech/claude-context](https://github.com/zilliztech/claude-context)
- [CocoIndex-io/CocoIndex](https://github.com/CocoIndex-io/CocoIndex)
- [continuedev/continue](https://github.com/continuedev/continue)
- [sourcebot-dev/sourcebot](https://github.com/sourcebot-dev/sourcebot)
- [yamadashy/repomix](https://github.com/yamadashy/repomix)
- [cyclotruc/gitingest](https://github.com/cyclotruc/gitingest)
- [simonw/files-to-prompt](https://github.com/simonw/files-to-prompt)

## Current AgenVantage Position

AgenVantage is now stronger than simple repository-packing tools in several
important ways:

- it measures token-budgeted reduction against real local repositories;
- it supports multi-repo packs, persistent file metadata, and git provenance;
- it now distinguishes file recall from answer-rubric sufficiency.

The June 30, 2026 benchmark also made the main remaining weakness concrete:

- the system can often select the right file;
- it still sometimes selects the wrong chunk inside that file.

That means the next improvements should focus less on "can it find the file?"
and more on "can it return the right evidence span with high precision?"

## Ranked Improvements

### 1. Deepen Symbol-To-Line And Route-To-Line Anchors

Priority: Highest

Why this is first:

- the first chunk-local anchor implementation already removed the previous
  cross-repo input-flow failure;
- the remaining miss is still excerpt precision, not repository discovery;
- more explicit line-targeted anchors are still the clearest path from "right
  file" to "right snippet."

External grounding:

- `sourcebot` emphasizes grounded definition/reference navigation;
- `claude-context` centers search over precise code spans instead of whole-file
  packing;
- `CocoIndex` focuses structured indexing and retrieval over code entities.

Recommended implementation:

- keep the new chunk-local anchor scoring and extend it to stored symbol
  definitions with line ranges in the persistent index;
- extract route handlers, exported functions, CLI commands, and tests as
  line-addressed anchors;
- boost chunks that actually contain matched anchors instead of only boosting
  the file that contains them;
- add benchmark cases that only pass when the correct excerpt is selected.

### 2. Separate Repository Exploration From Context Packing

Priority: High

Why this is second:

- current benchmarking shows retrieval mistakes are often exploratory mistakes;
- a dedicated explorer can narrow to file/line evidence before the final pack
  is assembled;
- this is the cleanest inspiration from newer agent work rather than older
  prompt-pack tools.

External grounding:

- `fastcontext` separates repository exploration from code solving and returns
  compact file-line citations;
- `continue` already treats indexing and retrieval as a background service
  instead of a one-shot prompt step.

Recommended implementation:

- add an internal exploration phase that returns candidate file-line anchors;
- keep that phase read-only and separately measurable from final packing;
- preserve the current packer as the deterministic "context assembler" after
  exploration is done.

### 3. Add Task-Shaped Query Planning

Priority: High

Why this is third:

- "compare input flows" and "explain smoke test coverage" should not use the
  same retrieval recipe;
- the remaining partial and failed cases are compound tasks with multiple
  evidence types.

External grounding:

- `fastcontext` explores iteratively from the natural-language request;
- `files-to-prompt` and `gitingest` show the practical value of explicit scope
  controls even without semantic retrieval;
- `claude-context` exposes specialized repository search behavior instead of
  one generic pack mode.

Recommended implementation:

- keep the new include/exclude glob controls as the basic scope-constraining
  layer;
- add task presets such as `explain`, `compare`, `review`, and `change`;
- expand queries differently for tests, routes, uploads, storage, and
  provenance-style tasks;
- require cross-repo compare tasks to collect at least one evidence span per
  repo before packing is considered sufficient.

### 4. Add Local Lexical Search And Structured Index Queries

Priority: Medium-High

Why this is fourth:

- precise excerpt selection gets much easier when the packer can issue local
  regex, trigram, or symbol queries instead of relying on one chunk ranker;
- this supports both human inspection and future explorer phases.

External grounding:

- Cursor publicly highlights fast regex search and indexed codebase access;
- `CocoIndex` treats indexing as the substrate for structured retrieval;
- `continue` uses a persistent sync/index layer for efficient repeated lookup.

Recommended implementation:

- add a path/content/symbol lexical index keyed by repository and file hash;
- expose exact path, symbol, and regex hits in the manifest;
- prefer exact lexical hits before semantic or fuzzy reranking.

### 5. Add Hybrid Retrieval Only After The Above

Priority: Medium

Why this is fifth:

- semantic retrieval is still useful, but the current failures are not mainly
  semantic failures;
- the benchmark does not yet justify paying complexity cost before excerpt
  precision improves.

External grounding:

- `claude-context` uses hybrid search for broad recall;
- `continue` and similar systems combine indexing with semantic search;
- `sourcebot` combines search and navigation rather than relying on embeddings
  alone.

Recommended implementation:

- keep deterministic lexical retrieval as the baseline;
- add BM25 or embeddings behind a flag;
- require answer-rubric improvement before promoting hybrid retrieval.

### 6. Expand Evaluation From Excerpt Rubrics To Generated Answers

Priority: Medium

Why this is sixth:

- required-observation scoring is now implemented and already useful;
- the next step is to test whether a model using only the packed context can
  answer without unsupported claims.

External grounding:

- `fastcontext` and newer repository-agent work report end-to-end task impact;
- benchmark work such as RepoQA and CodeRAG-Bench separates retrieval quality
  from downstream answer behavior.

Recommended implementation:

- keep the current deterministic excerpt-rubric layer;
- add generated-answer scoring with expected citations and unsupported-claim
  checks;
- compare packed-context answers against a full-context or human-grounded
  baseline.

### 7. Keep Packing UX And Safety Improvements As Supporting Work

Priority: Medium-Low

Why this is last:

- better manifests and safety checks matter;
- they are not the main blocker revealed by the current benchmark.

External grounding:

- `repomix`, `gitingest`, and `files-to-prompt` show that users care about
  format control, token trees, and explicit file inclusion.

Recommended implementation:

- keep path filters, token trees, and safety screens on the roadmap;
- do not let them displace work on excerpt precision and evaluation.

## Recommendation Summary

If the goal is to prove that AgenVantage materially reduces context overload
without losing answer quality, the next implementation sequence should be:

1. symbol-to-line and route-to-line anchors
2. separated repository exploration
3. task-shaped query planning
4. local lexical and structured index queries
5. optional hybrid retrieval
6. generated-answer evaluation
7. supporting UX and safety improvements

That order follows the stronger June 30, 2026 measurement: the biggest gap is
no longer finding the file, it is selecting the right evidence span.
