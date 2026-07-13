# PRD: Competitive Improvements And Hierarchical Agent Development

Status: proposed  
Audience: maintainers, agent orchestrators, contributors  
Prepared: July 12, 2026  
Companion docs:
[`prd-context-performance-platform.md`](prd-context-performance-platform.md),
[`prd-open-source-repository-intelligence.md`](prd-open-source-repository-intelligence.md),
[`context-tooling-landscape.md`](context-tooling-landscape.md),
[`paired-feature-implementation-validation.md`](paired-feature-implementation-validation.md)

## 1. Executive Summary

AgenVantage already compresses first-request repository context by ~90–99% on
annotated tasks and plugs into Cursor, Codex, Claude, and MCP. The next leap is
not “pack harder once.” It is to combine **research-backed context engineering**
with **hierarchical agent development** so the project can ship faster at lower
cost:

1. **Product:** move from one-shot packing toward task-aware pruning, iterative
   retrieval, session caching, and end-to-end trajectory proof.
2. **Process:** a **Manager Agent** on a strong reasoning model plans and
   reviews; **Worker Agents** on cheaper coding models implement bounded
   modules, tests, and docs under strict handoff contracts.

North-star metric (unchanged):

```text
total input-equivalent tokens per successfully completed task
```

paired with task-success rate and grounding sufficiency.

## 2. Competitive Landscape (2025–2026)

### 2.1 Product categories

| Category | Examples | Core mechanism | Strength | Weakness vs AgenVantage |
|----------|----------|----------------|----------|------------------------|
| IDE-embedded RAG | Cursor, Windsurf, Copilot Enterprise | Vector index + `@codebase` | Zero setup, always on | Opaque ranking, no budget manifest, cloud index |
| Repo map / structure | Aider | tree-sitter + PageRank repo map (~1k tokens) | Cheap structural overview | No line excerpts, no change-surface planner |
| Enterprise code intelligence | Sourcegraph Cody/Amp, Augment | Org-wide graph + search | Cross-repo, symbols/refs | Heavy infra, not local-first |
| Open retrieval plugins | Continue.dev, claude-context | Embeddings + MCP | Pluggable, local option | Generic chunks, weak task conditioning |
| Exploration agents | Microsoft fastcontext | Separate explore → cite lines | Iterative, citation-first | Not a budgeted handoff product |
| Dump utilities | repomix, gitingest, files-to-prompt | Whole-tree or glob pack | Simple | No ranking, no sufficiency gate |
| **AgenVantage (today)** | this repo | Deterministic index + planner + budget + manifest | Local, inspectable, agent-neutral | One-shot, heuristic ranking, small eval sample |

### 2.2 What big tech and research emphasize

**Anthropic — context engineering (2025)**  
Treat context as a finite attention budget. Prefer compaction, structured
note-taking, just-in-time retrieval, and **multi-agent architectures** for
long-horizon work. Smallest high-signal token set wins.

**Industry shift — RAG → Context Engine (RAGFlow 2025 review)**  
Retrieval alone is insufficient. Products need an intermediate layer that
understands agent intent, fuses sources, ranks, deduplicates, and formats
LLM-ready context dynamically per turn.

**Academic / industry papers (applicable)**

| Paper / system | Idea | AgenVantage application |
|----------------|------|-------------------------|
| **SWE-Pruner** (2026) | Task-goal-conditioned line-level pruning with lightweight skimmer | Goal hint from preset + change surface → prune selected chunks before render |
| **SWEzze / OCD** (2026) | Minimal sufficient subsequence for issue resolution; ~6× compression | Oracle distillation on benchmark cases → train/heuristic compress step |
| **CodePromptZip** (ACL 2026) | Type-aware token priority + copy mechanism for code | Preserve identifiers/paths/lines; drop comments/docstrings first under pressure |
| **Meta-RAG / RepoDistill** | Repository summaries + graph RAG | Compact repo map block; Graphify as optional expand channel |
| **Aider repo map** | PageRank over symbol references | Import graph + symbol refs already partial; add ranked map section |
| **Cursor indexing** | Incremental index, 80% threshold, semantic @codebase | Warm incremental index (partial today); explicit `@`-style file hints in handoff |

