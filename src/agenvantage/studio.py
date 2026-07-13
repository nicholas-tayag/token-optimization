"""Local HTML studio for setup, context preview, and navigation."""

from __future__ import annotations

import html
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from agenvantage.agent_integrations import SUPPORTED_TARGETS, agent_skill_status

STUDIO_DIRNAME = "studio"
DEFAULT_PREVIEW_NAME = "context-preview.html"
DEFAULT_SETUP_NAME = "setup.html"
DEFAULT_HUB_NAME = "index.html"

_DEMO_TASK = (
    "Explain the repository architecture and identify the most likely files "
    "to change for a small feature with regression tests."
)


def studio_root(repo: Path | None = None) -> Path:
    root = Path(repo or ".").resolve()
    return root / ".agenvantage" / STUDIO_DIRNAME


def build_doctor_payload(
    *,
    scope: str = "personal",
    command: str = "agenvantage",
    project_root: Path | None = None,
) -> dict[str, Any]:
    skills = [
        agent_skill_status(target, scope=scope, command=command, project_root=project_root)
        for target in SUPPORTED_TARGETS
    ]
    executables = {
        name: shutil.which(name)
        for name in ("git", "graphify", "codex", "claude", "npx")
    }
    graphify_version = None
    graphify_error = None
    if executables["graphify"]:
        try:
            completed = subprocess.run(
                [str(executables["graphify"]), "--version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if completed.returncode == 0:
                graphify_version = completed.stdout.strip()
            else:
                graphify_error = completed.stderr.strip() or "version check failed"
        except (OSError, subprocess.TimeoutExpired) as exc:
            graphify_error = str(exc)
    repository_root = None
    cwd = Path(project_root or Path.cwd())
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if completed.returncode == 0:
            repository_root = completed.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        repository_root = None
    current_skills = sum(bool(item.get("current")) for item in skills)
    return {
        "status": "ready" if current_skills and executables["git"] else "needs_setup",
        "scope": scope,
        "repository_root": repository_root,
        "agenvantage_command": command,
        "mcp_command": f"{command} mcp",
        "executables": executables,
        "graphify": {
            "available": bool(executables["graphify"]),
            "version": graphify_version,
            "error": graphify_error,
        },
        "agent_launchers": {
            "codex": bool(executables["codex"] or executables["npx"]),
            "claude": bool(executables["claude"]),
        },
        "skills": skills,
    }


def build_studio_status(
    *,
    repo: Path,
    doctor: dict[str, Any],
    checkup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    repo = repo.resolve()
    studio_dir = studio_root(repo)
    return {
        "repo_path": str(repo),
        "doctor": doctor,
        "checkup": checkup or {},
        "paths": {
            "studio_hub": str(studio_dir / DEFAULT_HUB_NAME),
            "setup_panel": str(studio_dir / DEFAULT_SETUP_NAME),
            "context_preview": str(studio_dir / DEFAULT_PREVIEW_NAME),
            "observability_dashboard": str(repo / ".agenvantage" / "observability-dashboard.html"),
        },
        "ready": doctor.get("status") == "ready",
        "demo_task": _DEMO_TASK,
    }


def graph_policy_chip(graph: dict[str, Any] | None) -> dict[str, str]:
    graph = graph or {}
    decision = str(graph.get("decision") or "off")
    backend = str(graph.get("backend") or graph.get("requested_mode") or "off")
    reasons = [str(item) for item in graph.get("reasons") or ()]
    primary_reason = reasons[0].replace("_", " ") if reasons else "baseline retrieval"
    tone = "neutral"
    if decision == "use" or graph.get("used"):
        tone = "positive"
    elif decision == "skip" or backend == "off":
        tone = "neutral"
    elif graph.get("attempted") and not graph.get("used"):
        tone = "warning"
    label = f"{backend} → {decision}"
    return {
        "label": label,
        "detail": primary_reason,
        "tone": tone,
        "reasons": ", ".join(reason.replace("_", " ") for reason in reasons[:4]),
    }


def _fmt_int(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def _chip_html(chip: dict[str, str]) -> str:
    tone = html.escape(chip.get("tone", "neutral"))
    return (
        f'<span class="chip {tone}" title="{html.escape(chip.get("reasons", ""))}">'
        f"{html.escape(chip.get('label', 'graph'))}"
        f'<em>{html.escape(chip.get("detail", ""))}</em></span>'
    )


def _shared_styles() -> str:
    return """
      :root {
        color-scheme: light;
        --ink: #111827;
        --muted: #64748b;
        --bg: #eef2f6;
        --panel: #ffffff;
        --panel-soft: #f8fafc;
        --line: #d8dee8;
        --accent: #0f766e;
        --accent-2: #2563eb;
        --warn: #b45309;
        --danger: #b91c1c;
        --ok: #047857;
        --shadow: rgba(15, 23, 42, 0.08);
        font-family: "Avenir Next", ui-sans-serif, "Helvetica Neue", sans-serif;
      }
      body {
        margin: 0;
        color: var(--ink);
        background:
          radial-gradient(circle at top left, rgba(37, 99, 235, 0.14), transparent 28rem),
          radial-gradient(circle at 90% 15%, rgba(15, 118, 110, 0.16), transparent 24rem),
          linear-gradient(135deg, #eef2f6 0%, #e6edf5 100%);
      }
      header { padding: 2rem clamp(1rem, 4vw, 4rem) 1rem; }
      header h1 {
        margin: 0;
        font-size: clamp(2rem, 4vw, 3.5rem);
        letter-spacing: -0.05em;
      }
      header p { max-width: 48rem; color: var(--muted); }
      main { padding: 0 clamp(1rem, 4vw, 4rem) 4rem; }
      .nav {
        display: flex;
        flex-wrap: wrap;
        gap: 0.65rem;
        margin: 1rem 0 1.5rem;
      }
      .nav a, .button-link {
        display: inline-flex;
        align-items: center;
        gap: 0.35rem;
        padding: 0.55rem 0.85rem;
        border-radius: 999px;
        border: 1px solid var(--line);
        background: rgba(255, 255, 255, 0.92);
        color: var(--ink);
        text-decoration: none;
        box-shadow: 0 8px 24px var(--shadow);
      }
      .nav a.active { border-color: var(--accent); color: var(--accent); }
      .grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
        gap: 1rem;
      }
      .panel, .card, .stat {
        background: rgba(255, 255, 255, 0.92);
        border: 1px solid var(--line);
        border-radius: 18px;
        box-shadow: 0 18px 50px var(--shadow);
      }
      .panel, .card { padding: 1rem 1.1rem; }
      .stat { padding: 1rem; }
      .stat strong { display: block; font-size: 1.8rem; letter-spacing: -0.04em; }
      .stat span, .muted { color: var(--muted); }
      .panel h2, .card h2 {
        margin: 0 0 0.75rem;
        font-size: 1rem;
        letter-spacing: -0.02em;
      }
      .status-pill {
        display: inline-flex;
        border-radius: 999px;
        padding: 0.2rem 0.55rem;
        font-size: 0.78rem;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        background: #ecfdf5;
        color: var(--ok);
      }
      .status-pill.warn { background: #fef3c7; color: var(--warn); }
      .status-pill.fail { background: #fee2e2; color: var(--danger); }
      .chip {
        display: inline-flex;
        align-items: center;
        gap: 0.45rem;
        border-radius: 999px;
        padding: 0.28rem 0.65rem;
        font-size: 0.82rem;
        background: #ecfeff;
        color: #0e7490;
        border: 1px solid #a5f3fc;
      }
      .chip.positive { background: #ecfdf5; color: var(--ok); border-color: #a7f3d0; }
      .chip.warning { background: #fff7ed; color: var(--warn); border-color: #fed7aa; }
      .chip.neutral { background: #f1f5f9; color: var(--muted); border-color: var(--line); }
      .chip em { font-style: normal; opacity: 0.85; }
      ul.clean { list-style: none; padding: 0; margin: 0; }
      ul.clean li {
        display: grid;
        gap: 0.15rem;
        padding: 0.65rem 0;
        border-top: 1px solid var(--line);
      }
      ul.clean li:first-child { border-top: 0; padding-top: 0; }
      .file-row {
        display: grid;
        grid-template-columns: 1fr auto;
        gap: 0.5rem;
        padding: 0.55rem 0;
        border-top: 1px solid var(--line);
      }
      .file-row:first-child { border-top: 0; }
      details { margin-top: 0.75rem; }
      pre {
        white-space: pre-wrap;
        word-break: break-word;
        background: var(--panel-soft);
        border: 1px solid var(--line);
        border-radius: 12px;
        padding: 0.85rem;
        overflow: auto;
        max-height: 24rem;
      }
      code {
        background: #eee2c8;
        border-radius: 6px;
        padding: 0.12rem 0.32rem;
      }
      .command-box {
        font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        font-size: 0.85rem;
        background: #0f172a;
        color: #e2e8f0;
        border-radius: 12px;
        padding: 0.85rem;
        overflow-x: auto;
      }
    """


def _nav_html(active: str, links: dict[str, str]) -> str:
    items = []
    for key, label in (
        ("hub", "Studio"),
        ("setup", "Setup"),
        ("preview", "Context preview"),
        ("observability", "Traces"),
    ):
        href = links.get(key, "#")
        css = " active" if key == active else ""
        items.append(f'<a class="{css.strip()}" href="{html.escape(href)}">{html.escape(label)}</a>')
    return f'<nav class="nav">{"".join(items)}</nav>'


def render_setup_panel(status: dict[str, Any], *, links: dict[str, str]) -> str:
    doctor = status.get("doctor") or {}
    checkup = status.get("checkup") or {}
    doctor_state = str(doctor.get("status") or "needs_setup")
    pill_class = "status-pill" if doctor_state == "ready" else "status-pill warn"
    skill_rows = []
    for skill in doctor.get("skills") or []:
        skill_state = str(skill.get("status") or "missing")
        skill_rows.append(
            "<li>"
            f"<strong>{html.escape(str(skill.get('target', 'agent')).title())}</strong>"
            f"<span>{html.escape(skill_state)}</span>"
            f"<em class=\"muted\">{html.escape(str(skill.get('destination', '')))}</em>"
            "</li>"
        )
    checkup_rows = []
    for finding in checkup.get("findings") or []:
        finding_state = str(finding.get("status") or "warn")
        row_class = ""
        if finding_state == "fail":
            row_class = " fail"
        elif finding_state == "warn":
            row_class = " warn"
        checkup_rows.append(
            "<li>"
            f"<span class=\"status-pill{row_class}\">{html.escape(finding_state)}</span>"
            f"<strong>{html.escape(str(finding.get('title', 'Finding')))}</strong>"
            f"<span>{html.escape(str(finding.get('detail', '')))}</span>"
            f"<em class=\"muted\">{html.escape(str(finding.get('next_action', '')))}</em>"
            "</li>"
        )
    launcher_rows = []
    for name, ready in (doctor.get("agent_launchers") or {}).items():
        launcher_rows.append(
            f"<li><strong>{html.escape(name.title())}</strong>"
            f"<span>{'ready' if ready else 'missing'}</span></li>"
        )
    graphify = doctor.get("graphify") or {}
    graphify_text = graphify.get("version") or graphify.get("error") or "not installed"
    commands = [
        doctor.get("agenvantage_command", "agenvantage") + " init",
        doctor.get("agenvantage_command", "agenvantage") + " doctor",
        doctor.get("agenvantage_command", "agenvantage") + " studio demo",
        doctor.get("mcp_command", "agenvantage mcp"),
    ]
    return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>AgenVantage Setup</title>
    <style>{_shared_styles()}</style>
  </head>
  <body>
    <header>
      <h1>Setup</h1>
      <p>Install skills, verify Graphify and agent launchers, and confirm this repository is ready for automatic context.</p>
      <p><span class="{pill_class}">{html.escape(doctor_state)}</span></p>
    </header>
    <main>
      {_nav_html("setup", links)}
      <section class="grid">
        <div class="stat"><strong>{html.escape(str(len([s for s in doctor.get('skills', []) if s.get('current')])))}</strong><span>skills current</span></div>
        <div class="stat"><strong>{html.escape('yes' if graphify.get('available') else 'no')}</strong><span>graphify available</span></div>
        <div class="stat"><strong>{html.escape(str(checkup.get('overall_status', 'unknown')))}</strong><span>repo checkup</span></div>
        <div class="stat"><strong>{html.escape(str(checkup.get('metrics', {}).get('trace_count', 0)))}</strong><span>saved traces</span></div>
      </section>
      <section class="grid" style="margin-top:1rem;">
        <section class="panel">
          <h2>Agent integrations</h2>
          <ul class="clean">{''.join(skill_rows) or '<li class="muted">No skill data.</li>'}</ul>
        </section>
        <section class="panel">
          <h2>Launchers and Graphify</h2>
          <ul class="clean">{''.join(launcher_rows)}</ul>
          <p class="muted">Graphify: {html.escape(str(graphify_text))}</p>
          <p class="muted">Repository: {html.escape(str(doctor.get('repository_root') or status.get('repo_path') or 'not detected'))}</p>
        </section>
      </section>
      <section class="panel" style="margin-top:1rem;">
        <h2>Repository checkup</h2>
        <ul class="clean">{''.join(checkup_rows) or '<li class="muted">Run <code>agenvantage checkup</code> to populate repository hygiene findings.</li>'}</ul>
      </section>
      <section class="panel" style="margin-top:1rem;">
        <h2>Recommended commands</h2>
        <div class="command-box">{'<br>'.join(html.escape(command) for command in commands)}</div>
      </section>
    </main>
  </body>
</html>"""


def _target_paths(change_surface: dict[str, Any], key: str) -> list[str]:
    items = change_surface.get(key) or []
    paths: list[str] = []
    for item in items:
        if isinstance(item, dict):
            path = item.get("path") or item.get("relative_path")
        else:
            path = item
        if path:
            paths.append(str(path))
    return paths


def render_context_preview(
    report: dict[str, Any],
    *,
    links: dict[str, str],
    preset_name: str | None = None,
) -> str:
    task = str(report.get("task") or "Untitled task")
    preset = preset_name or str(report.get("preset") or report.get("workflow") or "feature")
    accounting = report.get("prompt_token_accounting") or {}
    graph = report.get("graph") or {}
    chip = _chip_html(graph_policy_chip(graph))
    change_surface = report.get("change_surface") or {}
    selected_chunks = report.get("selected_chunks") or []
    chunk_rows = []
    for chunk in selected_chunks[:24]:
        if not isinstance(chunk, dict):
            continue
        chunk_rows.append(
            "<div class=\"file-row\">"
            f"<div><strong>{html.escape(str(chunk.get('relative_path') or chunk.get('path') or 'unknown'))}</strong>"
            f"<div class=\"muted\">{html.escape(str(chunk.get('id', '')))}</div></div>"
            f"<span>{_fmt_int(chunk.get('tokens'))} tok</span>"
            "</div>"
        )
    surface_sections = []
    for label, key in (
        ("Edit targets", "edit_targets"),
        ("Test targets", "test_targets"),
        ("Config targets", "config_targets"),
        ("Supporting targets", "supporting_targets"),
    ):
        paths = _target_paths(change_surface, key)
        if not paths:
            continue
        surface_sections.append(
            f"<section class=\"panel\"><h2>{html.escape(label)}</h2><ul class=\"clean\">"
            + "".join(f"<li><strong>{html.escape(path)}</strong></li>" for path in paths)
            + "</ul></section>"
        )
    missing = [str(item) for item in change_surface.get("missing_signals") or []]
    missing_html = (
        "<section class=\"panel\"><h2>Missing signals</h2><ul class=\"clean\">"
        + "".join(f"<li>{html.escape(item)}</li>" for item in missing)
        + "</ul></section>"
        if missing
        else ""
    )
    sufficiency = report.get("context_sufficiency") or {}
    sufficiency_status = str(sufficiency.get("status") or "unknown")
    return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>AgenVantage Context Preview</title>
    <style>{_shared_styles()}</style>
  </head>
  <body>
    <header>
      <h1>Context preview</h1>
      <p>{html.escape(task)}</p>
      <p>{chip} <span class="status-pill">{html.escape(preset)}</span> <span class="status-pill">{html.escape(sufficiency_status)}</span></p>
    </header>
    <main>
      {_nav_html("preview", links)}
      <section class="grid">
        <div class="stat"><strong>{_fmt_int(accounting.get('packed_prompt_tokens'))}</strong><span>packed tokens</span></div>
        <div class="stat"><strong>{_fmt_int(accounting.get('full_scan_prompt_tokens'))}</strong><span>full scan</span></div>
        <div class="stat"><strong>{_fmt_int(accounting.get('prompt_tokens_saved_vs_full_scan'))}</strong><span>tokens saved</span></div>
        <div class="stat"><strong>{len(selected_chunks)}</strong><span>selected chunks</span></div>
      </section>
      <section class="panel" style="margin-top:1rem;">
        <h2>Selected files</h2>
        {''.join(chunk_rows) or '<p class="muted">No selected chunks recorded.</p>'}
      </section>
      <section class="grid" style="margin-top:1rem;">
        {''.join(surface_sections) or '<section class="panel"><h2>Change surface</h2><p class="muted">No structured edit/test targets were inferred for this preset.</p></section>'}
      </section>
      {missing_html}
      <details style="margin-top:1rem;">
        <summary>Graph policy details</summary>
        <pre>{html.escape(json.dumps(graph.get('policy') or graph, indent=2))}</pre>
      </details>
    </main>
  </body>
</html>"""


def render_studio_hub(status: dict[str, Any], *, links: dict[str, str]) -> str:
    doctor = status.get("doctor") or {}
    checkup = status.get("checkup") or {}
    ready = bool(status.get("ready"))
    pill_class = "status-pill" if ready else "status-pill warn"
    cards = [
        ("setup", "Install and verify", "Check skills, Graphify, MCP, and repository readiness.", links.get("setup", "#")),
        ("preview", "Preview context", "See selected files, token savings, graph policy, and change surface.", links.get("preview", "#")),
        ("observability", "Review traces", "Inspect saved agent runs, warnings, and token reductions.", links.get("observability", "#")),
    ]
    card_html = "".join(
        f'<a class="card button-link" href="{html.escape(href)}">'
        f"<h2>{html.escape(title)}</h2>"
        f"<p>{html.escape(body)}</p>"
        f'<span class="muted">Open panel →</span></a>'
        for _, title, body, href in cards
    )
    command = doctor.get("agenvantage_command", "agenvantage")
    return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>AgenVantage Studio</title>
    <style>{_shared_styles()}</style>
  </head>
  <body>
    <header>
      <h1>AgenVantage Studio</h1>
      <p>Plug-and-play local UI for setup, context preview, and trace review. No API keys required.</p>
      <p><span class="{pill_class}">{'ready' if ready else 'needs setup'}</span></p>
    </header>
    <main>
      {_nav_html("hub", links)}
      <section class="grid">
        <div class="stat"><strong>{html.escape(str(len([s for s in doctor.get('skills', []) if s.get('current')])))}</strong><span>skills installed</span></div>
        <div class="stat"><strong>{html.escape(str(checkup.get('metrics', {}).get('trace_count', 0)))}</strong><span>saved traces</span></div>
        <div class="stat"><strong>{html.escape('yes' if (doctor.get('graphify') or {}).get('available') else 'no')}</strong><span>graphify</span></div>
        <div class="stat"><strong>{html.escape(str(checkup.get('overall_status', 'unknown')))}</strong><span>repo checkup</span></div>
      </section>
      <section class="grid" style="margin-top:1rem;">{card_html}</section>
      <section class="panel" style="margin-top:1rem;">
        <h2>One-click demo</h2>
        <p class="muted">Pack the current repository, write a preview page, record a trace, and refresh the dashboards.</p>
        <div class="command-box">{html.escape(command)} studio demo</div>
      </section>
    </main>
  </body>
</html>"""


def _relative_link(from_dir: Path, target: Path) -> str:
    import os

    return Path(os.path.relpath(target.resolve(), from_dir.resolve())).as_posix()


def studio_links(repo: Path) -> dict[str, str]:
    studio_dir = studio_root(repo)
    observability = repo / ".agenvantage" / "observability-dashboard.html"
    return {
        "hub": DEFAULT_HUB_NAME,
        "setup": DEFAULT_SETUP_NAME,
        "preview": DEFAULT_PREVIEW_NAME,
        "observability": _relative_link(studio_dir, observability),
    }


def write_studio_pages(
    repo: Path,
    *,
    status: dict[str, Any],
    preview_report: dict[str, Any] | None = None,
    preset_name: str | None = None,
) -> dict[str, Path]:
    repo = repo.resolve()
    output_dir = studio_root(repo)
    output_dir.mkdir(parents=True, exist_ok=True)
    links = studio_links(repo)
    paths = {
        "hub": output_dir / DEFAULT_HUB_NAME,
        "setup": output_dir / DEFAULT_SETUP_NAME,
        "preview": output_dir / DEFAULT_PREVIEW_NAME,
    }
    paths["hub"].write_text(render_studio_hub(status, links=links), encoding="utf-8")
    paths["setup"].write_text(render_setup_panel(status, links=links), encoding="utf-8")
    if preview_report is not None:
        paths["preview"].write_text(
            render_context_preview(preview_report, links=links, preset_name=preset_name),
            encoding="utf-8",
        )
    return paths


def render_graph_policy_chip_html(graph: dict[str, Any] | None) -> str:
    return _chip_html(graph_policy_chip(graph))


def demo_task() -> str:
    return _DEMO_TASK


__all__ = [
    "DEFAULT_HUB_NAME",
    "DEFAULT_PREVIEW_NAME",
    "DEFAULT_SETUP_NAME",
    "build_doctor_payload",
    "build_studio_status",
    "demo_task",
    "graph_policy_chip",
    "render_graph_policy_chip_html",
    "render_context_preview",
    "render_setup_panel",
    "render_studio_hub",
    "studio_links",
    "studio_root",
    "write_studio_pages",
]
