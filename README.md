# AgenVantage

**Measure the context behind every agent decision.**

AgenVantage started as a way to understand and reduce token usage in personal
LLM workflows. Agent requests rarely contain only a user message: they carry
instructions, tool schemas, retrieved documents, memory, and conversation
history.

This project treats that context as measurable infrastructure. Its first
milestone assembles alternative context packages under token budgets and
traces policy-level token metrics, creating a reproducible foundation for
future latency, caching, quality, and security evaluations.

## Current Scope

The v0 experiment harness provides:

- a typed context-component format for instructions, tools, memory, retrieved
  evidence, and user requests;
- a `full` baseline policy that includes every component;
- a `cache_aligned` policy that places stable context before variable context;
- a `budgeted` policy that retains required context and selects optional
  sections under a configurable token budget;
- `tiktoken`-based token measurements for repeatable local experiments;
- optional OpenTelemetry spans for policy runs; and
- a synthetic on-call incident scenario, with no private or employer data.

It does **not** yet make model calls, measure real provider cache hits, assess
answer quality, or represent a production enterprise system.

## Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
agenvantage run --fixture examples/synthetic_oncall_context.json --budget 360
pytest
```

Write a report and display OpenTelemetry spans locally:

```powershell
agenvantage run `
  --fixture examples/synthetic_oncall_context.json `
  --budget 360 `
  --trace-console `
  --output artifacts/oncall-report.json
```

## Why This Project

Repeated context has operational consequences. Official provider
documentation describes prompt caching around reusable prefixes and token
usage metadata; OpenTelemetry defines developing GenAI conventions for model,
tool, MCP, and token telemetry; Datadog LLM Observability consumes token and
trace information for cost and evaluation analysis.

AgenVantage begins before a provider call: it makes context composition and
policy tradeoffs inspectable. See [docs/research.md](docs/research.md) for
source-based design decisions and [docs/roadmap.md](docs/roadmap.md) for the
measured path from personal utility to portfolio-ready infrastructure.

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
examples/             Synthetic context scenarios
tests/                Deterministic policy tests
docs/                 Research basis and implementation roadmap
```

