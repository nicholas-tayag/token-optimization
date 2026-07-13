---
name: agenvantage-context
description: Automatically prepares token-budgeted repository context before substantial coding, debugging, review, explanation, or cross-file architecture tasks. Use when a request requires understanding or changing a local codebase; skip trivial edits and non-code questions.
---

# AgenVantage Context

Prepare repository context before starting substantive code work. Do this
without asking the user to run extra setup commands.

## Workflow

1. Skip this skill for trivial one-line edits, non-code questions, or when an
   AgenVantage package has already been generated for the current request.
2. Find the repository root with `git rev-parse --show-toplevel`.
3. Choose the matching preset: `feature`, `debug`, `review`, or `explain`.
4. Run:

   ```bash
   {{AGENVANTAGE_COMMAND}} pack \
     --repo "<repository-root>" \
     --preset "<preset>" \
     --discipline full \
     --task "<user request verbatim>" \
     --graph-backend auto \
     --handoff-json
   ```

5. Use the handoff's selected chunks, change surface, context plan, graph
   provenance, and sufficiency warnings as the initial evidence for the task.
6. When `missing_signals` is non-empty, call the AgenVantage MCP
   `expand_context` tool before implementing. Do not guess or claim that the
   package is complete.

## Implementation Discipline

After reading the supplied context: avoid unnecessary code, reuse selected
implementations, prefer the standard library or native framework APIs, reuse
installed dependencies, and write the minimum change that passes the listed
tests. Never simplify away validation, authentication, path-traversal checks,
data-loss error handling, security-sensitive parsing, or accessibility. Run any
selected guard or contract test before declaring completion.

## Behavior

- Graph enrichment is optional. With `--graph-backend auto`, AgenVantage uses
  an available graph backend and falls back to baseline retrieval when none is
  available or graph data is stale, malformed, oversized, or times out.
- If the command fails entirely, continue with normal repository tools instead
  of blocking the user's task.
- Never weaken the package's hard token budget or source-path safety checks.
- Do not print the full handoff unless the user asks. Summarize only information
  that helps complete the task.
- Do not regenerate the same package repeatedly in one turn.
- Keep `.env`, credentials, and secret-looking values out of prompts.

## Missing command

If `agenvantage` is unavailable, tell the user once:

```text
Install AgenVantage, then install its context skill for this coding agent.
```

Continue the task using normal repository tools instead of blocking.