### 2.3 Orchestration patterns (for building AgenVantage itself)

| Project | Pattern | Relevant lesson |
|---------|---------|-----------------|
| Claude Code Orchestrator | Director → EM → Workers (Opus/Sonnet/Sonnet) | Model tiering by role; git worktree isolation |
| Entourage | Manager reads codebase → task DAG → parallel workers | Manager plans; workers execute in isolation |
| Hivemind | Orchestrator + Architect + PM + DAG | Architect pre-read before coding agents |
| Karajan | 22-role pipeline, researcher stage, output compression | Dedicated **researcher** role before **coder** |
| PyAgent Hierarchical | Manager → team leads → workers | Fan-out implementation with bottom-up synthesis |

**Design choice for AgenVantage development:** adopt **Manager + bounded Workers**
(not 22 roles). Manager owns architecture and acceptance; workers own single
modules.

## 3. Gap Analysis: AgenVantage Today

| Capability | Status | Gap |
|------------|--------|-----|
| First-request pack + manifest | Shipped | Strong on annotated cases |
| Auto graph policy | Shipped | Needs paired agent proof at scale |
| Plug-and-play (`init`, `codex`, `compare`) | Shipped | Copilot wrapper missing |
| Incremental index | Partial | Still heavy warm paths |
| Line-level excerpt precision | Partial | Right file, wrong chunk still possible |
| Iterative retrieval | Missing | One-shot only |
| Goal-conditioned pruning | Missing | Fixed chunk boundaries |
| Session / cache-aware deltas | Partial | `session` exists; provider proof thin |
| End-to-end trajectory savings | Early evidence | 2 paired cases only |
| Hierarchical dev orchestration | Missing | This PRD |

## 4. Improvement Feature Backlog (Prioritized)

Features are ordered by **impact on total input-equivalent tokens per success**,
not by first-prompt compression alone.

### P0 — Prove and productize what exists

| ID | Feature | Research / competitor basis | Acceptance |
|----|---------|----------------------------|------------|
| P0.1 | **`compare --live` automation** | Paired validation methodology | Scriptable control/treatment; JSONL import; 3+ repos |
| P0.2 | **Copilot / generic stdin wrapper** | Continue, Karajan adapters | `agenvantage agent run` docs + Copilot example |
| P0.3 | **Default exclude `.agenvantage/**`** | Hygiene | Shipped; regression test |
| P0.4 | **Trajectory telemetry in traces** | Anthropic compaction accounting | Import Codex JSONL → dashboard |

### P1 — Context quality under budget (Phase 1 continuation)

| ID | Feature | Research / competitor basis | Acceptance |
|----|---------|----------------------------|------------|
| P1.1 | **Evidence-slot adaptive budget** | OCD minimal sufficient set | `cold_start_sufficiency` pass rate ≥ target on held-out cases |
| P1.2 | **Goal-conditioned line pruning** | SWE-Pruner | 20%+ token drop on selected chunks with no recall regression on benchmark |
| P1.3 | **Ranked repo map section** | Aider PageRank | ≤1.5k token map; boosts cross-file tasks in ablation |
| P1.4 | **Type-aware render compression** | CodePromptZip priorities | Under budget pressure, drop low-priority spans first; manifest records drops |

### P2 — Hybrid retrieval (Phase 2)

| ID | Feature | Research / competitor basis | Acceptance |
|----|---------|----------------------------|------------|
| P2.1 | **Local BM25F over chunk index** | Cursor/Continue lexical+semantic | Unseen-task recall ≥ deterministic-only on 10+ cases |
| P2.2 | **Optional local embeddings** | claude-context | Off by default; ablation justifies latency/storage |
| P2.3 | **Rank fusion + channel ablation** | Context Engine pattern | `graphify_ablation`-style report per channel |

### P3 — Dynamic context (Phase 3)

| ID | Feature | Research / competitor basis | Acceptance |
|----|---------|----------------------------|------------|
| P3.1 | **MCP `expand_context` tool** | Anthropic JIT retrieval | Agent can fetch next best chunk within round/token cap |
| P3.2 | **Iterative pack loop** | fastcontext explore→cite | ≤3 expansion rounds; total tokens < full scan |
| P3.3 | **Tool-result compaction** | Anthropic compaction | Preserve paths, line numbers, error strings |

