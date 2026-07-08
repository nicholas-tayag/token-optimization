# PRD: AgenVantage Agent Observability Dev Tool

Prepared: July 8, 2026
Status: implementation started

## 1. Product Thesis

AgenVantage should become a local-first, open-source observability and cost
control tool for individual developers using AI coding agents.

Short positioning:

```text
AgenVantage is Datadog-style observability for individual AI-agent developers:
trace every agent task, understand what context the agent saw, measure
token/cost waste, evaluate quality, and reduce prompt size with repo-aware
context planning.
```

The current product already proves the token-saving wedge:

- 12 feature-work cases across 4 local repositories.
- Median feature-task prompt reduction: `80,921.5 -> 1,993.0` tokens.
- Median reduction: `96.73%`.
- Edit-target recall: `1.0`.
- Test-target recall: `0.8333`.

The next product step is to wrap that engine in an observability workflow that
is easy for new agent users to understand.

## 2. Problem

Individual developers are adopting Codex, Cursor, Claude Code, Copilot,
Gemini, and local agent workflows, but most have little visibility into what
their agent is doing.

Common questions:

- What did the agent actually see?
- Which repo files were selected, and which were omitted?
- How many tokens did this task use?
- How much did this prompt likely cost?
- Was context missing before the agent started coding?
- Did the agent fail because retrieval was bad, the model was bad, or tests
  were missing?
- Would a smaller prompt have preserved the same quality?
- Is my context stable enough to benefit from prompt caching?
- Which tasks, repos, or prompts are causing token waste?

Datadog frames the production-agent problem similarly: agentic applications are
distributed, nondeterministic, costly, and hard to evaluate. Their Agent
Observability product focuses on traces, spans, cost, latency, prompt/response
visibility, quality evaluations, and experiments. AgenVantage should adapt
those concepts for a solo developer workflow, then add something Datadog does
not provide locally by default: active repo-aware context reduction.

## 3. Target Users

Primary user:

- An individual developer using coding agents on local repositories.
- Technical enough to run a CLI, but not necessarily familiar with tracing,
  OpenTelemetry, RAG, evals, or provider billing.

Secondary users:

- Open-source maintainers who want to document AI-agent usage and context
  quality on their projects.
- Students or early AI engineers who want a visible, measurable way to learn
  how agent context works.
- Power users who want to compare Codex/Cursor/Claude Code/Gemini workflows
  without sending full repositories to every prompt.

## 4. Product Principles

- Local-first by default: no cloud account required for the MVP.
- Beginner-readable: explain every observability term in plain language.
- Evidence over claims: separate local token estimates from provider-billed
  proof.
- Trace first, optimize second: every optimization should be visible as part of
  a task trace.
- Context is a first-class artifact: selected files, omitted files, missing
  signals, and token deltas are inspectable.
- Open-source friendly: store data in SQLite/JSON, expose simple commands, and
  avoid proprietary backends.
- Datadog-friendly mental model: traces, spans, metrics, logs, evaluations,
  experiments, and dashboards.

## 5. Beginner-Friendly User Flow

The first-run experience should teach the product while producing useful data.

### 5.1 First Command

```bash
agenvantage observe init
```

Output:

```text
AgenVantage tracks AI coding-agent work locally.

A trace = one developer task.
A span = one step inside that task, like scanning files or packing context.
A metric = a number, like tokens saved or estimated cost.

Local database created: .agenvantage/observability.db
Try: agenvantage observe pack --task "Add tests for upload limits"
```

### 5.2 First Useful Run

```bash
agenvantage observe pack --task "Add upload limit smoke-test coverage"
```

The command should:

- run the existing `pack --preset feature` flow;
- create a trace for the task;
- create spans for scan, index, retrieval, context pack, and artifact pack;
- print a readable summary;
- store all metrics locally.

Example summary:

