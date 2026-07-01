# AgenVantage Claim Status

## Current Supported Claim

- Supported: `True`
- Statement: AgenVantage reduces local candidate repository context while preserving grounding for many coding-task benchmarks.
- Reason: The current artifact covers 7 hand-authored repository tasks with 96.23% weighted candidate-context reduction, 1.0 grounding sufficiency pass rate, and 0.86 answer-rubric pass rate.

## Current Evidence

- Use-case cases: `7`
- Weighted candidate-context reduction: `96.23%`
- Grounding sufficiency pass rate: `1.0`
- Answer-rubric pass rate: `0.86`
- Provider fixture scope: `synthetic_local`
- Provider fixture cases: `6`
- Cache-ready stable prefix: `1066` tokens
- Average budgeted reduction vs full: `10.57%`
- No live provider-validation artifact found.

## Resume-Risk Claims

| Claim | Supported | Reason |
| --- | --- | --- |
| `solved_agent_context_overload_end_to_end` | `False` | No end-to-end provider-backed artifact exists yet. |
| `proved_real_api_cost_savings` | `False` | No measured provider-cost artifact is present yet. |
| `proved_latency_improvements_in_production` | `False` | No production-scoped latency artifact is present yet. |
| `proved_downstream_model_answer_quality_retention_across_broad_workloads` | `False` | No broad provider-backed answer-quality artifact is present yet. |

## What To Run Next

- Record provider-backed cost and latency results for the compared policies.
- Run a broad answer-quality evaluation with enough distinct tasks to satisfy the declared tolerance.
- Keep the result production-scoped if you want the strongest end-to-end claim.
- Run validate-provider live with a real pricing snapshot and saved records.
- Show lower mean request cost for budgeted_cache_aligned than full_unaligned.
- Collect latency measurements from a production-scoped workload or telemetry source.
- Show a better p50 than full_unaligned with enough production requests.
- Run provider validation across at least 30 distinct cases and 6 or more failure types.
- Show no material regression in correctness, safety, and grounded citation pass rates.

## Resource Pack

- See [proof-resource-pack.md](./proof-resource-pack.md) for the external docs,
  papers, and benchmark repositories that most directly unblock these four
  unsupported claims.