### P4 — Session and cache (Phase 4)

| ID | Feature | Research / competitor basis | Acceptance |
|----|---------|----------------------------|------------|
| P4.1 | **Byte-stable prefix serialization** | OpenAI/Anthropic cache docs | Same bytes → same cache key in dry-run harness |
| P4.2 | **Prefix churn diagnostics** | Platform PRD Phase 4 | Dashboard shows stale prefix warnings |
| P4.3 | **Warm-turn delta packets** | `session task` | Repeated feature tasks show ≥40% fresh-input reduction in dry-run |

### P5 — Orchestration product (optional monetizable layer)

| ID | Feature | Research / competitor basis | Acceptance |
|----|---------|----------------------------|------------|
| P5.1 | **`agenvantage orchestrate` CLI** | Entourage, CCO, Hivemind | Manager spawns workers with handoff JSON |
| P5.2 | **Worktree-isolated workers** | CCO, Entourage | Parallel tasks without file clash |
| P5.3 | **Cost ledger per role/model** | Entourage cost tracking | Trace stores manager vs worker token totals |

## 5. Hierarchical Agent Development System

### 5.1 Purpose

Use a **Manager Agent** (strong model) to decompose the PRD into work packages,
assign them to **Worker Agents** (cheaper coding models), review outputs, and
merge — mirroring the same context discipline AgenVantage sells to users.

### 5.2 Role definitions

```text
                    ┌─────────────────────────────┐
                    │     Manager Agent           │
                    │  (strong reasoning model)   │
                    │  - read PRD + benchmarks    │
                    │  - slice work packages      │
                    │  - review / accept / reject │
                    │  - integrate architecture   │
                    └──────────────┬──────────────┘
                                   │
           ┌───────────────────────┼───────────────────────┐
           ▼                       ▼                       ▼
   ┌───────────────┐      ┌───────────────┐      ┌───────────────┐
   │ Worker A      │      │ Worker B      │      │ Worker C      │
   │ (cheap code)  │      │ (cheap code)  │      │ (cheap code)  │
   │ bounded module│      │ bounded module│      │ tests/docs    │
   └───────────────┘      └───────────────┘      └───────────────┘
```

| Role | Model tier (recommended) | Responsibilities | Must NOT |
|------|--------------------------|------------------|----------|
| **Manager** | Strong (e.g. Claude Opus / Sonnet thinking, GPT-5.x high) | Architecture, package boundaries, acceptance review, benchmark interpretation, conflict resolution | Bulk implementation, drive-by refactors |
| **Worker** | Cheap code (e.g. Composer, Codex mini, Haiku, GPT-4o-mini) | Single module + tests per handoff; follow existing conventions | Cross-cutting redesign, PRD changes, dependency adds without approval |
| **Verifier** (optional worker) | Cheap or strong | Run pytest, benchmarks, `compare` smoke; report JSON only | Feature design |

### 5.3 Model routing policy

```yaml
manager:
  model: strong-reasoning
  max_context: full PRD + last worker handoffs + benchmark summaries
  tools: read repo, run benchmarks, agenvantage compare, git diff, no direct commit

worker:
  model: fast-code
  max_context: agenvantage pack handoff for assigned paths only
  tools: edit assigned paths, run scoped pytest, write handoff JSON

verifier:
  model: fast-code
  max_context: diff + test output only
  tools: pytest, ruff, compare --summary
```

**Token discipline:** Manager prepares each worker task with
`agenvantage pack --preset feature --task "<package spec>"` so workers never
receive full-repo scans.

### 5.4 Handoff contract (Worker → Manager)

Every worker turn ends with JSON:

```json
{
  "package_id": "P1.2-goal-pruning",
  "status": "completed|blocked|needs_review",
  "files_changed": ["src/agenvantage/..."],
  "tests_run": "pytest tests/test_foo.py -q",
  "tests_passed": true,
  "benchmark_delta": null,
  "risks": [],
  "manager_decision_needed": []
}
```