```text
Trace: trace_20260708_153012_upload_limit
Task: Add upload limit smoke-test coverage

Context:
  Full scan: 40,797 tokens
  Packed:    5,630 tokens
  Saved:     35,167 tokens (86.2%)

Likely edit file:
  server.js

Tests to inspect:
  scripts/server-smoke-test.js

Warnings:
  No config/schema file was strongly required.

Next:
  agenvantage traces show trace_20260708_153012_upload_limit
  agenvantage dashboard
```

### 5.3 Dashboard

```bash
agenvantage dashboard
```

The dashboard should open a local web page with:

- task timeline;
- token/cost trends;
- selected files and omitted files;
- context reduction by repo;
- missing-signal warnings;
- cache-readiness;
- artifact-mode savings;
- quality/evaluation status.

### 5.4 Explain Mode For New Users

Every summary should include short help text on first use:

```text
Why this matters:
Your agent performs better when it sees the right files, but whole-repo prompts
burn tokens. AgenVantage selected the likely edit/test surface and omitted
low-signal files before you prompt the agent.
```

Users can disable help:

```bash
agenvantage config set onboarding_help false
```

## 6. MVP

The MVP is not a full Datadog clone. It is a local trace store plus a simple
dashboard around the existing context planner.

### 6.1 MVP Promise

```text
Given a local repo and an AI coding task, AgenVantage records a local trace that
shows what context was selected, how many tokens were saved, what quality
signals were present or missing, and what the developer should do next.
```

### 6.2 MVP Commands

```bash
agenvantage observe init
agenvantage observe pack --task "..."
agenvantage traces list
agenvantage traces show <trace-id>
agenvantage dashboard
agenvantage experiments compare --trace <trace-id>
```

`observe pack` should be the beginner-friendly wrapper around the existing
`pack` command. Power users can still use `pack` directly.

### 6.3 MVP Data Model

Use SQLite for local durability.

Tables:

- `traces`: one developer task.
- `spans`: one operation inside a task.
- `metrics`: numeric measurements attached to traces/spans.
- `artifacts`: context markdown, handoff JSON, manifests, benchmark outputs.
- `evaluations`: deterministic or LLM-as-judge quality results.
- `events`: warnings, errors, missing signals, user annotations.

Trace fields:

- `trace_id`
- `task`
- `repo_path`
- `workflow`
- `created_at`
- `status`
- `selected_file_count`
- `full_scan_prompt_tokens`
- `packed_prompt_tokens`
- `tokens_saved`
- `reduction_percent`
- `estimated_cost_full`
- `estimated_cost_packed`
- `cache_eligible`
- `quality_status`

Span fields:

- `span_id`
- `trace_id`
- `parent_span_id`
- `name`
- `kind`
- `started_at`
- `ended_at`
- `duration_ms`
- `input_tokens`
- `output_tokens`
- `estimated_cost`
- `metadata_json`

MVP span kinds:

- `repo.scan`
- `repo.index`
- `retrieval.rank`
- `context.pack`
- `context.multimodal`
- `validation.feature`
- `validation.provider_dry_run`
- `test.run`
- `agent.external` (placeholder for future wrappers)

### 6.4 MVP Dashboard Views

#### Overview

- total traces;
- total estimated tokens saved;
- median reduction percent;
- most expensive tasks;
- tasks with missing-context warnings;
- last 10 traces.

#### Trace Detail

- task summary;
- span waterfall;
- selected files;
- omitted top-ranked files;
- token accounting;
- context warnings;
- cache-readiness;
- artifact attachments and rehydrate IDs;
- copyable agent handoff.

#### Cost And Tokens

- full-scan vs packed tokens over time;
- estimated cost saved by model/provider pricing snapshot;
- input/output/cached-token fields when available;
- top repos by token spend.

#### Quality

- edit-target recall when fixture/annotation exists;
- test-target recall when fixture/annotation exists;
- required-observation recall;
- answer-plan pass/fail;
- missing-signal warning rate;
- user thumbs-up/down or "agent succeeded" annotation.

#### Experiments

- compare prompt strategies:
  - full scan;
  - packed;
  - packed + cache-aligned;
  - packed + mixed artifact.
- compare model/pricing assumptions.
- show cost, latency, and quality deltas side by side.

