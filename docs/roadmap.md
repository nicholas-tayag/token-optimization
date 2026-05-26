# AgenVantage Roadmap

## Project Narrative

AgenVantage begins as a developer tool for my own AI-assisted coding work:

1. Ask a coding question about one of my local public repositories.
2. Select useful files and chunks under a measured token budget.
3. Inspect the package before sending it to a model.
4. Compare API responses and provider-reported token usage only after the
   local planner is useful on real tasks.
5. Add broader governance or observability integrations only when experiments
   justify them.

## Milestone 0: Local Repository Context Packaging

Status: implemented.

- Scan eligible tracked source and documentation files in a selected local
  repository while excluding dependency folders and `.env` content.
- Split source files into line-addressable chunks and rank them against a
  coding question.
- Assemble a Markdown package and JSON decision manifest under a token budget.
- Record local candidate-context reduction, without describing it as billed
  API savings.
- Preserve the original synthetic policy comparison as a controlled secondary
  fixture.

Exit evidence:

- tests cover repository selection and policy behavior;
- real runs package relevant context from a public personal repository.

## Milestone 1: Provider-Reported Usage

- Add an OpenAI Responses API adapter for selected repository-context tasks.
- Record actual input, output, and cached input token metadata where exposed.
- Expand stable instructions/context to satisfy caching eligibility and run
  repeated-prefix experiments against fixed coding tasks.
- Capture request latency and calculate cost only from published/provider
  metadata recorded alongside each experiment.

Exit evidence:

- reproducible experiment command and raw output artifacts;
- measured cache-hit and latency comparison, or a documented negative result.

## Milestone 2: Coding Task Evaluations

- Add a human-authored set of tasks drawn from completed public repository
  behavior: locating an implementation, explaining a known test case,
  identifying changed behavior, and recommending edge-case tests.
- Evaluate expected file citations, required behavioral observations, and
  unsupported-claim avoidance.
- Gate input-cost reduction against a declared answer-correctness tolerance.

Success rule:

```text
token_or_cost_reduction > 0
AND unsupported_claim_pass_rate >= baseline_unsupported_claim_pass_rate
AND correctness_pass_rate >= baseline_correctness_pass_rate - declared_tolerance
```

## Milestone 3: Observable Workflow And Later Extensions

- Emit documented OpenTelemetry GenAI spans for real inference operations.
- Add MCP/tool selection or sensitive-context policy rules only when the local
  coding workflow provides a genuine need for them.
- Export telemetry to a local collector and optionally Datadog if access and
  cost make sense for a documented demonstration.
- Build a minimal report or dashboard from real experiment telemetry.

## Resume Rule

No resume bullet should claim API token reduction, cost savings, latency
improvement, quality retention, enterprise controls, or Datadog integration
until the corresponding experiment has been run and its artifacts are
committed or reproducible.
