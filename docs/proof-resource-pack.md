# AgenVantage Proof Resource Pack

This document centralizes the highest-value external resources needed to turn
the current `False` claim statuses into measured, defensible results.

It is organized around the four unsupported claims in
[claim-status.md](./claim-status.md) and prioritizes resources that directly
unblock implementation, evaluation, or telemetry work.

## How To Use This Pack

- Treat `P0` resources as immediate implementation inputs.
- Treat `P1` resources as benchmark expansion inputs for stronger evidence.
- Treat `P2` resources as later validation or comparison inputs.
- Prefer official docs and primary papers when translating claims into code or
  README language.

## Claim 1: "Solved agent context overload end-to-end"

### P0

1. OpenTelemetry Demo docs
   Link: [opentelemetry.io/docs/demo](https://opentelemetry.io/docs/demo/)
   Why it matters: provides public, realistic incident scenarios and telemetry
   surfaces for end-to-end agent-style evaluation instead of synthetic toy
   prompts.
   Suggested repo task: expand the provider-validation dataset from the current
   6 synthetic incidents to 30+ public scenarios grounded in the demo.

2. OpenTelemetry Demo feature flags
   Link: [opentelemetry.io/docs/demo/feature-flags](https://opentelemetry.io/docs/demo/feature-flags/)
   Why it matters: defines controllable failure modes that can generate labeled
   scenarios for reproducible end-to-end runs.
   Suggested repo task: map each feature flag to a case template and expected
   failure label in the provider-validation fixture.

3. OpenTelemetry demo repository
   Link: [github.com/open-telemetry/opentelemetry-demo](https://github.com/open-telemetry/opentelemetry-demo)
   Why it matters: provides the runnable public system behind the demo docs and
   a near-real-world environment for tracing and latency experiments.
   Suggested repo task: add a setup guide or helper scripts for running the demo
   locally as the reference workload.

4. RepoBench paper
   Link: [ICLR 2024 RepoBench PDF](https://proceedings.iclr.cc/paper_files/paper/2024/file/d191ba4c8923ed8fd8935b7c98658b5f-Paper-Conference.pdf)
   Why it matters: separates retrieval, completion, and full pipeline
   evaluation, which matches AgenVantage's need to distinguish context
   selection from end-to-end task success.
   Suggested repo task: mirror this separation in AgenVantage reports:
   retrieval-only, answer-quality, and end-to-end pipeline metrics.

5. SWE-bench
   Link: [github.com/swe-bench/SWE-bench](https://github.com/swe-bench/SWE-bench)
   Why it matters: gives a widely recognized end-to-end software engineering
   benchmark with reproducible execution-based evaluation.
   Suggested repo task: add a "context packaging before patch generation"
   experiment on a narrow SWE-bench slice.

### P1

6. SWE-bench Verified
   Link: [swebench.com/verified.html](https://www.swebench.com/verified.html)
   Why it matters: offers a higher-quality, human-filtered subset for stronger
   portfolio-quality claims.
   Suggested repo task: use Verified before claiming broad end-to-end agent
   wins.

7. RepoQA paper
   Link: [arXiv RepoQA](https://arxiv.org/html/2406.06025v1)
   Why it matters: benchmarks long-context code understanding and can strengthen
   claims about finding the right repository evidence before generation.
   Suggested repo task: add a retrieval-only benchmark mode modeled on RepoQA.

8. Long Code Arena
   Link: [arXiv Long Code Arena](https://arxiv.org/abs/2406.11612)
   Why it matters: covers project-wide tasks like CI repair, bug localization,
   and module summarization, which are broader than the current local
   explanation-oriented benchmark.
   Suggested repo task: select one or two tasks that fit AgenVantage's context
   packaging scope and add them as downstream evaluations.

9. Long Code Arena baselines
   Link: [github.com/JetBrains-Research/lca-baselines](https://github.com/JetBrains-Research/lca-baselines)
   Why it matters: includes open-source baseline code for benchmark execution.
   Suggested repo task: use baseline runners as a comparison harness instead of
   creating a custom benchmark loop from scratch.

## Claim 2: "Proved real API cost savings"

### P0

1. OpenAI prompt caching guide
   Link: [developers.openai.com/api/docs/guides/prompt-caching](https://developers.openai.com/api/docs/guides/prompt-caching)
   Why it matters: defines the official cache behavior, minimum token threshold,
   and the measured claim boundary for cached-token savings.
   Suggested repo task: keep AgenVantage provider fixtures above the cache
   threshold and record cached-token usage from live runs.

2. OpenAI pricing page
   Link: [developers.openai.com/api/docs/pricing](https://developers.openai.com/api/docs/pricing)
   Why it matters: provides the pricing snapshot needed to compute actual input,
   cached input, and output cost from live runs.
   Suggested repo task: replace the template pricing file with a dated snapshot
   used by each experiment.

3. OpenAI cost optimization guide
   Link: [developers.openai.com/api/docs/guides/cost-optimization](https://developers.openai.com/api/docs/guides/cost-optimization)
   Why it matters: ties token reduction to cost and latency strategy at the API
   level.
   Suggested repo task: explicitly report whether AgenVantage reduces requests,
   tokens, or both.

4. OpenAI Prompt Caching 201 cookbook
   Link: [developers.openai.com/cookbook/examples/prompt_caching_201](https://developers.openai.com/cookbook/examples/prompt_caching_201)
   Why it matters: gives practical cache-hit optimization patterns beyond the
   surface docs.
   Suggested repo task: compare fixture ordering and prompt layout against these
   patterns before spending on live runs.

5. OpenAI Costs API reference
   Link: [developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/costs](https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/costs/)
   Why it matters: provides an authoritative way to reconcile experiment-level
   estimates with organization-level recorded costs.
   Suggested repo task: add a replay or reconciliation mode for recorded live
   experiments if admin credentials are available.

### P1

6. OpenAI usage details with cached token fields
   Link: [developers.openai.com/api/reference/python/resources/batches/methods/list](https://developers.openai.com/api/reference/python/resources/batches/methods/list/)
   Why it matters: confirms the presence of `cached_tokens` style usage details
   in API responses and related resources.
   Suggested repo task: harden parsing and tests around `cached_tokens`
   extraction.

7. Responses API migration guide
   Link: [developers.openai.com/api/docs/guides/migrate-to-responses](https://developers.openai.com/api/docs/guides/migrate-to-responses)
   Why it matters: clarifies billing behavior when chaining requests.
   Suggested repo task: avoid over-claiming savings from request chaining that
   still bills prior input tokens.

## Claim 3: "Proved latency improvements in production"

### P0

1. OpenAI latency optimization guide
   Link: [developers.openai.com/api/docs/guides/latency-optimization](https://developers.openai.com/api/docs/guides/latency-optimization)
   Why it matters: defines the official techniques and vocabulary for latency
   improvement claims.
   Suggested repo task: frame AgenVantage latency claims as measured request
   latency changes, not generic "faster AI."

2. OpenAI production best practices
   Link: [developers.openai.com/api/docs/guides/production-best-practices](https://developers.openai.com/api/docs/guides/production-best-practices)
   Why it matters: sets the production-quality bar for security, scaling, and
   environment handling.
   Suggested repo task: separate synthetic local experiments from any
   production-scoped telemetry or deployment.

3. OpenTelemetry GenAI registry attributes
   Link: [opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)
   Why it matters: defines portable telemetry fields for input tokens, cache
   reads, and related usage metrics.
   Suggested repo task: emit these fields when live provider calls are
   instrumented.

4. OpenTelemetry GenAI semantic conventions repository
   Link: [github.com/open-telemetry/semantic-conventions-genai](https://github.com/open-telemetry/semantic-conventions-genai)
   Why it matters: tracks the evolving GenAI telemetry spec that production
   instrumentation should follow.
   Suggested repo task: pin the semantic-convention version in experiment
   reports.

### P1

5. OpenTelemetry demo repository
   Link: [github.com/open-telemetry/opentelemetry-demo](https://github.com/open-telemetry/opentelemetry-demo)
   Why it matters: provides a public, instrumentable workload whose latency can
   be measured under controlled scenarios.
   Suggested repo task: treat this as the default production-like latency target
   before using private systems.

6. OpenAI realtime or API cost docs
   Link: [developers.openai.com/api/docs/guides/realtime-costs](https://developers.openai.com/api/docs/guides/realtime-costs)
   Why it matters: useful if AgenVantage expands into realtime or streaming
   experiments with different latency/cost dynamics.
   Suggested repo task: keep separate from the current Responses API path unless
   realtime becomes in scope.

## Claim 4: "Proved downstream model answer quality retention across broad workloads"

### P0

1. OpenAI evaluation best practices
   Link: [developers.openai.com/api/docs/guides/evaluation-best-practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices)
   Why it matters: defines how to build evals for variable model behavior and
   production workflows.
   Suggested repo task: use these principles to formalize grader design,
   tolerances, and evaluation sample sizes.

2. OpenAI evaluate agent workflows guide
   Link: [developers.openai.com/api/docs/guides/agent-evals](https://developers.openai.com/api/docs/guides/agent-evals)
   Why it matters: directly targets agent workflows with traces, graders,
   datasets, and evaluation runs.
   Suggested repo task: map AgenVantage provider validation into an agent-style
   eval surface with traces plus graders.

3. OpenAI evals guide
   Link: [developers.openai.com/api/docs/guides/evals](https://developers.openai.com/api/docs/guides/evals)
   Why it matters: provides the programmatic evaluation flow on the platform.
   Suggested repo task: decide whether to use the hosted eval surface or remain
   fully local for reproducibility.

4. OpenAI evals repository
   Link: [github.com/openai/evals](https://github.com/openai/evals)
   Why it matters: gives examples of eval organization and custom eval
   structure, even though the hosted platform is being deprecated.
   Suggested repo task: borrow dataset and grader layout patterns for local
   evals.

5. CodeRAG-Bench paper
   Link: [arXiv CodeRAG-Bench](https://arxiv.org/abs/2406.14497)
   Why it matters: directly studies when retrieval helps code generation and
   where retrievers and generators still fail.
   Suggested repo task: use its repository-level tasks and retrieval-vs-generation
   framing to test whether AgenVantage actually preserves downstream quality.

6. CodeRAG-Bench repository
   Link: [github.com/code-rag-bench/code-rag-bench](https://github.com/code-rag-bench/code-rag-bench)
   Why it matters: includes retrieval code, generation loops, and execution-based
   evaluation paths.
   Suggested repo task: adapt the repository-level split as a downstream
   validation target for AgenVantage context packages.

### P1

7. CodeRepoQA paper
   Link: [arXiv CodeRepoQA PDF](https://arxiv.org/pdf/2412.14764)
   Why it matters: focuses on repository-level question answering rather than
   patch generation, which is close to AgenVantage's current explanation-heavy
   benchmark.
   Suggested repo task: compare current answer-rubric evaluation against a
   larger QA-style benchmark.

8. SWE-bench Verified
   Link: [swebench.com/verified.html](https://www.swebench.com/verified.html)
   Why it matters: strongest public benchmark choice if AgenVantage expands from
   explanation tasks into patch-generation workflows.
   Suggested repo task: use a narrow Verified slice once the provider runner and
   grading path are stable.

## Cross-Cutting Resource Imports

### Immediate imports

- OpenAI prompt caching guide
- OpenAI pricing page
- OpenAI latency optimization guide
- OpenAI evaluation best practices
- OpenTelemetry demo docs and feature flags
- RepoBench paper
- CodeRAG-Bench paper and repository

These are the best "do next" inputs because they jointly cover:

- measurement of real cost;
- measurement of latency;
- design of evals and graders;
- realistic public workloads; and
- end-to-end repository-level benchmark structure.

### Best public benchmark stack for AgenVantage

If only a small number of external resources are adopted, use this order:

1. OpenTelemetry Demo
2. RepoBench
3. CodeRAG-Bench
4. SWE-bench Verified
5. RepoQA
6. Long Code Arena

Reason:

- OpenTelemetry Demo gives the public incident-response workload.
- RepoBench gives the retrieval vs pipeline separation.
- CodeRAG-Bench gives retrieval-augmented generation evaluation.
- SWE-bench Verified gives recognized end-to-end software engineering proof.
- RepoQA and Long Code Arena broaden long-context coverage.

## Recommended Repo Changes After Import

1. Add a `resources` or `benchmarks/external` section in the repo for cached
   copies of benchmark metadata and adapters.
2. Expand `examples/provider_validation_cases.json` from 6 synthetic cases to
   30+ cases across at least 6 failure types.
3. Add live-run artifact persistence for provider validation.
4. Add OpenTelemetry GenAI fields to the live-run records.
5. Add a downstream benchmark target using either CodeRAG-Bench repo-level
   tasks or a SWE-bench slice.
6. Keep production-scoped latency claims separate from local synthetic runs.

## Materialize Locally

To turn the machine-readable manifest into local checkouts under ignored
workspace storage, run:

```bash
.venv/bin/python scripts/materialize_proof_resources.py --dry-run
.venv/bin/python scripts/materialize_proof_resources.py --github-only --priority P0
```

That writes an index under `external/proof-resources/` and shallow-clones the
selected GitHub repositories into `external/proof-resources/repos/`.
