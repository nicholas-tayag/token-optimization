from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

from agenvantage import mcp_server


def _request(method: str, *, request_id: int = 1, params=None) -> dict:
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def test_protocol_initialize_ping_notifications_and_method_errors() -> None:
    initialized = mcp_server.handle_request(
        _request(
            "initialize",
            params={
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        )
    )
    assert initialized == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "protocolVersion": mcp_server.MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "agenvantage", "version": "0.1.0"},
            "instructions": (
                "Use prepare_context for bounded coding context, expand_context "
                "when signals are missing, and search_graph for source-backed graph retrieval."
            ),
        },
    }
    assert mcp_server.handle_request(_request("ping"))["result"] == {}
    assert (
        mcp_server.handle_request(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        is None
    )
    assert mcp_server.handle_request(_request("missing"))["error"]["code"] == -32601
    assert mcp_server.handle_request([])["error"]["code"] == -32600


def test_tools_list_exposes_closed_schemas_and_required_arguments() -> None:
    response = mcp_server.handle_request(_request("tools/list"))
    tools = {tool["name"]: tool for tool in response["result"]["tools"]}

    assert set(tools) == {"prepare_context", "expand_context", "search_graph", "context_status"}
    assert tools["prepare_context"]["inputSchema"]["required"] == ["repo", "task"]
    assert tools["prepare_context"]["inputSchema"]["additionalProperties"] is False
    assert tools["prepare_context"]["inputSchema"]["properties"]["graph_backend"] == {
        "type": "string",
        "enum": ["auto", "off", "graphify"],
        "default": "auto",
    }
    assert tools["search_graph"]["inputSchema"]["properties"]["hops"]["enum"] == [1, 2]
    assert tools["context_status"]["inputSchema"]["required"] == ["repo"]
    assert tools["expand_context"]["inputSchema"]["required"] == ["reason"]


def test_expand_context_returns_unseen_chunks_within_budget(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "def feature():\n    return 'primary'\n\n"
        "def helper():\n    return 'secondary evidence'\n",
        encoding="utf-8",
    )
    manifest = repo / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "repo": str(repo),
                "task": "Add secondary evidence to feature helper",
                "tokenizer": {"model": "gpt-4o-mini"},
                "selected_chunks": [],
                "context_plan": {"evidence_slots": [{"name": "implementation"}]},
            }
        ),
        encoding="utf-8",
    )

    result = mcp_server.expand_context(
        {"manifest_path": str(manifest), "reason": "helper implementation missing", "expand_budget": 300}
    )

    assert result["round"] == 1
    assert result["expanded_tokens"] <= 300
    assert result["selected_chunks"]
    assert "secondary evidence" in result["prompt_markdown"]


def test_expand_context_limits_handoff_to_three_rounds(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def feature():\n    return True\n", encoding="utf-8")
    manifest = repo / "manifest.json"
    manifest.write_text(
        json.dumps({"repo": str(repo), "task": "Change feature", "selected_chunks": []}),
        encoding="utf-8",
    )
    arguments = {"manifest_path": str(manifest), "reason": "more evidence", "expand_budget": 200}
    for _ in range(3):
        mcp_server.expand_context(arguments)

    try:
        mcp_server.expand_context(arguments)
    except mcp_server.ToolError as exc:
        assert "Maximum of 3" in str(exc)
    else:
        raise AssertionError("fourth expansion should fail")


def test_prepare_context_returns_compact_report_and_optional_prompt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[dict] = []

    def fake_build(repo, task, budget, counter, **kwargs):
        calls.append(
            {
                "repo": repo,
                "task": task,
                "budget": budget,
                "counter": counter,
                **kwargs,
            }
        )
        return "FULL SOURCE PROMPT", {
            "selected_chunks": [
                {
                    "id": "src/app.py#L1-L8",
                    "path": "src/app.py",
                    "start_line": 1,
                    "end_line": 8,
                    "tokens": 42,
                    "text": "source omitted from compact response",
                },
                {
                    "id": "src/app.py#L9-L12",
                    "path": "src/app.py",
                    "start_line": 9,
                    "end_line": 12,
                    "tokens": 20,
                },
            ],
            "change_surface": {"edit_targets": [{"path": "src/app.py"}]},
            "prompt_token_accounting": {"packed_prompt_tokens": 200},
            "graph": {"used": False},
            "context_sufficiency": {"status": "sufficient"},
            "handoff_ready": True,
        }

    monkeypatch.setattr(mcp_server, "build_context_package", fake_build)
    monkeypatch.setattr(mcp_server, "TokenCounter", lambda: object())

    compact = mcp_server.prepare_context(
        {
            "repo": str(tmp_path),
            "task": "Change app",
            "preset": "feature",
            "budget": 5000,
            "top_k": 12,
        }
    )

    assert compact["selected_paths"] == ["src/app.py"]
    assert compact["selected_chunks"][0] == {
        "id": "src/app.py#L1-L8",
        "path": "src/app.py",
        "start_line": 1,
        "end_line": 8,
        "tokens": 42,
    }
    assert "prompt" not in compact
    assert "text" not in compact["selected_chunks"][0]
    assert calls[0]["workflow"] == "feature"
    assert calls[0]["graph_backend"] == "auto"

    included = mcp_server.prepare_context(
        {"repo": str(tmp_path), "task": "Change app", "include_prompt": True}
    )
    assert included["prompt"] == "FULL SOURCE PROMPT"


def test_search_graph_uses_source_backed_graph_candidates(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src" / "service.py"
    source.parent.mkdir()
    source.write_text("def process_order():\n    return True\n", encoding="utf-8")
    graph = tmp_path / "graph.json"
    graph.write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "id": "process",
                        "label": "process_order",
                        "source_file": "src/service.py",
                        "source_location": "L1",
                    }
                ],
                "links": [],
            }
        ),
        encoding="utf-8",
    )

    result = mcp_server.search_graph(
        {
            "repo": str(tmp_path),
            "query": "process_order",
            "graph_json": str(graph),
            "hops": 1,
            "limit": 5,
        }
    )

    assert result["backend"] == "graphify"
    assert result["fallback_used"] is False
    assert result["candidates"][0]["source_path"] == "src/service.py"


