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
  repositories;
- git-aware source scanning that includes tracked files plus untracked,
  non-ignored worktree files while still avoiding dependency folders and `.env` files;
- line-addressable source chunk ranking using task terms;
- Markdown context packages and JSON decision manifests under a token budget;
- local candidate-context reduction metrics that do not pretend to be API
  savings; and
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

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
agenvantage run --fixture examples/synthetic_oncall_context.json --budget 360
pytest
```

Build a context package for a coding task in one of your own repositories:

```powershell
agenvantage pack `
  --repo C:\Users\nicho\GithubRepos\UF-SASE-Website `
  --task "Explain the Redis-backed rate limiter, including fail-open behavior and tests." `
  --budget 1800 `
  --output artifacts/rate-limiter-context.md `
  --manifest artifacts/rate-limiter-manifest.json
```

Write a report and display OpenTelemetry spans locally:

```powershell
agenvantage run `
  --fixture examples/synthetic_oncall_context.json `
  --budget 360 `
  --trace-console `
  --output artifacts/oncall-report.json
```

Open the visual policy explorer (bar charts, budget usage, per-component inclusion):

```powershell
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

```powershell
agenvantage run --fixture examples/synthetic_oncall_context.json --budget 360
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
src/agenvantage/     Context model, policies, tracing, and CLI
examples/             Secondary synthetic context scenarios
viz/                  Browser dashboard for experiment reports
tests/                Deterministic policy tests
docs/                 Research basis and implementation roadmap
```
