# PRD: Three-Layer Agent Cost Stack (Overnight Build)

Status: **approved for autonomous implementation**  
Audience: Codex / Claude Code overnight agent  
Prepared: July 13, 2026  
Repository: `<repository-root>`
Companion docs:
[`prd-hierarchical-agent-development.md`](prd-hierarchical-agent-development.md),
[`prd-context-performance-platform.md`](prd-context-performance-platform.md),
[`paired-feature-implementation-validation.md`](paired-feature-implementation-validation.md),
[`claim-status.md`](claim-status.md)

---

## COPY THIS BLOCK INTO CODEX (overnight mission)

```text
MISSION: Implement the Three-Layer Agent Cost Stack PRD in token-optimization.

Read the full PRD at docs/prd-three-layer-agent-cost-stack.md and execute Waves 1–4 in order.
Do not skip acceptance gates. Do not weaken existing tests.

NORTH-STAR METRIC:
  total input-equivalent tokens per successfully completed task
  paired with task-success rate and grounding sufficiency.

CONSTRAINTS:
  - Minimize diff scope per package; one package = one focused change set.
  - Run `make test` or `.venv/bin/pytest -q` after every package; fix regressions before continuing.
  - Do not claim provider-billed savings in docs unless live artifacts exist.
  - Do not add heavy new dependencies without manager approval (tiktoken/otel only in core).
  - Match existing code style in src/agenvantage/.
  - Commit each completed wave with a clear message. Do not force-push.

SETUP:
  cd <repository-root>
  source .venv/bin/activate || make setup
  .venv/bin/pytest -q   # baseline must be green before starting

OVERNIGHT ORDER (stop only if blocked):
  Wave 1 → Wave 2 → Wave 3 → Wave 4 → final pytest + benchmark smoke

END STATE:
  - Feature preset includes Ponytail-style output discipline
  - MCP expand_context recovers confidence when missing_signals fire
  - Orchestrator dispatches workers with --worker-model and role routing
  - Traces record manager vs worker token totals
  - docs/three-layer-stack-results.md summarizes what shipped and benchmark deltas
```

---

## 1. Executive Summary