def test_search_graph_falls_back_deterministically(
    tmp_path: Path,
    monkeypatch,
) -> None:
    first = tmp_path / "src" / "alpha.py"
    second = tmp_path / "src" / "beta.py"
    first.parent.mkdir()
    first.write_text("def target():\n    return 1\n", encoding="utf-8")
    second.write_text("def target_helper():\n    return 2\n", encoding="utf-8")
    monkeypatch.setattr(
        mcp_server,
        "run_graphify",
        lambda *_args, **_kwargs: SimpleNamespace(
            status="unavailable",
            reason="graphify_missing",
            graph_path=None,
        ),
    )
    monkeypatch.setattr(mcp_server, "source_files", lambda _repo: (second, first))

    result = mcp_server.search_graph(
        {"repo": str(tmp_path), "task": "target", "limit": 2}
    )

    assert result["backend"] == "lexical"
    assert result["graph_reason"] == "graphify_missing"
    assert [item["source_path"] for item in result["candidates"]] == [
        "src/alpha.py",
        "src/beta.py",
    ]
    assert all(
        Path(tmp_path / item["source_path"]).is_file() for item in result["candidates"]
    )


def test_tool_validation_returns_mcp_tool_error(tmp_path: Path) -> None:
    response = mcp_server.handle_request(
        _request(
            "tools/call",
            params={
                "name": "search_graph",
                "arguments": {"repo": str(tmp_path), "query": "x", "hops": 3},
            },
        )
    )

    result = response["result"]
    assert result["isError"] is True
    assert "hops must be one of" in json.loads(result["content"][0]["text"])["error"]


def test_context_status_reads_cached_state_without_building_index(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cache = tmp_path / "cached-index.json"
    cache.write_text(
        json.dumps(
            {
                "manifest": {
                    "revision": "abc",
                    "indexed_files": 3,
                    "indexed_at": "now",
                },
                "entries": {},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(mcp_server, "repository_index_path", lambda _repo: cache)
    monkeypatch.setattr(mcp_server, "_git_head", lambda _repo: "abc")
    monkeypatch.setattr(
        mcp_server,
        "_graphify_status",
        lambda: {"ready": False, "status": "missing"},
    )

    result = mcp_server.context_status({"repo": str(tmp_path)})

    assert result["repo"]["ready"] is True
    assert result["index"] == {
        "ready": True,
        "path": str(cache),
        "status": "fresh",
        "revision": "abc",
        "indexed_files": 3,
        "indexed_at": "now",
    }
    assert result["graphify"]["status"] == "missing"


def test_stdio_server_uses_newline_delimited_json_and_recovers_from_parse_error() -> None:
    input_stream = io.StringIO(
        "not json\n"
        + json.dumps(_request("ping", request_id=2))
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
        + "\n"
    )
    output_stream = io.StringIO()

    mcp_server.run_stdio_server(input_stream, output_stream)

    lines = output_stream.getvalue().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["error"]["code"] == -32700
    assert json.loads(lines[1]) == {"jsonrpc": "2.0", "id": 2, "result": {}}
