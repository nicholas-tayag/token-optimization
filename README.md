# AgenVantage

**Measure the context behind every agent decision.**

AgenVantage started as a way to understand and reduce token usage in my own
AI-assisted development workflows. Coding requests rarely contain only a
question: they carry source files, diffs, test failures, documentation, tool
schemas, instructions, and conversation history.

This project treats that context as measurable input. It builds token-budgeted
context packages from local repositories before a model call occurs, showing
what was selected, what was omitted, and why. A secondary experiment harness
compares synthetic context policies as a foundation for future API-backed
latency, caching, cost, and quality measurements.

## Current Scope

The current local workflow provides:

- an `agenvantage pack` command for real coding questions over local
  repositories, including multi-repository tasks with repeated `--repo` flags,
  smart defaults (`--repo .`, default budget), task presets
  (`explain`/`review`/`debug`/`change`/`compare`), clipboard/stdout output, and
  optional `.agenvantage.toml` project defaults;
- git-aware source scanning that includes tracked files plus untracked,
  non-ignored worktree files while still avoiding dependency folders and `.env` files;
- a persistent local repository-metadata index that caches per-file symbols and
  imports outside the worktree for reuse across runs;
- optional git diff and recent commit-log provenance sections for changed-
  behavior and review-style tasks;
- optional include and exclude path globs for narrowing eligible repository
  files before ranking;
- line-addressable source chunk ranking using task terms;
- chunk-local anchor symbols plus file-level symbol and import boosts layered
  onto chunk ranking, with a diversity-aware candidate pool and
  imported-helper expansion before budget selection;
- Markdown context packages and JSON decision manifests under a token budget;
- local candidate-context reduction metrics that do not pretend to be API
  savings; and
- a use-case benchmark that scores required behavioral observations against the
  selected excerpts, not just file recall; and
- a typed context-policy experiment harness for controlled synthetic cases.

The experiment harness also provides:

- a typed context-component format for instructions, tools, memory, retrieved
  evidence, and user requests;
- a `full` baseline policy that includes every component;
- a `cache_aligned` policy that places stable context before variable context;
- a `budgeted` policy that retains required context and selects optional
  sections under a configurable token budget;
- `tiktoken`-based token measurements for repeatable local experiments;
- optional OpenTelemetry spans for policy runs; and
- a synthetic on-call incident scenario, with no private or employer data.

It does **not** yet make model calls, measure real provider cache hits or
billed cost, assess generated-answer quality, or represent a production
enterprise system.

## Quick Start

Try the built-in demo in three commands:

```bash
make setup
source .venv/bin/activate   # Windows: .venv\Scripts\activate
agenvantage demo
```

`agenvantage demo` runs the synthetic on-call scenario, writes
`artifacts/oncall-report.json`, prints a short summary, and opens the policy
explorer dashboard.

### Manual setup

macOS / Linux:

```bash
bash scripts/setup.sh
source .venv/bin/activate
agenvantage demo
pytest
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
agenvantage demo
pytest
```

