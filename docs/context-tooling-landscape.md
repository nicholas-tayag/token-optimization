# GitHub Landscape And Ranked Improvement Priorities

Prepared: June 30, 2026

This review compares AgenVantage's current local context-packaging flow against
adjacent GitHub projects in repository packing, code search, and agent context
retrieval.

Reviewed repositories:

- [yamadashy/repomix](https://github.com/yamadashy/repomix)
- [cyclotruc/gitingest](https://github.com/cyclotruc/gitingest)
- [simonw/files-to-prompt](https://github.com/simonw/files-to-prompt)
- [Aider-AI/aider](https://github.com/Aider-AI/aider)
- [mufeedvh/code2prompt](https://github.com/mufeedvh/code2prompt)
- [continuedev/continue](https://github.com/continuedev/continue)
- [sourcebot-dev/sourcebot](https://github.com/sourcebot-dev/sourcebot)
- [BloopAI/bloop](https://github.com/BloopAI/bloop)

## Current AgenVantage Position

AgenVantage is already stronger than simple "pack the repo into one file"
tools in one important way: it measures task-shaped selection under a token
budget and preserves line-addressable chunk identities.

It is weaker in four places:

- no git diff or commit-log provenance in the pack path;
- no symbol-aware or syntax-aware retrieval;
- no indexed retrieval or freshness layer for scale; and
- no semantic or lexical reranking beyond deterministic term matching.

## Ranked Improvements

### 1. Add Git Diff And Commit-Log Provenance

Priority: Highest

Why this is first:

- It directly explains one current benchmark failure:
  `changed-behavior-signalfoundry-upload-limit`.
- It closes the gap between "the right files were selected" and "the changed
  behavior is actually proven."

External grounding:

- `repomix` supports including git diffs and logs in packed output.
- `code2prompt` supports diff and log inclusion, including branch comparisons.
- `continue` has explicit diff-oriented context flows.

Recommended implementation:

- add optional `--include-diff` and `--include-log` inputs to `pack`;
- expose provenance sections in the manifest separately from file chunks;
- measure provenance token counts independently from repository-file tokens;
- add changed-behavior benchmark cases that only pass when provenance is present.

### 2. Add Symbol-Aware Retrieval And Identifier Expansion

Priority: High

Why this is second:

- It is the most likely fix for the missed
  `application-tracker/server.mjs` in the cross-repo hardening case.
- Current term matching is vulnerable when the answer hinges on identifiers,
  route definitions, or helper relationships more than on obvious task words.

External grounding:

- `aider` uses a repo map to surface important code structure.
- `sourcebot` emphasizes definition and reference navigation with grounded
  answers.
- `bloop` exposes symbol search and precise code navigation.

Recommended implementation:

- extract top-level symbols, route handlers, exported names, and test names;
- expand query terms with identifier and path signals before ranking;
- bias selection toward files that both match terms and define referenced symbols;
- add benchmark cases whose expected files are reachable only through symbol clues.

### 3. Add Task-Shaped Selection Controls And Output Modes

Priority: High

Why this is third:

- It improves practical usability without requiring an indexing system.
- It makes benchmarks more diagnosable because users can constrain what counts
  as eligible context.

External grounding:

- `files-to-prompt` supports line numbers, XML/Markdown output, ignore patterns,
  and stdin path lists.
- `gitingest` emphasizes include/exclude control and structured output.
- `code2prompt` uses templated prompts for different task types.

Recommended implementation:

- add include and exclude path globs on `pack`;
- optionally emit line-number-heavy and XML-style outputs;
- support task presets such as `explain`, `review`, `compare`, and `change`;
- allow preselected path lists from stdin or files.

### 4. Add Incremental Indexing And Search Contexts

Priority: Medium

Why this is fourth:

- It matters once repository sets become large or repeatedly queried.
- It reduces repeated scan and chunk cost without changing the public claim yet.

External grounding:

- `continue` maintains index freshness with a Merkle-tree-based sync layer.
- `sourcebot` treats repository indexing and search contexts as first-class.

Recommended implementation:

- cache file hashes and chunk metadata locally;
- skip unchanged files between runs;
- add named repository groups for recurring multi-repo tasks;
- expose freshness stats in the manifest.

### 5. Add Hybrid Lexical And Semantic Retrieval

Priority: Medium

Why this is not first:

- It is a bigger lift and adds operational complexity.
- The present benchmark failures are more clearly about provenance and
  structure-aware retrieval than about generic semantic similarity.

External grounding:

- `continue` uses embeddings and reranking in its codebase tools.
- `bloop` uses semantic search over indexed code.
- `sourcebot` combines search, navigation, and grounded answer flows.

Recommended implementation:

- keep deterministic term ranking as a baseline;
- add optional BM25 and embedding-backed rerankers behind flags;
- compare retrieval quality against the current benchmark suite before making
  it the default.

### 6. Add Richer Reporting For Security, Token Trees, And Selection Coverage

Priority: Medium-Low

Why this is last:

- It improves trust and diagnosis, but it does not fix the current capability
  gaps on its own.

External grounding:

- `repomix` surfaces token-count trees, diff metrics, and security checks.

Recommended implementation:

- report per-file and per-repo token shares;
- show why top-ranked files lost budget slots;
- surface sensitive-content screening for packed files and diff sections;
- graph coverage of task terms versus selected files.

## Recommendation Summary

If the goal is to prove that AgenVantage meaningfully addresses context
overload, the next implementation sequence should be:

1. git provenance
2. symbol-aware retrieval
3. task-shaped selection controls
4. incremental indexing
5. hybrid retrieval
6. richer reporting

That order follows the benchmark evidence rather than generic feature parity.