### 6.5 MVP Metrics

Hero metrics:

- median prompt tokens saved;
- median context reduction percent;
- edit-target recall;
- test-target recall;
- answer-plan pass rate;
- estimated input-cost savings;
- cache-eligible trace rate;
- packed prompt size distribution.

Operational metrics:

- repo scan duration;
- index reuse rate;
- retrieval duration;
- selected chunk count;
- missing-signal warning count;
- artifact incremental savings;
- factsheet/recoverable block counts.

Beginner-friendly explanations:

- "Full scan tokens" = what you would send if you pasted every eligible repo
  chunk.
- "Packed tokens" = what AgenVantage selected for the agent.
- "Tokens saved" = full scan minus packed prompt.
- "Edit-target recall" = whether the expected file-to-change was included.
- "Missing signal" = AgenVantage is telling you it does not have enough
  evidence yet.

## 7. Concrete Next Builds

### Build 1: Local Trace Store

Status: partially implemented.

Goal: every `pack` run creates a durable trace.

Tasks:

- Add `src/agenvantage/observability.py`.
- Add SQLite schema and migration helper.
- Add trace/span writer APIs.
- Record `pack` runs into the trace store.
- Add `agenvantage observe init`.
- Add `agenvantage traces list/show`.

Acceptance:

- Running `agenvantage observe pack --task "..."` creates one trace and at
  least scan/index/rank/pack spans.
- `traces show` prints selected files, token accounting, and warnings.
- Existing `pack` behavior remains unchanged unless observe mode is used.

Current implementation slice:

- `agenvantage observe init` creates `.agenvantage/observability.db`.
- `agenvantage observe pack --task "..."` runs the feature context packer and
  records a local trace.
- `agenvantage traces list` and `agenvantage traces show <trace-id>` inspect
  the stored trace.
- `agenvantage dashboard` writes `.agenvantage/observability-dashboard.html`
  and can open a local static dashboard from the SQLite trace store.
- The initial trace schema stores traces, spans, and context/manifest artifacts.

### Build 2: Beginner-Friendly Observe Wrapper

Goal: make the product usable without knowing all existing flags.

Tasks:

- Add `agenvantage observe pack --task "..."`.
- Default to `--preset feature`.
- Automatically choose local repo from cwd.
- Store markdown package and manifest as trace artifacts.
- Print a compact summary with "what this means" explanations.

Acceptance:

- A new user can run one command from a repo and understand:
  - what context was selected;
  - how many tokens were saved;
  - what files to inspect;
  - what to paste into their agent.

### Build 3: Local Dashboard

Status: partially implemented as a generated static HTML dashboard.

Goal: visual Datadog-style UI for individual developers.

Tasks:

- Add `agenvantage dashboard`.
- Serve a local read-only web app.
- Start with server-rendered HTML or a lightweight static bundle.
- Views:
  - Overview;
  - Traces;
  - Trace Detail;
  - Tokens/Cost;
  - Quality.

Acceptance:

- Dashboard opens locally.
- It reads from SQLite.
- The current static version shows summary metrics, trace cards, selected
  files, missing-signal warnings, and spans for recent traces.
- A later server-backed version should add token trends, richer trace detail,
  filters, and a span waterfall.

### Build 4: Experiment Comparison

Status: partially implemented for local token/cost planning comparisons.

Goal: make optimization decisions visible.

Tasks:

- Add `agenvantage experiments compare --task "..."`.
- Run local variants:
  - full scan;
  - packed;
  - packed cache-aligned;
  - packed mixed-artifact.
- Store each variant as child spans or linked traces.
- Compare tokens, estimated cost, cacheability, and deterministic quality
  checks.

Acceptance:

- User can see why packed context is cheaper than full scan.
- User can see when artifact mode helps or hurts.
- User can export comparison as Markdown for README/resume proof.

Current implementation slice:

- `agenvantage experiments compare --task "..."` compares full-scan text,
  packed text, packed cache-aligned, and packed mixed-artifact variants.