If `python` is not available, install Python 3.10+ or use
[uv](https://docs.astral.sh/uv/) (`uv venv .venv --python 3.12`).

### Common commands

```bash
agenvantage demo                              # built-in on-call walkthrough
agenvantage run --summary                     # default scenario, readable output
agenvantage view --report artifacts/oncall-report.json
make test
```

## Pack: your day-to-day token saver

`agenvantage pack` is the developer workflow: it selects the repository context
that matters for a task, fits it under a token budget, and hands you a
ready-to-paste package. Run it from inside a repo with just a task:

```bash
agenvantage pack --task "Explain the rate limiter and its fail-open behavior."
```

That uses smart defaults (`--repo .`, a default budget, and the `explain`
preset) and prints a readable summary of what was selected and how many tokens
were saved versus scanning the whole repo.

Send the package straight into your prompt with the clipboard or stdout:

```bash
agenvantage pack --task "Why do the checkout tests fail?" --preset debug --copy
agenvantage pack --task "token budget selection" --stdout | pbcopy
```

### Task presets

Presets pick good instructions and provenance so you do not have to remember
flags:

| Preset  | Focus                              | Includes diff | Includes log |
|---------|------------------------------------|---------------|--------------|
| explain | describe behavior (default)        | no            | no           |
| review  | correctness, edge cases, risk      | yes           | no           |
| debug   | localize a bug from evidence       | yes           | yes          |
| change  | plan a minimal, correct edit       | yes           | yes          |
| compare | contrast implementations per repo  | no            | no           |

```bash
agenvantage pack --preset review --task "Review the new upload limit change."
```

### Output modes

```bash
agenvantage pack --task "..."                 # human summary (default)
agenvantage pack --task "..." --stdout        # only the Markdown package (pipe-friendly)
agenvantage pack --task "..." --json          # full JSON decision manifest
agenvantage pack --task "..." --copy          # copy the package to the clipboard
agenvantage pack --task "..." \
  --output artifacts/context.md \
  --manifest artifacts/manifest.json          # write files
```

### Project defaults (`.agenvantage.toml`)

Drop a config file at a repo root to stop repeating flags. CLI flags always
override it.

```toml
[pack]
budget = 6000
model = "gpt-4o-mini"
preset = "explain"
top_k = 20
include_glob = ["src/*"]
exclude_glob = ["docs/*", "**/*.min.js"]
```

### Multi-repo packages

```bash
agenvantage pack \
  --repo /path/to/repo-a \
  --repo /path/to/repo-b \
  --preset compare \
  --task "Compare the local static server hardening in both apps." \
  --include-glob "scripts/*" \
  --exclude-glob "docs/*" \
  --budget 2200 \
  --output artifacts/multi-repo-context.md \
  --manifest artifacts/multi-repo-manifest.json
```

See [docs/prd-developer-workflow.md](docs/prd-developer-workflow.md) for the
product requirements behind this workflow.

Write a report and display OpenTelemetry spans locally:

```bash
agenvantage run \
  --fixture examples/synthetic_oncall_context.json \
  --budget 360 \
  --trace-console \
  --summary \
  --output artifacts/oncall-report.json
```

Open the visual policy explorer (bar charts, budget usage, per-component inclusion):

```bash
agenvantage view
```

After generating a report, load it in the dashboard via the file picker, or open the
dashboard path printed by `agenvantage view --report artifacts/oncall-report.json`.

## Why This Project

The immediate use case is practical: send less irrelevant repository text
when asking an LLM to explain, debug, or review code. Provider documentation
also establishes a future measurement path: repeated stable prefixes can be
cached, response metadata can reveal cached tokens, and OpenTelemetry can
represent real GenAI usage once provider calls exist.

AgenVantage begins before a provider call: it makes context composition
inspectable in real coding workflows. See
[docs/context-planning-layer.md](docs/context-planning-layer.md) for the
pre-inference design, [docs/real-token-tradeoff-experiment.md](docs/real-token-tradeoff-experiment.md)
for the eventual API validation plan, and [docs/roadmap.md](docs/roadmap.md)
for the measured build sequence.

## Example Experiment

```bash
agenvantage run --summary
```

The report compares each policy against `full`, showing:

- selected and excluded components;
- total input tokens;
- stable-prefix tokens eligible for later cache experiments; and
- token savings relative to baseline.

Stable-prefix tokens are a local structural measurement, not proof of a cache
hit or provider cost reduction.

## Repository Layout

```text
src/agenvantage/     Context model, policies, presets, config, tracing, and CLI
examples/             Secondary synthetic context scenarios
viz/                  Browser dashboard for experiment reports
tests/                Deterministic policy, CLI, preset, and config tests
docs/                 Research basis, PRD, and implementation roadmap
```
