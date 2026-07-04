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
  (`explain`/`feature`/`review`/`debug`/`change`/`compare`), clipboard/stdout output, and
  optional `.agenvantage.toml` project defaults;
- git-aware source scanning that includes tracked files plus untracked,
  non-ignored worktree files while still avoiding dependency folders and `.env` files;
- deterministic redaction of secret-looking values inside otherwise eligible
  files before ranking or rendering, with redaction counts surfaced in
  manifests and summaries;
- a persistent local repository-metadata index that caches per-file symbols,
  line-addressed symbol occurrences, local imports, and reverse import edges
  outside the worktree for reuse across runs;
- optional git diff and recent commit-log provenance sections for changed-
  behavior and review-style tasks;
- optional include and exclude path globs for narrowing eligible repository
  files before ranking;
- line-addressable source chunk ranking using task terms;
- chunk-local anchor symbols plus file-level symbol and import boosts layered
  onto chunk ranking, with a diversity-aware candidate pool and
  imported-helper expansion before budget selection;
- a deterministic feature-work planner that separates candidate exploration
  from final packing, reserves room for likely edit files, tests, config/schema
  evidence, and supporting neighbors, and can emit a structured handoff JSON
  artifact for agent workflows;
- Markdown context packages and JSON decision manifests under a token budget;
- local candidate-context reduction metrics that do not pretend to be API
  savings; and
- use-case and feature-work benchmarks that score required behavioral
  observations against the selected excerpts, not just file recall;
- an experimental `agenvantage validate-provider` workflow that can dry-run a
  cache-eligible synthetic dataset locally, normalize raw usage artifacts
  including OTLP spans/logs/per-request metrics, replay recorded provider
  results, or collect OpenAI Responses API usage,
  latency, and deterministic grading data when credentials and a pricing
  snapshot are supplied, with readiness reporting for claim-sufficiency gaps
  and paired-bootstrap reporting for measured deltas, plus optional Costs API
  reconciliation;
- an experimental `agenvantage validate-feature-provider` workflow that compares
  full-scan, AgenVantage-packed, and cache-aligned feature-work prompts against
  provider usage and deterministic answer-plan grading; and
- a cache-aware `agenvantage session` workflow that freezes stable feature
  context once and emits smaller dynamic task packets for repeated prompts; and
- a pxpipe-inspired mixed-modality pack mode that can estimate or write local
  PNG context pages for bulky gist-level context while exact identifiers,
  secrets, hashes, edit/test/config chunks, and recoverable source stay text; and
- a typed context-policy experiment harness for controlled synthetic cases.

The experiment harness also provides:

- a typed context-component format for instructions, tools, memory, retrieved
  evidence, and user requests;
- a `full` baseline policy that includes every component;
- a `cache_aligned` policy that places stable context before variable context;
- a `budgeted` policy that retains required context and selects optional
  sections under a configurable token budget;
- `tiktoken`-based token measurements for repeatable local experiments;
- optional OpenTelemetry spans for policy runs and live provider-validation
  requests; and
- a synthetic on-call incident scenario, with no private or employer data.

It does **not** yet include checked-in provider-backed result artifacts, prove
broad workload quality retention, or represent a production enterprise system.

## Quick Start

Try the built-in demo in three commands:

```bash
make setup
source .venv/bin/activate   # Windows: .venv\Scripts\activate
agenvantage demo
```

For live provider validation, you can store local keys in
[`/Users/nicky/GithubRepos/token-optimization/.env`](/Users/nicky/GithubRepos/token-optimization/.env).
The CLI and helper scripts load that file automatically if it exists.

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
agenvantage validate-provider --dry-run --summary
agenvantage validate-feature-provider --pricing artifacts/openai-pricing.json --dry-run --summary
.venv/bin/python benchmarks/modality_tradeoff_validation.py --summary
agenvantage pack --preset feature --task "..." --multimodal artifact
agenvantage rehydrate --manifest artifacts/context-images/<pack-id>/manifest.json --verify
agenvantage rehydrate --manifest artifacts/context-images/<pack-id>/manifest.json --list
agenvantage view --report artifacts/oncall-report.json
make test
```

`validate-provider --dry-run` now verifies that the synthetic provider-eval
fixture is actually cache-eligible before any API spend. The current fixture
contains `30` distinct cases across `6` failure types, produces a `1066`-token
stable prefix for cache-aligned runs, and applies enough budget pressure to
reduce selected context by about `12.82%` on average in the dry run.

`validate-feature-provider --dry-run` compares full-scan and AgenVantage-packed
feature-work prompts without making API calls. When a pricing snapshot is
provided, it also reports estimated cold and warm input-only cost deltas; these
are planning metrics, not billed-provider proof.

`benchmarks/modality_tradeoff_validation.py` is inspired by
[pxpipe](https://github.com/teamchong/pxpipe). It validates the local
mixed-modality artifact path after retrieval has already selected context. The
benchmark can write PNG context pages, factsheets, and recoverable source
manifests, but still reports estimated token deltas rather than provider-billed
savings. The benchmark also verifies every generated artifact manifest for PNG
attachments and recoverable-source hash integrity.

`pack --multimodal` uses `--modality-profile auto` by default: GPT/o-series
models use the conservative OpenAI estimate and Claude models use the standard
Claude vision estimate. Forcing an incompatible profile keeps the package
text-only instead of assuming an unsupported image-ingestion path.

When artifact mode images background context, the manifest records recoverable
`rec_...` blocks. Use `agenvantage rehydrate --manifest ... --id rec_...` to
recover exact source text instead of transcribing from image pages. Use
`--verify` to confirm image attachments exist and recoverable source hashes
still match the manifest. Line-addressed references and identifier-dense
blocks stay text-only so exact implementation evidence does not depend on image
OCR.

For the end-to-end billed-cost proof workflow, including live request spans,
OTLP export, and provider-cost reconciliation, see
[docs/provider-cost-proof-playbook.md](docs/provider-cost-proof-playbook.md).

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
| feature | start implementing a feature       | no            | no           |
| review  | correctness, edge cases, risk      | yes           | no           |
| debug   | localize a bug from evidence       | yes           | yes          |
| change  | plan a minimal, correct edit       | yes           | yes          |
| compare | contrast implementations per repo  | no            | no           |

```bash
agenvantage pack --preset feature --task "Add upload limit smoke-test coverage."
agenvantage pack --preset review --task "Review the new upload limit change."
```

The `feature` preset prints likely edit files, tests to inspect, config/schema
targets, supporting files, covered concepts, missing signals, and token
reduction. It is tuned for the first prompt of a coding-agent workflow.

### Output modes

```bash
agenvantage pack --task "..."                 # human summary (default)
agenvantage pack --task "..." --stdout        # only the Markdown package (pipe-friendly)
agenvantage pack --task "..." --json          # full JSON decision manifest
agenvantage pack --task "..." --preset feature --handoff-json
                                               # structured agent handoff payload
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

### Cache-aware feature sessions

For repeated work on the same feature, initialize a stable context prefix once:

```bash
agenvantage session init \
  --repo . \
  --task "Add memory search diagnostics and test coverage." \
  --output .agenvantage/sessions/memory-search.json
```

Then create follow-up prompts that reuse the same stable prefix and append only
the new task packet:

```bash
agenvantage session task \
  --session .agenvantage/sessions/memory-search.json \
  --task "Add an empty-result diagnostic counter." \
  --stdout
```

The session report shows stable-prefix tokens, dynamic-packet tokens, whether
the prefix is cache-eligible, and the estimated warm-call uncached token count.
Actual cache hits and billed savings still require provider usage metadata from
a live run.

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