- The comparison records JSON and Markdown artifacts on the local trace.
- Optional `--input-price-per-million` estimates local input-cost differences.
- Warm-cache and mixed-artifact outputs are explicitly labeled as local
  planning estimates rather than provider-billed proof.

### Build 5: User Feedback And Quality Labels

Status: partially implemented for manual trace annotations.

Goal: bridge local metrics with actual usefulness.

Tasks:

- Add `agenvantage traces annotate <trace-id>`.
- Labels:
  - agent succeeded;
  - agent failed;
  - context missing;
  - wrong file selected;
  - tests passed;
  - tests failed.
- Add optional notes.
- Surface labeled failure patterns in dashboard.

Acceptance:

- Dashboard can answer: "Which prompts saved tokens but failed quality?"

Current implementation slice:

- `agenvantage traces annotate <trace-id>` attaches a user quality label and
  optional note to a trace.
- Supported labels are `agent_succeeded`, `agent_failed`, `context_missing`,
  `wrong_file_selected`, `tests_passed`, and `tests_failed`.
- Trace detail output and the local dashboard surface the annotation history.
- Failure labels update trace `quality_status` to `failed`; success labels
  update it to `passed`.

### Build 6: Provider Usage Import

Goal: support real cost proof without requiring every user to spend money.

Tasks:

- Add import for saved OpenAI/Anthropic usage records where available.
- Map provider request IDs to AgenVantage traces.
- Store billed input/output/cached tokens when available.
- Keep estimated costs separate from billed costs.

Acceptance:

- Trace detail shows:
  - local estimated cost;
  - provider reported cost/tokens when imported;
  - reconciliation status.

### Build 7: Agent Wrappers

Goal: connect AgenVantage to daily workflows.

Potential commands:

```bash
agenvantage run -- codex "implement X"
agenvantage run -- claude "implement X"
agenvantage run -- cursor-agent "implement X"
```

MVP wrapper behavior:

- create trace;
- pack context;
- pass handoff to the external command when feasible;
- capture stdout/stderr logs;
- record command duration and exit code;
- optionally run tests.

Acceptance:

- Users can see an end-to-end trace from task to external agent result.

## 8. Non-Goals For MVP

- Cloud-hosted multi-user SaaS.
- Billing users for monitoring.
- Replacing Datadog, LangSmith, Phoenix, Weave, or Langfuse.
- Real-time prevention of hallucinations.
- Claiming provider-billed savings without imported or live provider usage
  records.
- Requiring users to understand OpenTelemetry before using the tool.
- Embedding/vector RAG as a default path.

## 9. Differentiation

Datadog, LangSmith, Phoenix, Weave, and Langfuse focus on observing LLM
applications and agent runs. AgenVantage should borrow their mental model but
differentiate on the individual-developer coding-agent workflow.

Unique wedge:

- local-first;
- open-source;
- repo-aware;
- optimized for coding agents;
- measures context before the model call;
- actively reduces context, not only observes it;
- beginner-friendly explanations;
- produces resume/README-ready metrics from local repos.

Competitive framing:

```text
Datadog for production agent teams.
LangSmith/Phoenix/Weave/Langfuse for LLM app tracing and evals.
AgenVantage for individual developers who want local AI-agent observability,
repo-context visibility, and token reduction before prompting coding agents.
```

## 10. Reference-Inspired Product Patterns

### Datadog Agent Observability

Patterns to adopt:

- traces and spans as the core interaction model;
- cost/token and latency views;
- quality/security evaluations;
- experiments for prompts, models, and parameters;
- integration with broader observability context;
- full end-to-end agent visibility.

Adaptation for AgenVantage:

- local SQLite traces instead of cloud-only traces;
- context-pack spans instead of production service spans;
- file-selection and missing-context views;
- token reduction and cache-readiness as first-class metrics.

### LangSmith

Patterns to adopt:

- trace-centric debugging;
- production-wide performance metrics;
- dataset-based evaluations;
- experiments to compare agent versions.

Adaptation:

- local feature-work fixtures become datasets;
- trace comparison should show whether retrieval changes improve or regress
  quality.

### Arize Phoenix

