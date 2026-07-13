# Paired Feature Implementation Validation

Date: July 12, 2026  
Cases: 2  
Runner: Codex CLI, `gpt-5.4-mini`, ephemeral sessions, user configuration and
project rules disabled

## Method

Each case used two clean checkouts at the same pinned commit and the same
feature prompt, model, tool access, and completion requirement.

- Control: normal repository exploration.
- Treatment: the same agent workflow with an AgenVantage feature handoff as
  initial context.
- Success: independently authored acceptance checks and the repository's full
  test/lint/type suite pass.
- Usage: token counts from the runner's `turn.completed` event.
- Quality: independent senior-agent review after tests completed.

The Click control had to be repeated to capture durable usage events. It used
the same commit, prompt, model, and execution settings, but was not concurrent
with the recorded treatment. The p-limit pair ran concurrently.

Machine-readable results are in
[`examples/paired_feature_implementation_results.json`](../examples/paired_feature_implementation_results.json).

## Results

| Case | Variant | Input | Cached input | Uncached input | Output | Reasoning | External acceptance | Full suite | Success |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| Click `--help-json` | Control | 2,617,815 | 2,537,600 | 80,215 | 22,513 | 15,407 | 5/5 | 1,903 passed | yes |
| Click `--help-json` | AgenVantage | 1,393,951 | 1,337,216 | 56,735 | 18,553 | 11,427 | 5/5 | 1,906 passed | yes |
| p-limit `onIdle()` | Control | 1,028,249 | 993,920 | 34,329 | 16,506 | 10,562 | passed | 25 passed | yes |
| p-limit `onIdle()` | AgenVantage | 772,131 | 717,824 | 54,307 | 13,105 | 8,522 | passed | 23 passed | yes |

### Click

AgenVantage initial handoff:

- Full eligible-corpus counterfactual: 326,827 tokens.
- Packed handoff: 3,807 tokens.
- Selected chunks: 9 of 856.

Trajectory deltas versus control:

- Input tokens: `-46.75%`.
- Cached input tokens: `-47.30%`.
- Uncached input tokens: `-29.27%`.
- Output tokens: `-17.59%`.
- Reasoning output tokens: `-25.83%`.
- Wall time: `-20.43%` (`377.89s` to `300.68s`).

The first treatment passed all five external feature checks but failed Click's
repository-wide `test_light_imports` contract because it imported Python's
`json` module eagerly. AgenVantage was updated to reserve the discovered guard
test and full-suite command inside the actual handoff. A clean rerun used a
lazy import, passed all external checks, and passed the full suite. The failed
initial treatment remains preserved in the machine-readable artifact rather
than being discarded.

### p-limit

AgenVantage initial handoff:

- Full eligible-corpus counterfactual: 13,225 tokens.
- Packed handoff: 4,000 tokens.
- Selected chunks: 10 of 39.

Trajectory deltas versus control:

- Input tokens: `-24.91%`.
- Cached input tokens: `-27.78%`.
- Uncached input tokens: `+58.20%`.
- Output tokens: `-20.60%`.
- Reasoning output tokens: `-19.31%`.
- Wall time: `-13.64%` (`356.91s` to `308.24s`).

Both implementations passed the independently authored lifecycle program and
the full repository lint, AVA, and TypeScript declaration suite. Independent
review scored control `9.5/10` and treatment `9.1/10`. The treatment had fewer
repository tests around `clearQueue` and newly queued work, although the
external checks covered both behaviors.

## Aggregate

| Metric | Control | AgenVantage | Treatment change |
| --- | ---: | ---: | ---: |
| Successful tasks | 2/2 | 2/2 | no change |
| Input tokens | 3,646,064 | 2,166,082 | -40.59% |
| Cached input tokens | 3,531,520 | 2,055,040 | -41.81% |
| Uncached input tokens | 114,544 | 111,042 | -3.06% |
| Output tokens | 39,019 | 31,658 | -18.87% |
| Reasoning output tokens | 25,969 | 19,949 | -23.18% |
| Wall time | 734.80s | 608.92s | -17.13% |
| Input tokens per successful task | 1,823,032 | 1,083,041 | -40.59% |

The two final handoffs totaled 7,807 tokens, only `0.36%` of treatment input
across the full trajectories. Initial prompt compression was not a reliable
proxy for end-to-end agent context consumption.

## Interpretation

The final treatments completed both features and passed their repository-wide
validation. Across this two-case sample, AgenVantage used `40.59%` fewer input
tokens per successful task and finished `17.13%` faster. The initial Click
failure is evidence that the result depended on adding repository-wide guard
evidence, not merely compressing the initial prompt.

This is promising task-level evidence, but it does not establish a general
effect because the sample contains only two tasks and one model.

Required next evidence:

- expand to frozen tasks across more repositories and languages; and
- report paired confidence intervals only after the sample is large enough.

## Claim Boundary

These two cases do not prove general token savings, provider-billed cost
savings, production latency improvement, or broad task-quality retention.
They are direct task-level evidence that initial handoff size alone is
insufficient and that quality gates must be included in the optimization
objective.
