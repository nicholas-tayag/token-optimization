# Plug-and-Play Agent Integrations

AgenVantage can install automatic context preparation for Cursor, Codex, and
Claude Code from one package.

## First Install

After installing the package and optional pinned Graphify dependency:

```bash
uv tool install "agenvantage[graphify]"
agenvantage init
agenvantage doctor
```

`init` installs the same auto-discoverable skill for all three agents. The skill
records the exact AgenVantage executable used during installation, so it can
prepare other repositories without relying on shell activation.

Use project-scoped skills for a shared repository:

```bash
agenvantage init --project
```

## Automatic Graph Policy

The installed skill requests `--graph-backend auto`. AgenVantage starts with
its normal repository scan and uses Graphify only when deterministic signals
justify the overhead, such as:

- partial or ambiguous grounding;
- cross-file, compound, debug, review, or execution-flow tasks;
- explicit paths or identifiers requiring relationship traversal; and
- repository scale large enough to make graph expansion useful.

Small, trivial, already-grounded, or clearly single-file tasks stay on the
baseline retrieval path. Explicit `off` and `graphify` modes remain available.
Every manifest records the policy decision and reasons.

## Guaranteed Agent Wrappers

Skills are model-directed. Use wrappers when context preparation must happen
before the external agent starts:

```bash
agenvantage codex "Add rate limiting across the API and persistence layer"
agenvantage claude "Debug the checkout validation failure"
```

Both wrappers prepare automatic context, pass it on standard input, execute the
agent in the repository, and record the run in AgenVantage observability.

Preview without launching an agent:

```bash
agenvantage codex "Trace request validation" --dry-run
```

## Plug into any repository

From any cloned project — no AgenVantage fixture or config required:

```bash
cd /path/to/your-repo
agenvantage compare --task "Fix the checkout validation bug and add a regression test"
agenvantage codex "Fix the checkout validation bug and add a regression test"
agenvantage claude "Fix the checkout validation bug and add a regression test"
```

`compare` writes artifacts only under `<repo>/.agenvantage/compare/` and prints
first-request token savings. Use `--live` when Codex is installed to run a
paired control-vs-treatment trajectory.

Import Codex JSONL logs into observability for dashboard reconciliation:

```bash
agenvantage provider import --records control.jsonl --trace-id <trace-id> --repo .
```

## Manager / worker orchestration

Wave packages live in `examples/agent_development_partition.json`. The manager
plans and reviews; workers implement one package at a time with a scoped context
pack under `.agenvantage/orchestration/`:

```bash
agenvantage orchestrate plan --repo .
agenvantage orchestrate run --package W2.1 --repo .
agenvantage orchestrate review --package W2.1 --repo .
```

Each worker handoff records allowed paths, the prepared context manifest, and a
`worker-response.json` the manager validates before merge.

One-time agent setup:

```bash
uv tool install "agenvantage[graphify]"
agenvantage init
agenvantage doctor
```

After `init`, Cursor/Codex/Claude skills call AgenVantage automatically before
substantial coding tasks. Wrappers (`agenvantage codex`, `agenvantage claude`)
guarantee context is prepared even when a skill is ignored.

## Local studio UI

Generate a browser-based setup and preview hub for the current repository:

```bash
agenvantage studio
agenvantage studio demo
agenvantage pack --task "Explain the checkout flow" --preview
```

The studio writes HTML under `.agenvantage/studio/`:

- `index.html` — hub with setup, preview, and trace links
- `setup.html` — doctor + checkup status for integrations and repository hygiene
- `context-preview.html` — selected files, token accounting, graph policy chip, and change surface

The observability dashboard links back to the studio and now shows graph policy chips plus manifest change-surface drill-down on each trace.

## MCP

The dependency-light MCP server exposes:

- `prepare_context`
- `search_graph`
- `context_status`

Run it with:

```bash
agenvantage mcp
```

Generic MCP configuration:

```json
{
  "mcpServers": {
    "agenvantage": {
      "command": "agenvantage",
      "args": ["mcp"]
    }
  }
}
```

`agenvantage doctor --json` prints the exact interpreter-qualified MCP command
for installations that do not put `agenvantage` directly on `PATH`.

## Failure Behavior

Graphify and MCP failures do not block coding work. Automatic retrieval falls
back to the normal source-backed packer, preserves the hard token budget, and
reports the fallback reason. Modified user skills are never overwritten or
removed unless `--force` is supplied.