Patterns to adopt:

- open-source observability;
- OpenTelemetry/OpenInference compatibility over time;
- traces for model calls, retrieval, tool use, and custom logic;
- local hosting and data control.

Adaptation:

- AgenVantage should optionally export traces as OTLP/OpenInference later, but
  the MVP should not require users to know those standards.

### W&B Weave

Patterns to adopt:

- track, evaluate, and improve LLM apps;
- connect traces, evaluations, and iterative improvement;
- playground/experiment mindset.

Adaptation:

- experiment UI compares context strategies and token/cost quality tradeoffs.

### Langfuse

Patterns to adopt:

- open-source traces, monitoring, evaluations, and testing;
- broad integrations with agent frameworks;
- prompt/model iteration workflows.

Adaptation:

- integrations should start with CLI wrappers rather than framework SDKs.

## 11. Launch Plan

### Alpha

Audience: personal use and a few developer friends.

Requirements:

- trace store;
- observe pack wrapper;
- traces list/show;
- local dashboard overview;
- docs with screenshots/GIFs;
- at least 20 local traces collected from real work.

Success:

- at least 3 external users run the tool locally;
- at least 10 GitHub stars or meaningful issues from real users;
- one user reports that trace output helped them remove unnecessary context or
  debug a bad agent run.

### Public Open-Source MVP

Requirements:

- polished README quickstart;
- example demo repo or built-in demo trace;
- `agenvantage dashboard` works after `make setup`;
- benchmark evidence page;
- clear claim boundaries.

Success:

- at least 25 unique installs/clones with successful demo runs;
- at least 5 real issues/discussions from non-author users;
- at least 50 recorded local traces across users or demo submissions;
- public benchmark still shows `>= 90%` median token reduction on feature
  tasks with no edit-target recall regression.

## 12. Risks

- The product becomes too broad if it tries to clone Datadog instead of
  focusing on local AI coding-agent traces.
- Users may not trust estimated cost metrics unless claim boundaries are clear.
- Dashboard work can consume time without improving the core token/context
  engine.
- Agent wrappers may be brittle because external tools change quickly.
- Quality metrics may be misleading unless the UI clearly separates annotated
  benchmark tasks from unannotated real work.

## 13. Open Questions

- Should trace storage live in `.agenvantage/` inside each repo or in a global
  user data directory?
- Should dashboard be read-only in MVP, or support annotations immediately?
- Which external agent should be wrapped first: Codex, Claude Code, Cursor, or
  generic shell commands?
- Should AgenVantage export OpenTelemetry/OpenInference in the first public MVP
  or after local UX is stable?
- What is the minimum privacy story needed for open-source trust?

## 14. Source Notes

- YouTube workshop: Datadog Agent Observability for Gemini Enterprise Agent
  Platform, `https://youtu.be/6L2ESDibgRo?is=rMS37ZPirvI2IuqA`.
- Datadog Agent Observability docs:
  `https://docs.datadoghq.com/llm_observability/`.
- Datadog cost monitoring:
  `https://docs.datadoghq.com/llm_observability/monitoring/cost/`.
- Datadog evaluations:
  `https://docs.datadoghq.com/llm_observability/evaluations/`.
- Datadog experiments blog:
  `https://www.datadoghq.com/blog/llm-experiments/`.
- LangSmith observability:
  `https://docs.langchain.com/langsmith/observability`.
- LangSmith evaluation:
  `https://www.langchain.com/langsmith/evaluation`.
- Arize Phoenix docs:
  `https://arize.com/docs/phoenix`.
- Arize Phoenix GitHub:
  `https://github.com/arize-ai/phoenix`.
- W&B Weave docs:
  `https://docs.wandb.ai/weave`.
- W&B Weave concepts:
  `https://docs.wandb.ai/weave/concepts/what-is-weave`.
- Langfuse agent observability article:
  `https://langfuse.com/blog/2024-07-ai-agent-observability-with-langfuse`.
- CloudZero cost-focused agent observability article:
  `https://www.cloudzero.com/blog/ai-agent-observability/`.
