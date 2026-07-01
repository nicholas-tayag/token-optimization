# PRD: Developer-Focused Context Packing

Prepared: June 30, 2026
Status: in progress (first slice implemented alongside this document)

## 1. Background

AgenVantage began as a personal tool for reducing token usage while doing
AI-assisted development. The core idea is sound: before sending a coding
question to a model, select only the repository context that matters and fit it
under a token budget. The engine (`agenvantage pack`) already does this well,
including git-aware scanning, chunk ranking, symbol/import boosts, provenance
sections, and a JSON decision manifest.

The problem is not the engine. It is the **workflow around it**. Today `pack`
is hard to reach for during real development:

- it requires `--repo` and `--budget` on every invocation;
- there are no task presets, so the user must remember which flags fit an
  "explain" vs a "review" vs a "debug" task;
- the primary output is a large JSON blob printed to stdout, which is not what a
  developer wants to read or paste;
- there is no fast path to get the packaged context into an actual prompt
  (clipboard, stdout piping);
- there is no per-project configuration for recurring defaults.

This PRD defines the changes that turn AgenVantage from an experiment harness
into a tool a developer actually reaches for mid-task.

## 2. Goals

1. Make `pack` usable in one short command with sensible defaults.
2. Provide task presets that encode good retrieval + provenance recipes.
3. Make it trivial to move the packaged context into a prompt (clipboard /
   stdout).
4. Show a readable, human summary by default; keep full JSON opt-in.
5. Support per-project defaults via a config file so recurring tasks are cheap.

### Non-goals (this milestone)

- Making live model calls or measuring provider-billed tokens (tracked in the
  roadmap Milestone 1).
- IDE / Cursor plugin integration.
- Semantic / embedding retrieval (roadmap Backlog 5).

## 3. Target user

The primary user is the author, doing AI-assisted development on local repos,
plus other developers with the same workflow: "I have a question about my code
and want to hand a model the right context without pasting whole files."

## 4. User stories

- As a developer, I can run `agenvantage pack --task "..."` in a repo and get a
  ready-to-paste context package without specifying `--repo` or `--budget`.
- As a developer, I can pick a preset (`--preset debug`) and have the tool
  include the right evidence (diff, recent commits) automatically.
- As a developer, I can pipe the package to my clipboard (`--copy`) or stdout
  (`--stdout`) and paste it straight into a chat or Cursor.
- As a developer, I can see how many tokens were selected vs. the full scanned
  corpus, and which query concepts were not covered, in a short summary.
- As a developer, I can set project defaults once in `.agenvantage.toml` (budget,
  model, preset, excludes) and stop repeating flags.

## 5. Functional requirements

### 5.1 Smart defaults for `pack`

- `--repo` defaults to the current directory (`.`) when omitted.
- `--budget` defaults to a configured value (fallback `6000`).
- `--task` remains required.

### 5.2 Task presets

`--preset {explain,review,debug,change,compare}` selects a recipe that sets
default instructions and default provenance:

| Preset  | Instructions focus                 | Diff | Log |
|---------|------------------------------------|------|-----|
| explain | describe behavior from excerpts    | no   | no  |
| review  | correctness, edge cases, risk      | yes  | no  |
| debug   | localize a bug from evidence       | yes  | yes |
| change  | plan a minimal correct edit        | yes  | yes |
| compare | contrast implementations per repo  | no   | no  |

- Default preset is `explain`.
- Explicit `--include-diff` / `--include-log` are additive over the preset.

### 5.3 Output modes

- Default: a readable text summary (task, budget usage, token reduction vs.
  scanned corpus, covered/uncovered query concepts, top selected files).
- `--stdout`: print only the Markdown context package (pipe-friendly).
- `--json`: print the full JSON decision manifest (previous default behavior).
- `--copy`: copy the Markdown package to the system clipboard.
- `--output` / `--manifest`: still write Markdown / JSON to files.

### 5.4 Project configuration

- Optional `.agenvantage.toml` read from the first `--repo` path, then the
  current directory.
- Supported keys under `[pack]`: `budget`, `model`, `preset`, `top_k`,
  `include_glob` (list), `exclude_glob` (list).
- Precedence: explicit CLI flag > config file > built-in default.
- Config parsing uses the standard library `tomllib` (Python 3.11+). On older
  interpreters without it, config is skipped rather than erroring.

## 6. Success criteria

- `agenvantage pack --task "..."` works from a repo root with no other flags.
- Presets change both instructions and provenance in the manifest.
- `--copy` / `--stdout` deliver the exact Markdown package.
- Default output is human-readable; `--json` reproduces the prior manifest.
- Config-file defaults are applied and overridden correctly by CLI flags.
- All existing tests plus new CLI/preset/config tests pass.

## 7. Rollout

Implemented incrementally in one PR:

1. Presets module + config loader (pure, unit-tested).
2. `pack` CLI rework (defaults, presets, output modes, clipboard).
3. Tests + README updates.

Future slices (tracked in `docs/roadmap.md`): named repo groups, incremental
index reuse in the summary, and the eventual provider-usage measurement loop.