Manager accepts only if: scoped tests pass, no unrelated diff, handoff JSON present.

### 5.5 Manager loop (pseudocode)

```text
1. Manager reads this PRD + current claim_status + failing CI
2. Manager selects next work package from §6 (respect dependencies)
3. Manager runs: agenvantage pack --preset feature --task "<package acceptance criteria>"
4. Manager spawns Worker with handoff markdown on stdin + file allowlist
5. Worker implements; runs scoped tests; returns handoff JSON
6. Manager reviews diff + test output (strong model, minimal context)
7. If accept → mark package done; else → rework task with delta instructions
8. Verifier runs full suite on batch merge candidate
```

### 5.6 Isolation rules

- **One package = one branch or git worktree** (Entourage/CCO pattern).
- Workers may touch only paths listed in `allowed_paths`.
- Manager merges sequentially; no worker merges to main.
- All runs recorded: `agenvantage observe` trace per package with
  `manager_succeeded` / `worker_succeeded` annotations.

## 6. Work Package Partition (Agent Assignments)

Each package is independently assignable to a Worker. Manager owns ordering and
integration.

### Wave 0 — Foundation (Manager only)

| Package | Owner | Deliverable |
|---------|-------|-------------|
| M0.1 | Manager | Finalize package specs from this PRD; update claim gates |
| M0.2 | Manager | Baseline benchmark snapshot committed to `artifacts/` template |

### Wave 1 — P0 workers (parallel)

| ID | Worker task | Allowed paths | Model | Depends |
|----|-------------|---------------|-------|---------|
| W1.1 | Copilot/generic agent docs + example in plug-and-play doc | `docs/`, `README.md` | cheap | — |
| W1.2 | `compare --live` shell runner + JSONL capture helpers | `src/agenvantage/paired_codex_validation.py`, `benchmarks/` | cheap | — |
| W1.3 | Trace import for Codex JSONL in observability dashboard | `src/agenvantage/observability.py`, `tests/test_observability.py` | cheap | — |

### Wave 2 — P1 workers (parallel after M0.2)

| ID | Worker task | Allowed paths | Model | Depends |
|----|-------------|---------------|-------|---------|
| W2.1 | Goal-conditioned line pruner module + tests | `src/agenvantage/pruning.py`, tests | cheap | — |
| W2.2 | Wire pruner into `repo_context` render path | `src/agenvantage/repo_context.py`, tests | cheap | W2.1 |
| W2.3 | Ranked repo map generator (PageRank on import graph) | `src/agenvantage/repo_map.py`, tests | cheap | — |
| W2.4 | Adaptive evidence-slot budget controller | `src/agenvantage/context_planner.py`, benchmarks | cheap | — |

### Wave 3 — P2 workers (parallel after Wave 2 benchmarks)

| ID | Worker task | Allowed paths | Model | Depends |
|----|-------------|---------------|-------|---------|
| W3.1 | BM25F index over chunk records | `src/agenvantage/repo_index.py`, tests | cheap | — |
| W3.2 | Rank fusion layer + ablation hooks | `src/agenvantage/repo_context.py`, `benchmarks/graphify_ablation.py` | cheap | W3.1 |
| W3.3 | Optional embedding backend (feature flag) | new `src/agenvantage/semantic_index.py` | cheap | W3.1 |

### Wave 4 — P3 workers

| ID | Worker task | Allowed paths | Model | Depends |
|----|-------------|---------------|-------|---------|
| W4.1 | MCP `expand_context` tool | `src/agenvantage/mcp_server.py`, tests | cheap | W2.* |
| W4.2 | Iterative pack orchestrator (max 3 rounds) | `src/agenvantage/context_planner.py`, CLI | cheap | W4.1 |
| W4.3 | Tool-result compaction for agent spans | `src/agenvantage/observability.py` | cheap | — |

### Wave 5 — P4 + orchestration

