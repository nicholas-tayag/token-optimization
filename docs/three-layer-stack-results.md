# Three-Layer Agent Cost Stack Results

## Shipped

- **Input:** adaptive feature packing, mandatory guard-test reservation, and a
  bounded MCP `expand_context` recovery path with three rounds maximum.
- **Process:** configurable Codex/Claude worker routing, role defaults in
  `examples/agent_roles.json`, package verification, and role/model token ledgers.
- **Output:** `full`, `lite`, and `off` implementation discipline levels, with
  the full minimum-code ladder enabled for feature handoffs by default.

## Package Status

| Wave | Packages | Status |
| --- | --- | --- |
| T1 | T1.1-T1.4 | Completed |
| T2 | T2.1-T2.3 | Completed |
| T3 | T3.1-T3.4 | Completed |
| T4 | T4.1-T4.4 | Completed |
| T5 | T5.1-T5.3 | Skipped (optional) |

## Validation

| Metric | Before | After |
| --- | ---: | ---: |
| Pytest | 242 passed | 250 passed |
| Edit-target recall | 0.9583 | 0.9583 |
| Test-target recall | 1.0000 | 1.0000 |
| Required-observation recall | 0.9722 | 0.9444 |
| Context-plan readiness | 0.9167 | 0.9167 |
| Median additive-corpus reduction | 93.22% | 93.23% |
| Median packed prompt | 3,919 tokens | 3,913 tokens |
| Cold-start ready cases | 2/2 | 2/2 |

Required-observation recall moved by 2.78 percentage points, within the PRD's
3-point regression gate. The benchmark still passes every configured acceptance
threshold. Token reduction compares independently tokenized eligible repository
blocks with the packed prompt; it is not provider billing data.

## Commands

```bash
agenvantage pack --preset feature --discipline full --task "Add a feature" --handoff-json
agenvantage mcp
agenvantage orchestrate run --package T1.1 --worker-provider codex --worker-model gpt-5.4-mini
agenvantage orchestrate verify --package T1.1 --json
.venv/bin/python benchmarks/three_layer_smoke.py
```

## Known Gaps

- Role routing is implemented locally but has not been validated across a broad
  live provider workload.
- The role ledger records local input-equivalent tokens; provider output,
  cached-input, and billed cost require imported or live provider usage.
- Output discipline adopts Ponytail-inspired rules but does not establish
  Ponytail-equivalent LOC or session-token reductions.
- No production latency, provider-billed savings, or broad quality-retention
  claim is supported by this build.
