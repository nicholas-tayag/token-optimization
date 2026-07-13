# Graphify Integration and Ablation

Date: July 12, 2026  
Graphify: `graphifyy==0.9.13`  
Upstream commit: `eec7a0183847cbdc8a87d92b233759a5204b89fe`

## Integration

Graphify is an optional, disabled-by-default repository graph candidate backend.
AgenVantage invokes the pinned CLI out of process in code-only mode or consumes
an explicitly supplied `graph.json`. The core package does not import Graphify,
NetworkX, NumPy, or language grammars.

The adapter:

- validates Graphify version, graph size, repository revision, source-path
  containment, confidence labels, and line anchors;
- preserves semantic edge direction even though Graphify's NetworkX container
  is marked `directed: false`;
- seeds exact task paths and symbols and traverses at most two hops;
- ranks `EXTRACTED` edges above `INFERRED` and `AMBIGUOUS` edges;
- resolves every eligible candidate back to current repository source;
- uses graph candidates for supporting evidence without displacing the
  baseline lexical/structural edit and test targets; and
- falls back to normal packing when Graphify is absent, stale, incompatible,
  malformed, oversized, or times out.

Example:

```bash
agenvantage pack \
  --repo /path/to/repo \
  --preset feature \
  --task "Trace validation across the route and handler" \
  --graph-backend graphify \
  --graph-json /path/to/graphify-out/graph.json
```

Omit `--graph-json` to let AgenVantage invoke the pinned `graphify` executable
and cache its code-only graph outside the repository.

## Deterministic Retrieval Ablation

The paired ablation used ten annotated cross-file tasks across frozen revisions
of Click and FastAPI. Each baseline and treatment shared the same task, budget,
`top_k`, tokenizer, source revision, and evidence annotations.

Reproduce after materializing the pinned repositories and graphs:

```bash
.venv/bin/python benchmarks/graphify_ablation.py \
  --fixture examples/graphify_ablation_cases.json \
  --repos-root external/graphify-eval \
  --output-json artifacts/graphify-ablation.json \
  --output-md artifacts/graphify-ablation.md \
  --summary
```

Measured result:

- evidence-region recall: `20.51%` baseline and `20.51%` Graphify (`0.00`
  percentage-point change);
- missing-context expansions: `21` baseline and `13` Graphify (`38.10%`
  reduction);
- edit-target recall: `77.27%` in both variants;
- test-target recall: `64.29%` in both variants;
- median packed prompt: `3,935` baseline and `3,921` Graphify tokens;
- median pack runtime: `17,282.20 ms` baseline and `18,269.39 ms` Graphify;
- median graph-backend time: `679.69 ms`; and
- Graphify was used in all ten treatment cases.

The compact checked result is
`examples/graphify_ablation_results.json`; the full generated report remains in
the ignored `artifacts/` directory.

This passes the predeclared deterministic gate because missing-context
expansions fell by at least `25%` with no edit/test recall regression. It does
not demonstrate better region recall. The low absolute region recall in both
variants also means the current budgets and planner still omit much of the
annotated evidence on these larger repositories.

## Task-Level Gate

The next gate required six clean baseline-versus-Graphify coding-agent
implementation pairs with equal model, prompts, tools, and acceptance tests.
The authenticated Codex CLI preflight failed before any implementation request
with `runner_usage_limit`. Therefore:

- paired implementations executed: `0 / 6`;
- task-success preservation: not evaluated;
- input-equivalent token and recovery-turn deltas: not evaluated; and
- no task-level Graphify usefulness claim is supported.

The blocked status is preserved in
`examples/paired_feature_implementation_results.json`. Retry this layer only
after runner quota is available.

## Verdict

Graphify is useful enough to keep as an optional supporting-evidence backend:
it reduced deterministic missing-context expansion signals while preserving the
baseline edit/test surface. It should remain disabled by default because it did
not improve region recall, adds roughly one second to median pack latency, and
has not yet passed the required coding-agent task-success gate.