| ID | Worker task | Allowed paths | Model | Depends |
|----|-------------|---------------|-------|---------|
| W5.1 | Byte-stable prefix hasher + session diagnostics | `src/agenvantage/session.py`, tests | cheap | — |
| W5.2 | **`agenvantage orchestrate` MVP** — manager spawns workers via handoff | `src/agenvantage/orchestrator.py`, CLI, tests | cheap | W1.* |
| W5.3 | Cost ledger fields on traces (manager vs worker tokens) | `src/agenvantage/observability.py` | cheap | W5.2 |

**Manager integration packages (strong model only):**

| ID | Task |
|----|------|
| M-INT-1 | Review Wave 1 PRs collectively; resolve API surface conflicts |
| M-INT-2 | Run full benchmark suite; decide P2 go/no-go |
| M-INT-3 | Author `docs/paired-codex-validation-results.md` from live runs |

## 7. Orchestrator CLI Spec (P5.1 preview)

```bash
# Manager creates plan (strong model — external or --manager-model)
agenvantage orchestrate plan \
  --prd docs/prd-hierarchical-agent-development.md \
  --repo . \
  --output .agenvantage/orchestration/plan.json

# Dispatch one work package to cheap worker
agenvantage orchestrate run \
  --package W2.1 \
  --worker-model gpt-4o-mini \
  --allowed-paths src/agenvantage/pruning.py,tests/test_pruning.py

# Manager review gate
agenvantage orchestrate review \
  --package W2.1 \
  --manager-model claude-sonnet-4-20250514
```

Implementation note: v1 can shell out to `agenvantage agent run --task ... --`
with model flags rather than building a full Entourage clone.

## 8. Success Metrics

### Product metrics (release gates)

| Gate | Target |
|------|--------|
| Paired Codex cases | ≥6 repos, ≥80% input token reduction median, success within 3pp |
| Feature benchmark edit-target recall | ≥0.95 |
| Required observation recall | ≥0.95 |
| Cold-start sufficiency | ≥0.85 context-plan readiness |
| Warm session fresh-input reduction | ≥40% vs cold on repeated tasks (dry-run) |
| MCP expand round-trip | ≤3 rounds, ≤2× handoff tokens total vs one-shot full scan |

### Process metrics (hierarchical dev)

| Metric | Target |
|--------|--------|
| Worker package first-pass accept rate | ≥70% |
| Manager tokens per merged package | tracked; minimize via pack handoffs |
| Worker tokens per package | tracked; ≤50k input equivalent |
| CI green after manager merge | 100% |

## 9. Risks And Non-Goals

**Risks**

- Cheap workers may drift architecture → mitigated by allowlists + manager review.
- Over-orchestration overhead → start with 5–8 packages, not 22 roles.
- Research features (embeddings, neural pruner) may not beat heuristics → ablation required before default-on.

**Non-goals for this PRD**

- Replacing Cursor/Codex/Copilot IDEs.
- Hosted multi-tenant index service.
- Training custom LLMs (use heuristics + optional small skimmer later).
- Autonomous merge to main without human approval.

## 10. Immediate Next Actions

1. **Manager (strong):** approve Wave 1 package specs; run baseline benchmarks.
2. **Worker W1.2:** implement `benchmarks/paired_codex_live.sh` wrapper around `compare --live`.
3. **Worker W2.1:** scaffold `pruning.py` with SWE-Pruner-inspired goal hints from preset + task.
4. **Worker W5.2:** stub `orchestrator.py` with plan JSON schema and `agent run` dispatch.
5. **Human:** run `agenvantage compare --live` on 1 external repo when API key available.

## 11. References

- Anthropic, *Effective context engineering for AI agents* (2025).
- RAGFlow, *From RAG to Context* (2025 year-end review).
- SWE-Pruner: Self-Adaptive Context Pruning for Coding Agents (arXiv 2601.16746).
- Compressing Code Context for LLM-based Issue Resolution / SWEzze (arXiv 2603.28119).
- CodePromptZip (ACL 2026 Findings).
- Aider repo-map documentation; Cursor codebase indexing (2026).
- Entourage, Claude Code Orchestrator, Hivemind, Karajan (orchestration patterns).
- Internal: [`paired-feature-implementation-validation.md`](paired-feature-implementation-validation.md), [`graphify-ablation-results.md`](graphify-ablation-results.md).
