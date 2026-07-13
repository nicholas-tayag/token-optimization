"""Dependency-light MCP server for AgenVantage over newline-delimited stdio."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, TextIO

from agenvantage.graph_backend import GRAPHIFY_EXPECTED_VERSION, GraphCandidate
from agenvantage.graphify_adapter import GraphifyAdapter
from agenvantage.graphify_runner import run_graphify
from agenvantage.presets import DEFAULT_PRESET, PRESETS, get_preset
from agenvantage.repo_context import build_context_package, source_files
from agenvantage.repo_index import repository_index_path
from agenvantage.tokenizer import TokenCounter

JSONRPC_VERSION = "2.0"
MCP_PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "agenvantage"
SERVER_VERSION = "0.1.0"
_MAX_TEXT_LENGTH = 100_000
_TERM_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_-]{1,}")


class ToolError(ValueError):
    """An expected, user-correctable MCP tool failure."""


def _object_schema(
    properties: dict[str, Any],
    required: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "prepare_context",
        "description": "Prepare bounded repository context and return a concise evidence summary.",
        "inputSchema": _object_schema(
            {
                "repo": {"type": "string", "description": "Repository directory path."},
                "task": {"type": "string", "description": "Engineering task to prepare for."},
                "preset": {"type": "string", "enum": list(PRESETS), "default": DEFAULT_PRESET},
                "budget": {"type": "integer", "minimum": 1, "maximum": 200_000, "default": 6000},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 20},
                "graph_backend": {
                    "type": "string",
                    "enum": ["auto", "off", "graphify"],
                    "default": "auto",
                },
                "include_prompt": {"type": "boolean", "default": False},
            },
            ("repo", "task"),
        ),
    },
    {
        "name": "search_graph",
        "description": "Find source-backed repository candidates using Graphify with lexical fallback.",
        "inputSchema": {
            **_object_schema(
                {
                    "repo": {"type": "string", "description": "Repository directory path."},
                    "task": {"type": "string", "description": "Graph search task."},
                    "query": {"type": "string", "description": "Alias for task."},
                    "graph_json": {
                        "type": "string",
                        "description": "Optional Graphify graph.json path.",
                    },
                    "hops": {"type": "integer", "enum": [1, 2], "default": 2},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 500,
                        "default": 50,
                    },
                },
                ("repo",),
            ),
            "anyOf": [{"required": ["task"]}, {"required": ["query"]}],
        },
    },
    {
        "name": "context_status",
        "description": "Report repository, cached-index, and Graphify readiness without writing.",
        "inputSchema": _object_schema(
            {"repo": {"type": "string", "description": "Repository directory path."}},
            ("repo",),
        ),
    },
)


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ToolError(f"{label} must be an object.")
    return value


def _reject_unknown(arguments: dict[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(arguments) - allowed)
    if unknown:
        raise ToolError(f"Unknown argument(s): {', '.join(unknown)}.")


def _text(arguments: dict[str, Any], name: str, *, required: bool = False) -> str | None:
    value = arguments.get(name)
    if value is None:
        if required:
            raise ToolError(f"{name} is required.")
        return None
    if not isinstance(value, str):
        raise ToolError(f"{name} must be a string.")
    value = value.strip()
    if not value:
        raise ToolError(f"{name} must not be empty.")
    if len(value) > _MAX_TEXT_LENGTH:
        raise ToolError(f"{name} exceeds the {_MAX_TEXT_LENGTH}-character limit.")
    return value


def _integer(
    arguments: dict[str, Any],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
    allowed: set[int] | None = None,
) -> int:
    value = arguments.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError(f"{name} must be an integer.")
    if value < minimum or value > maximum or (allowed is not None and value not in allowed):
        constraint = (
            f"one of {sorted(allowed)}"
            if allowed is not None
            else f"between {minimum} and {maximum}"
        )
        raise ToolError(f"{name} must be {constraint}.")
    return value


def _boolean(arguments: dict[str, Any], name: str, default: bool) -> bool:
    value = arguments.get(name, default)
    if not isinstance(value, bool):
        raise ToolError(f"{name} must be a boolean.")
    return value


def _repo_path(arguments: dict[str, Any]) -> Path:
    supplied = _text(arguments, "repo", required=True)
    assert supplied is not None
    path = Path(supplied).expanduser().resolve()
    if not path.is_dir():
        raise ToolError(f"Repository directory does not exist: {path}")
    return path


def _file_path(arguments: dict[str, Any], name: str) -> Path | None:
    supplied = _text(arguments, name)
    if supplied is None:
        return None
    path = Path(supplied).expanduser().resolve()
    if not path.is_file():
        raise ToolError(f"{name} file does not exist: {path}")
    return path


def _candidate_dict(candidate: GraphCandidate) -> dict[str, Any]:
    return {
        "symbol": candidate.symbol,
        "source_path": candidate.source_path,
        "source_location": candidate.source_location,
        "score": candidate.score,
        "hop": candidate.hop,
        "confidence": candidate.confidence,
        "relations": list(candidate.relations),
        "provenance": list(candidate.provenance),
    }


def _lexical_candidates(repo: Path, query: str, limit: int) -> list[dict[str, Any]]:
    terms = tuple(sorted({term.casefold() for term in _TERM_PATTERN.findall(query)}))
    ranked: list[tuple[int, str, int | None]] = []
    for path in source_files(repo):
        relative = path.relative_to(repo).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        folded_path = relative.casefold()
        folded_text = text.casefold()
        path_hits = sum(term in folded_path for term in terms)
        content_hits = sum(min(folded_text.count(term), 10) for term in terms)
        score = (path_hits * 20) + content_hits
        if score <= 0:
            continue
        matching_line = next(
            (
                number
                for number, line in enumerate(text.splitlines(), 1)
                if any(term in line.casefold() for term in terms)
            ),
            None,
        )
        ranked.append((score, relative, matching_line))
    return [
        {
            "symbol": Path(relative).stem,
            "source_path": relative,
            "source_location": f"L{line}" if line is not None else None,
            "score": score,
            "hop": 0,
            "confidence": "INFERRED",
            "relations": [],
            "provenance": [relative, "deterministic_lexical_fallback"],
        }
        for score, relative, line in sorted(ranked, key=lambda item: (-item[0], item[1]))[
            :limit
        ]
    ]


def prepare_context(arguments: dict[str, Any]) -> dict[str, Any]:
    """Run the existing packer and return only its handoff-critical fields."""

    arguments = _require_object(arguments, "arguments")
    _reject_unknown(
        arguments,
        {"repo", "task", "preset", "budget", "top_k", "graph_backend", "include_prompt"},
    )
    repo = _repo_path(arguments)
    task = _text(arguments, "task", required=True)
    assert task is not None
    preset_name = _text(arguments, "preset") or DEFAULT_PRESET
    if preset_name not in PRESETS:
        raise ToolError(f"preset must be one of: {', '.join(PRESETS)}.")
    budget = _integer(arguments, "budget", 6000, minimum=1, maximum=200_000)
    top_k = _integer(arguments, "top_k", 20, minimum=1, maximum=1000)
    graph_backend = _text(arguments, "graph_backend") or "auto"
    if graph_backend not in {"auto", "off", "graphify"}:
        raise ToolError("graph_backend must be one of: auto, off, graphify.")
    include_prompt = _boolean(arguments, "include_prompt", False)
    preset = get_preset(preset_name)

    prompt, report = build_context_package(
        repo,
        task,
        budget,
        TokenCounter(),
        top_k=top_k,
        instructions=preset.instructions,
        include_diff=preset.include_diff,
        include_log=preset.include_log,
        workflow="feature" if preset_name == "feature" else "generic",
        graph_backend=graph_backend,
    )
    selected_chunks = report.get("selected_chunks", [])
    result: dict[str, Any] = {
        "repo": str(repo),
        "task": task,
        "preset": preset_name,
        "selected_paths": list(
            dict.fromkeys(
                str(chunk["path"])
                for chunk in selected_chunks
                if isinstance(chunk, dict) and chunk.get("path")
            )
        ),
        "selected_chunks": [
            {
                "id": chunk.get("id"),
                "path": chunk.get("path"),
                "start_line": chunk.get("start_line"),
                "end_line": chunk.get("end_line"),
                "tokens": chunk.get("tokens"),
            }
            for chunk in selected_chunks
            if isinstance(chunk, dict)
        ],
        "change_surface": report.get("change_surface"),
        "token_accounting": report.get("prompt_token_accounting"),
        "graph": report.get("graph"),
        "sufficiency": report.get("context_sufficiency"),
        "handoff_ready": report.get("handoff_ready"),
    }
    if include_prompt:
        result["prompt"] = prompt
    return result


def search_graph(arguments: dict[str, Any]) -> dict[str, Any]:
    """Search Graphify output, falling back to deterministic source scanning."""

    arguments = _require_object(arguments, "arguments")
    _reject_unknown(arguments, {"repo", "task", "query", "graph_json", "hops", "limit"})
    repo = _repo_path(arguments)
    task = _text(arguments, "task")
    query = _text(arguments, "query")
    if task is not None and query is not None and task != query:
        raise ToolError("Provide either task or query, not conflicting values.")
    search_text = task or query
    if search_text is None:
        raise ToolError("task or query is required.")
    graph_json = _file_path(arguments, "graph_json")
    hops = _integer(arguments, "hops", 2, minimum=1, maximum=2, allowed={1, 2})
    limit = _integer(arguments, "limit", 50, minimum=1, maximum=500)

    graph_status = "unavailable"
    graph_reason: str | None = None
    candidates: tuple[GraphCandidate, ...] = ()
    if graph_json is not None:
        backend = GraphifyAdapter(graph_json).candidates_for_task(
            repo, search_text, max_hops=hops, limit=limit
        )
        graph_status = backend.status
        graph_reason = backend.reason
        candidates = backend.candidates if backend.available else ()
    else:
        with tempfile.TemporaryDirectory(prefix="agenvantage-mcp-graphify-") as temporary:
            run = run_graphify(repo, Path(temporary), timeout_seconds=120.0)
            graph_status = run.status
            graph_reason = run.reason
            if run.graph_path is not None:
                backend = GraphifyAdapter(run.graph_path).candidates_for_task(
                    repo, search_text, max_hops=hops, limit=limit
                )
                graph_status = backend.status
                graph_reason = backend.reason
                candidates = backend.candidates if backend.available else ()

    if candidates:
        return {
            "repo": str(repo),
            "query": search_text,
            "backend": "graphify",
            "graph_status": graph_status,
            "graph_reason": graph_reason,
            "fallback_used": False,
            "candidates": [_candidate_dict(candidate) for candidate in candidates],
        }
    return {
        "repo": str(repo),
        "query": search_text,
        "backend": "lexical",
        "graph_status": graph_status,
        "graph_reason": graph_reason or "no_graph_candidates",
        "fallback_used": True,
        "candidates": _lexical_candidates(repo, search_text, limit),
    }


def _git_head(repo: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def _index_status(repo: Path, head: str | None) -> dict[str, Any]:
    path = repository_index_path(repo)
    result: dict[str, Any] = {"ready": False, "path": str(path), "status": "missing"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return result
    except (OSError, UnicodeError, json.JSONDecodeError):
        result["status"] = "invalid"
        return result
    manifest = payload.get("manifest")
    if not isinstance(manifest, dict) or not isinstance(payload.get("entries"), dict):
        result["status"] = "invalid"
        return result
    revision = manifest.get("revision")
    ready = head is not None and revision == head
    result.update(
        {
            "ready": ready,
            "status": "fresh" if ready else "stale",
            "revision": revision,
            "indexed_files": manifest.get("indexed_files"),
            "indexed_at": manifest.get("indexed_at"),
        }
    )
    return result


def _graphify_status() -> dict[str, Any]:
    executable = shutil.which("graphify")
    if executable is None:
        return {
            "ready": False,
            "status": "missing",
            "expected_version": GRAPHIFY_EXPECTED_VERSION,
        }
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "ready": False,
            "status": "error",
            "reason": type(exc).__name__,
            "expected_version": GRAPHIFY_EXPECTED_VERSION,
        }
    match = re.search(r"(?:^|\s)(\d+\.\d+\.\d+)(?:\s|$)", result.stdout.strip())
    version = match.group(1) if match else result.stdout.strip() or None
    ready = result.returncode == 0 and version == GRAPHIFY_EXPECTED_VERSION
    return {
        "ready": ready,
        "status": "ready" if ready else "incompatible",
        "executable": executable,
        "version": version,
        "expected_version": GRAPHIFY_EXPECTED_VERSION,
    }


def context_status(arguments: dict[str, Any]) -> dict[str, Any]:
    """Inspect readiness using read-only filesystem and subprocess operations."""

    arguments = _require_object(arguments, "arguments")
    _reject_unknown(arguments, {"repo"})
    repo = _repo_path(arguments)
    head = _git_head(repo)
    return {
        "repo": {
            "ready": True,
            "path": str(repo),
            "git": head is not None,
            "revision": head,
        },
        "index": _index_status(repo, head),
        "graphify": _graphify_status(),
    }


_TOOL_HANDLERS = {
    "prepare_context": prepare_context,
    "search_graph": search_graph,
    "context_status": context_status,
}


def _response(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}


def _tool_result(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "content": [{"type": "text", "text": json.dumps(payload, sort_keys=True)}],
        "isError": is_error,
    }
    if not is_error:
        result["structuredContent"] = payload
    return result


def handle_request(message: Any) -> dict[str, Any] | None:
    """Handle one decoded JSON-RPC message without starting a process."""

    if not isinstance(message, dict):
        return _error(None, -32600, "Invalid Request")
    request_id = message.get("id")
    has_id = "id" in message
    if message.get("jsonrpc") != JSONRPC_VERSION or not isinstance(message.get("method"), str):
        return _error(request_id if has_id else None, -32600, "Invalid Request")
    if has_id and (
        isinstance(request_id, bool)
        or request_id is not None
        and not isinstance(request_id, (str, int, float))
    ):
        return _error(None, -32600, "Invalid Request")

    method = message["method"]
    params = message.get("params", {})
    if method == "notifications/initialized":
        return None if not has_id else _response(request_id, {})
    if not has_id:
        return None
    if method == "initialize":
        if not isinstance(params, dict):
            return _error(request_id, -32602, "Invalid params")
        return _response(
            request_id,
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": "Use prepare_context for bounded coding context and search_graph for source-backed graph retrieval.",
            },
        )
    if method == "ping":
        return _response(request_id, {})
    if method == "tools/list":
        if not isinstance(params, dict):
            return _error(request_id, -32602, "Invalid params")
        return _response(request_id, {"tools": list(TOOLS)})
    if method == "tools/call":
        if not isinstance(params, dict):
            return _error(request_id, -32602, "Invalid params")
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(name, str) or name not in _TOOL_HANDLERS:
            return _error(request_id, -32602, "Invalid params", "Unknown tool name.")
        try:
            payload = _TOOL_HANDLERS[name](_require_object(arguments, "arguments"))
        except ToolError as exc:
            return _response(request_id, _tool_result({"error": str(exc)}, is_error=True))
        except Exception as exc:
            return _response(
                request_id,
                _tool_result(
                    {"error": f"{name} failed: {type(exc).__name__}: {exc}"},
                    is_error=True,
                ),
            )
        return _response(request_id, _tool_result(payload))
    return _error(request_id, -32601, "Method not found")


def run_stdio_server(
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
) -> None:
    """Serve newline-delimited JSON-RPC messages until input reaches EOF."""

    reader = input_stream or sys.stdin
    writer = output_stream or sys.stdout
    for line in reader:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            response = _error(None, -32700, "Parse error")
        else:
            response = handle_request(message)
        if response is not None:
            writer.write(json.dumps(response, separators=(",", ":")) + "\n")
            writer.flush()


__all__ = [
    "MCP_PROTOCOL_VERSION",
    "TOOLS",
    "context_status",
    "handle_request",
    "prepare_context",
    "run_stdio_server",
    "search_graph",
]