AgenVantage today proves **input-side** savings (~93% median prompt reduction on 12
annotated feature tasks). [Ponytail](https://github.com/DietrichGebert/ponytail)
proves **output-side** savings (~54% LOC, ~22% session tokens on agentic benchmarks).
Cursor-style **model routing** is the third axis: strong model plans/reviews, cheap
model implements bounded packages.

This PRD completes the combined stack:

| Layer | Mechanism | Primary savings |
|-------|-----------|-----------------|
| **L1 Input** | Budgeted pack + sufficiency gates + JIT expand | Input / exploration tokens |
| **L2 Process** | Manager / worker / verifier model routing | Reasoning + trajectory tokens |
| **L3 Output** | Ponytail-inspired minimal-code discipline | Output + diff + retry tokens |

**Design principle:** confidence comes from *expand when uncertain*, not *pack
everything upfront*. Output discipline runs *after* context is supplied, not
instead of it.

---

## 2. Current State (do not re-implement)

Already shipped — extend, do not rewrite:

| Component | Path | Status |
|-----------|------|--------|
| Pack pipeline | `src/agenvantage/repo_context.py` | Shipped |
| Goal-conditioned pruning | `src/agenvantage/pruning.py` | Shipped (wire + benchmark) |
| Context planner | `src/agenvantage/context_planner.py` | Shipped |
| Orchestrator skeleton | `src/agenvantage/orchestrator.py` | Partial — no model routing |
| Orchestrate CLI | `src/agenvantage/cli.py` (`orchestrate plan/run/review`) | Partial — no `--worker-model` |
| Agent launcher | `src/agenvantage/agent_launcher.py` | Has `--model` for codex/claude |
| MCP server | `src/agenvantage/mcp_server.py` | `prepare_context`, `search_graph`, `context_status` |
| Partition fixture | `examples/agent_development_partition.json` | Shipped — update with new packages |
| Tests | `tests/` (237 passing) | Must stay green |
| Paired validation | `src/agenvantage/paired_codex_validation.py` | Shipped |

---

## 3. Architecture

```text
┌─────────────────────────────────────────────────────────────────┐
│ MANAGER (strong model)                                          │
│ Context: PRD summary + last diff + review JSON (small)          │
│ Tools: orchestrate plan/review, benchmarks, git diff            │
└────────────────────────────┬────────────────────────────────────┘
                             │
         ┌───────────────────┼───────────────────┐
         ▼                   ▼                   ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│ WORKER          │ │ WORKER          │ │ VERIFIER        │
│ cheap model     │ │ cheap model     │ │ cheap model     │
│                 │ │                 │ │                 │
│ L1: pack        │ │ L1: pack        │ │ diff + pytest   │
│ L3: ponytail    │ │ L3: ponytail    │ │ JSON only       │
│ rules in prompt │ │ rules in prompt │ │                 │
│                 │ │                 │ │                 │
│ L1 fallback:    │ │                 │ │                 │
│ expand_context  │ │                 │ │                 │
└─────────────────┘ └─────────────────┘ └─────────────────┘
```

### Model routing policy (implement in `orchestrator.py` + CLI)

```yaml
roles:
  manager:
    default_model: null  # external / human strong model for review
    context_max_tokens: 8000
    allowed_tools: [read, diff, benchmark, orchestrate_review]
  worker:
    default_model: gpt-5.4-mini  # or composer / haiku — configurable
    context_max_tokens: 6000     # pack handoff only
    allowed_tools: [edit allowed_paths, scoped pytest, handoff_json]
  verifier:
    default_model: gpt-4o-mini
    context_max_tokens: 4000
    allowed_tools: [pytest, compare --summary]
```

Workers **must** receive context via `agenvantage pack --preset feature`, never
full-repo paste.

---

## 4. Layer Specifications

### L1 — Input confidence (AgenVantage)

**Goal:** Keep ~93% token reduction while raising grounding confidence.

| ID | Feature | Implementation |
|----|---------|----------------|
| L1.1 | **Guard-test reservation** | When change surface finds a test matching `import`, `security`, `contract`, or `guard` patterns, reserve it in mandatory chunks before optional neighbors (Click `test_light_imports` lesson). |
| L1.2 | **Adaptive budget on partial grounding** | If `context_plan.grounding.status == "partial"`, raise effective budget from 1800 → 4000 automatically (extend `cold_start_sufficiency` logic). |
| L1.3 | **MCP `expand_context`** | New tool: given `trace_id` or `manifest_path` + `reason`, return next-ranked chunks within `--expand-budget` (default 1500 tokens). Max 3 rounds per session. |
| L1.4 | **Handoff escalation block** | When `missing_signals` non-empty, append explicit instruction: "Call expand_context before implementing; do not guess." |
| L1.5 | **Pruning benchmark gate** | Run feature-work validation after pruning tuning; no recall regression > 3pp. |

**Acceptance (L1):**
- `cold_start_sufficiency` benchmark: 2/2 ready on adaptive path
- `feature_work_validation`: edit-target ≥ 0.95, test-target = 1.0, readiness ≥ 0.85
- `test_mcp_server.py`: `expand_context` returns valid chunks, respects budget
- Handoff with `missing_signals` includes escalation text

### L2 — Process routing (orchestrator + traces)

**Goal:** Strong model never implements; cheap model never architects.

| ID | Feature | Implementation |
|----|---------|----------------|
| L2.1 | **`--worker-model` on orchestrate run** | Pass through to `agent_launcher.resolve_agent_command(agent_model=...)`. |
| L2.2 | **`--worker-provider` flag** | `codex` (default) or `claude`. |
| L2.3 | **Real worker dispatch** | Replace placeholder `sh -c printf` in `build_worker_agent_command` with `agenvantage agent run --handoff-file ... -- <codex exec>`. |
| L2.4 | **Role config file** | `examples/agent_roles.json` — models per role; CLI reads it. |
| L2.5 | **Cost ledger on traces** | Add `manager_input_tokens`, `worker_input_tokens`, `verifier_input_tokens`, `role`, `model` fields to observability spans. |
| L2.6 | **Orchestrate `verify` subcommand** | Run scoped pytest + emit verifier JSON. |

**Acceptance (L2):**
- `agenvantage orchestrate run --package W2.1 --worker-model gpt-4o-mini --execute` produces real dispatch command with model flag
- `tests/test_orchestrator.py` covers model routing and verify
- Trace records role + model when `observe pack` / `agent run` used

### L3 — Output discipline (Ponytail-inspired)

**Goal:** Reduce output/diff tokens without dropping guards. Inspired by Ponytail's
ladder, not a fork of Ponytail.

**Implementation ladder** (add to `_FEATURE` preset in `presets.py` and bundled
skill `src/agenvantage/resources/agenvantage-context/SKILL.md`):

```text
IMPLEMENTATION LADDER (apply after reading provided context):
1. Does this need to exist?           → if no, do not add it (YAGNI)
2. Already in selected chunks?        → reuse; do not rewrite
3. Standard library covers it?        → use stdlib
4. Native platform / framework API?   → prefer native over new dependency
5. Existing installed dependency?     → use it
6. One line sufficient?               → write one line
7. Only then: minimum code that passes listed tests

NEVER simplify away at trust boundaries:
- input validation, auth, path traversal checks
- error handling on data loss paths
- security-sensitive parsing
- accessibility requirements when UI is touched

When selected chunks include a guard/contract test, run it before declaring done.
```

| ID | Feature | Implementation |
|----|---------|----------------|
| L3.1 | **Feature preset ladder** | Extend `presets.py` `_FEATURE.instructions` |
| L3.2 | **Skill sync** | Update bundled skill; run existing copy/sync if present |
| L3.3 | **`--discipline full|lite|off` flag** | Optional pack CLI flag; injects ladder variants |
| L3.4 | **Handoff `implementation_discipline` field** | JSON manifest records active discipline level |

**Acceptance (L3):**
- `agenvantage pack --preset feature --task "..." --json` includes `implementation_discipline`
- `tests/test_presets.py` asserts ladder text present
- No change to retrieval benchmarks beyond documented variance

### L4 — Session + compaction (if time remains)

| ID | Feature | Implementation |
|----|---------|----------------|
| L4.1 | **Tool-result compaction helper** | `src/agenvantage/compaction.py`: truncate grep/read output, keep paths + line numbers + error lines |
| L4.2 | **Wire into agent run spans** | Store compacted stderr/stdout summaries on `agent.external` span |
| L4.3 | **Session prefix diagnostics** | Warn in `session task` when stable prefix drifts > 5% bytes |

**Acceptance (L4):**
- Unit tests for compaction preserve paths and line numbers
- Optional; skip if Waves 1–3 incomplete

---

## 5. Work Packages (overnight execution order)

Update `examples/agent_development_partition.json` with these packages.
Execute in wave order. **Stop and fix tests** if any wave fails pytest.

### Wave 1 — L3 Output discipline + L1 guard reservation (highest user-visible value)

| Package | Files | Task | Acceptance |
|---------|-------|------|------------|
| **T1.1** | `src/agenvantage/presets.py`, `tests/test_presets.py` | Add Ponytail ladder to feature preset; add lite/off variants | Preset tests pass; ladder in `--json` output |
| **T1.2** | `src/agenvantage/resources/agenvantage-context/SKILL.md`, `src/agenvantage/agent_integrations.py` | Sync skill with ladder + "expand if missing_signals" | Skill install tests pass |
| **T1.3** | `src/agenvantage/repo_context.py`, `tests/test_repo_context.py` | Guard-test reservation in `_select_feature_reserved_chunks` | New test: guard test reserved when pattern matches |
| **T1.4** | `src/agenvantage/cli.py` | Add `--discipline` flag to pack command | CLI test passes |

### Wave 2 — L1 confidence (expand + adaptive budget)

| Package | Files | Task | Acceptance |
|---------|-------|------|------------|
| **T2.1** | `src/agenvantage/mcp_server.py`, `tests/test_mcp_server.py` | Implement `expand_context` MCP tool | Integration test; budget respected |
| **T2.2** | `src/agenvantage/repo_context.py`, `src/agenvantage/context_planner.py` | Adaptive budget on partial grounding; handoff escalation block | `cold_start_sufficiency` tests pass |
| **T2.3** | `benchmarks/feature_work_validation.py` | Re-run summary; record in `docs/three-layer-stack-results.md` | No recall regression > 3pp |

### Wave 3 — L2 Model routing (orchestrator)

| Package | Files | Task | Acceptance |
|---------|-------|------|------------|
| **T3.1** | `examples/agent_roles.json` (new) | Role → model mapping config | Valid JSON schema |
| **T3.2** | `src/agenvantage/orchestrator.py`, `tests/test_orchestrator.py` | Real `agent run` dispatch; `--worker-model`, `--worker-provider` | Orchestrator tests pass |
| **T3.3** | `src/agenvantage/cli.py` | Wire CLI flags to orchestrate run | `--help` documents flags |
| **T3.4** | `src/agenvantage/observability.py`, `tests/test_observability.py` | Cost ledger fields: role, model, token totals | Dashboard/trace JSON includes fields |

### Wave 4 — Integration docs + smoke validation

| Package | Files | Task | Acceptance |
|---------|-------|------|------------|
| **T4.1** | `docs/three-layer-stack-results.md` (new) | Summarize shipped features + benchmark before/after | Human-readable |
| **T4.2** | `README.md` | Add "Three-Layer Stack" section with commands | Copy-paste examples |
| **T4.3** | `examples/agent_development_partition.json` | Register T1–T4 packages | `orchestrate plan` succeeds |
| **T4.4** | `benchmarks/three_layer_smoke.py` (new) | Smoke: pack with discipline + expand_context dry path + orchestrate plan | Script exits 0 |

### Wave 5 — Optional (only if Waves 1–4 complete early)

| Package | Files | Task |
|---------|-------|------|
| T5.1 | `src/agenvantage/compaction.py` | Tool-result compaction |
| T5.2 | `src/agenvantage/session.py` | Prefix drift diagnostics |
| T5.3 | `benchmarks/paired_codex_live.sh` | `compare --live` wrapper |

---

## 6. Worker Handoff Contract

Every worker package must end with this JSON at
`.agenvantage/orchestration/packages/<package_id>/worker-response.json`:

```json
{
  "package_id": "T1.1",
  "status": "completed",
  "files_changed": ["src/agenvantage/presets.py"],
  "tests_run": ".venv/bin/pytest tests/test_presets.py -q",
  "tests_passed": true,
  "benchmark_delta": null,
  "risks": [],
  "manager_decision_needed": []
}
```

Manager review (`agenvantage orchestrate review --package T1.1`) must return
`accepted` with no findings.

---

## 7. Testing Protocol (mandatory after each wave)

```bash
cd <repository-root>
source .venv/bin/activate
.venv/bin/pytest -q
.venv/bin/python benchmarks/feature_work_validation.py --summary
.venv/bin/python benchmarks/cold_start_sufficiency.py --summary 2>/dev/null || true
```

**Regression gates (do not merge wave if failed):**

| Benchmark | Gate |
|-----------|------|
| pytest | 237+ tests pass (may increase, not decrease) |
| edit-target recall | ≥ 0.95 |
| test-target recall | = 1.0 |
| context-plan readiness | ≥ 0.85 |
| cold-start ready cases | 2/2 |

---

## 8. Claim Boundaries (do not overclaim in docs)

**May claim after this PRD:**
- AgenVantage combines input-budgeted packing, Ponytail-inspired output discipline,
  and role-based model routing in one local workflow
- `expand_context` enables just-in-time retrieval when initial pack reports
  missing signals
- Orchestrator dispatches bounded worker tasks with configurable cheap models

**May NOT claim without new live artifacts:**
- Provider-billed cost savings
- Production latency improvements
- Broad quality retention across workloads
- Ponytail-equivalent LOC reduction (we adopt the ladder, not their benchmark)

---

## 9. Codex Operating Rules

1. **Read before edit:** `git status`, relevant test file, and target module.
2. **One package per commit** when possible; message format:
   `feat(T1.1): add implementation ladder to feature preset`
3. **No drive-by refactors** in `cli.py` beyond required wiring.
4. **Prefer extending** `orchestrator.py`, `presets.py`, `mcp_server.py` over new files.
5. If blocked > 30 minutes on one package, document blocker in
   `docs/three-layer-stack-results.md` and proceed to next package.
6. **Final deliverable:** `docs/three-layer-stack-results.md` with:
   - packages completed / skipped
   - pytest count
   - feature-work benchmark numbers
   - example commands for the full stack
   - known gaps

---

## 10. Example End-to-End Commands (target UX after build)

```bash
# L1: Pack with input context + output discipline
agenvantage pack --preset feature --discipline full \
  --task "Add guard-test reservation for import contract tests" \
  --handoff-json --output .agenvantage/handoff.md

# L1 fallback: expand if missing signals (via MCP)
agenvantage mcp   # client calls expand_context with manifest path

# L2: Manager plans overnight work
agenvantage orchestrate plan --repo . \
  --partition examples/agent_development_partition.json

# L2: Dispatch cheap worker with pack handoff
agenvantage orchestrate run --package T1.1 --execute \
  --worker-provider codex --worker-model gpt-5.4-mini

# L2: Manager reviews (strong model reads diff only)
agenvantage orchestrate review --package T1.1

# L2: Verifier runs tests
agenvantage orchestrate verify --package T1.1

# Observe full trajectory
agenvantage observe pack --task "Three-layer stack smoke test"
agenvantage dashboard
```

---

## 11. Success Criteria (overnight complete)

| Criterion | Target |
|-----------|--------|
| Waves 1–3 shipped | All T1.*, T2.*, T3.* packages accepted |
| pytest | Green, count ≥ 237 |
| feature-work benchmark | Passes acceptance gates |
| `expand_context` | Working MCP tool with tests |
| `--discipline full` | In feature preset + CLI |
| `--worker-model` | On orchestrate run |
| `docs/three-layer-stack-results.md` | Written with evidence |
| README updated | Three-layer section present |

---

## 12. References

- Ponytail agentic benchmark: https://github.com/DietrichGebert/ponytail/blob/main/benchmarks/results/2026-06-18-agentic.md
- AgenVantage paired validation: `docs/paired-feature-implementation-validation.md`
- Hierarchical dev PRD: `docs/prd-hierarchical-agent-development.md`
- Claim status: `docs/claim-status.md`
- Partition fixture: `examples/agent_development_partition.json`
