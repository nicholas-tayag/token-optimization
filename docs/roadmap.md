# AgenVantage Roadmap

## Project Narrative

AgenVantage is a personal token-usage investigation that grows only when an
experiment justifies additional infrastructure:

1. Inspect the context attached to agent requests.
2. Compare context assembly policies under token budgets.
3. Run real model requests and record provider-reported usage and latency.
4. Evaluate whether token-saving policies preserve correct and safe behavior.
5. Export standards-aligned traces for production-style analysis.

## Milestone 0: Local Context Measurement

Status: implemented in the initial repository scaffold.

- Define context components with stability, requirement, priority, and
  relevance metadata.
- Compare full, cache-aligned, and budgeted packages.
- Record local token measurements and stable-prefix size.
- Emit optional OpenTelemetry context-assembly spans.
- Use only synthetic incident-response context.

Exit evidence:

- tests prove required context is retained under budgeting;
- a JSON experiment report compares policies without invented quality claims.

## Milestone 1: Provider-Reported Usage

- Add one provider adapter, initially selected based on available personal
  access and documentation quality.
- Record actual input, output, and cached input token metadata where exposed.
- Run repeated-prefix experiments against fixed synthetic cases.
- Capture request latency and calculate cost only from published/provider
  metadata recorded alongside each experiment.

Exit evidence:

- reproducible experiment command and raw output artifacts;
- measured cache-hit and latency comparison, or a documented negative result.

## Milestone 2: Context Regression Evaluations

- Add a small golden synthetic dataset for incident-response tasks.
- Evaluate evidence citation, required safety instruction adherence, and
  recommended tool/action correctness.
- Gate token reduction against a quality and safety tolerance.

Success rule:

```text
token_or_cost_reduction > 0
AND safety_pass_rate >= baseline_safety_pass_rate
AND overall_pass_rate >= baseline_overall_pass_rate - declared_tolerance
```

## Milestone 3: Observable Agent Workflow

- Emit documented OpenTelemetry GenAI spans for real inference operations.
- Add MCP/tool-call tracing only when a tool workflow exists.
- Export telemetry to a local collector and optionally Datadog if access and
  cost make sense for a documented demonstration.
- Build a minimal report or dashboard from real experiment telemetry.

## Resume Rule

No resume bullet should claim token reduction, cost savings, latency
improvement, quality retention, or Datadog integration until the corresponding
experiment has been run and its artifacts are committed or reproducible.

