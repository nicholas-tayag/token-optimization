from __future__ import annotations

import json
import hashlib
import html
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
ANNOTATION_LABELS = {
    "agent_succeeded",
    "agent_failed",
    "context_missing",
    "wrong_file_selected",
    "tests_passed",
    "tests_failed",
}
_SUCCESS_LABELS = {"agent_succeeded", "tests_passed"}
_FAILURE_LABELS = {"agent_failed", "context_missing", "wrong_file_selected", "tests_failed"}


@dataclass(frozen=True)
class TraceRecord:
    trace_id: str
    task: str
    repo_path: str
    workflow: str
    created_at: str
    status: str
    full_scan_prompt_tokens: int
    packed_prompt_tokens: int
    tokens_saved: int
    reduction_percent: float
    selected_file_count: int
    quality_status: str


def default_observability_db(repo: Path | None = None) -> Path:
    root = Path(repo or ".").resolve()
    return root / ".agenvantage" / "observability.db"


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def init_observability_store(db_path: Path) -> Path:
    with _connect(db_path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS traces (
                trace_id TEXT PRIMARY KEY,
                task TEXT NOT NULL,
                repo_path TEXT NOT NULL,
                workflow TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL,
                full_scan_prompt_tokens INTEGER NOT NULL DEFAULT 0,
                packed_prompt_tokens INTEGER NOT NULL DEFAULT 0,
                tokens_saved INTEGER NOT NULL DEFAULT 0,
                reduction_percent REAL NOT NULL DEFAULT 0,
                selected_file_count INTEGER NOT NULL DEFAULT 0,
                quality_status TEXT NOT NULL DEFAULT 'unknown',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS spans (
                span_id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL,
                parent_span_id TEXT,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                started_at REAL NOT NULL,
                ended_at REAL NOT NULL,
                duration_ms REAL NOT NULL,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                estimated_cost REAL NOT NULL DEFAULT 0,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(trace_id) REFERENCES traces(trace_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS artifacts (
                artifact_id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                path TEXT,
                content TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(trace_id) REFERENCES traces(trace_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS annotations (
                annotation_id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL,
                label TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(trace_id) REFERENCES traces(trace_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS provider_usage (
                usage_id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT 'unknown',
                model TEXT NOT NULL DEFAULT '',
                request_id TEXT NOT NULL DEFAULT '',
                policy_id TEXT NOT NULL DEFAULT '',
                input_tokens INTEGER NOT NULL DEFAULT 0,
                cached_input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                request_cost_usd REAL NOT NULL DEFAULT 0,
                latency_ms REAL NOT NULL DEFAULT 0,
                reconciliation_status TEXT NOT NULL DEFAULT 'unreconciled',
                imported_at TEXT NOT NULL,
                raw_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(trace_id) REFERENCES traces(trace_id)
            )
            """
        )
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
    return db_path


def _trace_id(task: str, repo_path: Path, created_ns: int) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "_" for ch in task.strip())[:32]
    slug = "_".join(part for part in slug.split("_") if part) or "task"
    repo_digest = hashlib.sha1(str(repo_path.resolve()).encode("utf-8")).hexdigest()[:6]
    return f"trace_{created_ns}_{slug}_{repo_digest}"


def record_pack_trace(
    db_path: Path,
    *,
    markdown: str,
    report: dict[str, Any],
    repo_path: Path,
    workflow: str,
    artifact_paths: dict[str, Path | None] | None = None,
) -> TraceRecord:
    init_observability_store(db_path)
    created_ns = time.time_ns()
    created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(created_ns / 1_000_000_000))
    trace_id = _trace_id(str(report.get("task") or "task"), repo_path, created_ns)
    accounting = report.get("prompt_token_accounting") or {}
    full_tokens = int(accounting.get("full_scan_prompt_tokens") or report.get("candidate_context_tokens") or 0)
    packed_tokens = int(accounting.get("packed_prompt_tokens") or report.get("selected_context_tokens") or 0)
    saved = int(accounting.get("prompt_tokens_saved_vs_full_scan") or (full_tokens - packed_tokens))
    reduction = float(accounting.get("prompt_reduction_percent_vs_full_scan") or 0.0)
    selected_chunks = report.get("selected_chunks") or []
    selected_files = sorted(
        {str(chunk.get("path")) for chunk in selected_chunks if isinstance(chunk, dict) and chunk.get("path")}
    )
    missing_signals = []
    change_surface = report.get("change_surface") or {}
    if isinstance(change_surface, dict):
        missing_signals = [str(item) for item in change_surface.get("missing_signals", [])]
    quality_status = "warning" if missing_signals else "unverified"
    metadata = {
        "preset": report.get("preset"),
        "repo_count": report.get("repo_count"),
        "selected_files": selected_files,
        "missing_signals": missing_signals,
        "covered_query_terms": report.get("covered_query_terms", []),
        "uncovered_query_terms": report.get("uncovered_query_terms", []),
        "multimodal": report.get("multimodal", {}),
    }
    started = time.time()
    with _connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO traces(
                trace_id, task, repo_path, workflow, created_at, status,
                full_scan_prompt_tokens, packed_prompt_tokens, tokens_saved,
                reduction_percent, selected_file_count, quality_status, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trace_id,
                str(report.get("task") or ""),
                str(repo_path.resolve()),
                workflow,
                created_at,
                "completed",
                full_tokens,
                packed_tokens,
                saved,
                reduction,
                len(selected_files),
                quality_status,
                json.dumps(metadata, sort_keys=True),
            ),
        )
        connection.execute(
            """
            INSERT INTO spans(
                span_id, trace_id, parent_span_id, name, kind, started_at, ended_at,
                duration_ms, input_tokens, output_tokens, estimated_cost, metadata_json
            ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, 0, 0, ?)
            """,
            (
                f"{trace_id}:context.pack",
                trace_id,
                "Context pack",
                "context.pack",
                started,
                time.time(),
                max((time.time() - started) * 1000, 0.0),
                packed_tokens,
                json.dumps({"selected_chunk_count": len(selected_chunks)}, sort_keys=True),
            ),
        )
        connection.execute(
            """
            INSERT INTO artifacts(artifact_id, trace_id, kind, path, content, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                f"{trace_id}:markdown",
                trace_id,
                "context_markdown",
                str((artifact_paths or {}).get("markdown") or ""),
                markdown,
                "{}",
            ),
        )
        connection.execute(
            """
            INSERT INTO artifacts(artifact_id, trace_id, kind, path, content, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                f"{trace_id}:manifest",
                trace_id,
                "decision_manifest",
                str((artifact_paths or {}).get("manifest") or ""),
                json.dumps(report, indent=2),
                "{}",
            ),
        )
    return TraceRecord(
        trace_id=trace_id,
        task=str(report.get("task") or ""),
        repo_path=str(repo_path.resolve()),
        workflow=workflow,
        created_at=created_at,
        status="completed",
        full_scan_prompt_tokens=full_tokens,
        packed_prompt_tokens=packed_tokens,
        tokens_saved=saved,
        reduction_percent=reduction,
        selected_file_count=len(selected_files),
        quality_status=quality_status,
    )


def seed_demo_trace(db_path: Path, *, repo_path: Path | None = None) -> TraceRecord:
    repo = Path(repo_path or ".").resolve()
    report = {
        "task": "Demo: add upload limit smoke-test coverage",
        "preset": "feature",
        "repo_count": 1,
        "prompt_token_accounting": {
            "original_user_prompt_tokens": 8,
            "full_scan_prompt_tokens": 40797,
            "packed_prompt_tokens": 5630,
            "prompt_tokens_saved_vs_full_scan": 35167,
            "prompt_reduction_percent_vs_full_scan": 86.2,
        },
        "selected_chunks": [
            {"id": "src/server.py#L20-L80", "path": "src/server.py"},
            {"id": "tests/test_upload_limits.py#L1-L90", "path": "tests/test_upload_limits.py"},
            {"id": "pyproject.toml#L1-L40", "path": "pyproject.toml"},
        ],
        "change_surface": {
            "edit_targets": [{"path": "src/server.py"}],
            "test_targets": [{"path": "tests/test_upload_limits.py"}],
            "config_targets": [{"path": "pyproject.toml"}],
            "supporting_targets": [{"path": "src/upload_config.py"}],
            "missing_signals": [],
        },
        "covered_query_terms": ["upload", "limit", "smoke", "test"],
        "uncovered_query_terms": [],
    }
    markdown = (
        "# AgenVantage Context Package\n\n"
        "## Task\n\n"
        "Demo: add upload limit smoke-test coverage\n\n"
        "## Selected Repository Context\n\n"
        "[SOURCE:src/server.py#L20-L80]\n"
        "```python\n"
        "def upload_file(request):\n"
        "    return enforce_upload_limit(request)\n"
        "```\n\n"
        "[SOURCE:tests/test_upload_limits.py#L1-L90]\n"
        "```python\n"
        "def test_upload_limit_rejects_large_file():\n"
        "    assert True\n"
        "```\n"
    )
    trace = record_pack_trace(
        db_path,
        markdown=markdown,
        report=report,
        repo_path=repo,
        workflow="demo",
    )
    record_trace_span(
        db_path,
        trace.trace_id,
        name="Demo external agent",
        kind="agent.external",
        duration_ms=842.0,
        input_tokens=trace.packed_prompt_tokens,
        metadata={
            "command": ["demo-agent", "implement-upload-limit-test"],
            "exit_code": 0,
            "stdin_handoff": True,
        },
    )
    record_trace_artifact(
        db_path,
        trace.trace_id,
        kind="agent_stdout",
        content=(
            "Demo agent plan:\n"
            "- Edit src/server.py to enforce upload size.\n"
            "- Add tests/test_upload_limits.py coverage.\n"
            "- Run targeted upload smoke tests.\n"
        ),
        metadata={"demo": True},
    )
    annotate_trace(
        db_path,
        trace.trace_id,
        label="agent_succeeded",
        note="Demo trace: context was sufficient for a plausible upload-limit test task.",
    )
    import_provider_usage_records(
        db_path,
        [
            {
                "trace_id": trace.trace_id,
                "request_id": "demo_provider_usage",
                "provider": "openai",
                "model": "gpt-demo",
                "input_tokens": 5630,
                "cached_input_tokens": 0,
                "output_tokens": 420,
                "request_cost_usd": 0.0,
                "latency_ms": 842.0,
            }
        ],
        provider="openai",
        reconciliation_status="estimated",
    )
    return trace


def list_traces(db_path: Path, *, limit: int = 20) -> list[TraceRecord]:
    init_observability_store(db_path)
    with _connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT * FROM traces
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        TraceRecord(
            trace_id=str(row["trace_id"]),
            task=str(row["task"]),
            repo_path=str(row["repo_path"]),
            workflow=str(row["workflow"]),
            created_at=str(row["created_at"]),
            status=str(row["status"]),
            full_scan_prompt_tokens=int(row["full_scan_prompt_tokens"]),
            packed_prompt_tokens=int(row["packed_prompt_tokens"]),
            tokens_saved=int(row["tokens_saved"]),
            reduction_percent=float(row["reduction_percent"]),
            selected_file_count=int(row["selected_file_count"]),
            quality_status=str(row["quality_status"]),
        )
        for row in rows
    ]


def load_trace(db_path: Path, trace_id: str, *, include_artifact_content: bool = False) -> dict[str, Any]:
    init_observability_store(db_path)
    with _connect(db_path) as connection:
        trace = connection.execute(
            "SELECT * FROM traces WHERE trace_id = ?",
            (trace_id,),
        ).fetchone()
        if trace is None:
            raise KeyError(trace_id)
        spans = connection.execute(
            "SELECT * FROM spans WHERE trace_id = ? ORDER BY started_at",
            (trace_id,),
        ).fetchall()
        artifact_columns = "artifact_id, kind, path, metadata_json"
        if include_artifact_content:
            artifact_columns += ", content"
        artifacts = connection.execute(
            f"SELECT {artifact_columns} FROM artifacts WHERE trace_id = ? ORDER BY kind",
            (trace_id,),
        ).fetchall()
        annotations = connection.execute(
            "SELECT * FROM annotations WHERE trace_id = ? ORDER BY created_at",
            (trace_id,),
        ).fetchall()
        provider_usage = connection.execute(
            "SELECT * FROM provider_usage WHERE trace_id = ? ORDER BY imported_at, usage_id",
            (trace_id,),
        ).fetchall()
    payload = dict(trace)
    payload["metadata"] = json.loads(payload.pop("metadata_json") or "{}")
    payload["spans"] = [dict(row) for row in spans]
    for span in payload["spans"]:
        span["metadata"] = json.loads(span.pop("metadata_json") or "{}")
    payload["artifacts"] = [dict(row) for row in artifacts]
    for artifact in payload["artifacts"]:
        artifact["metadata"] = json.loads(artifact.pop("metadata_json") or "{}")
    payload["annotations"] = [dict(row) for row in annotations]
    for annotation in payload["annotations"]:
        annotation["metadata"] = json.loads(annotation.pop("metadata_json") or "{}")
    payload["provider_usage"] = [dict(row) for row in provider_usage]
    for usage in payload["provider_usage"]:
        usage["raw"] = json.loads(usage.pop("raw_json") or "{}")
    return payload


def load_trace_artifact(
    db_path: Path,
    trace_id: str,
    *,
    kind: str | None = None,
    artifact_id: str | None = None,
) -> dict[str, Any]:
    init_observability_store(db_path)
    if kind is None and artifact_id is None:
        raise ValueError("load_trace_artifact requires kind or artifact_id.")
    with _connect(db_path) as connection:
        if artifact_id is not None:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE trace_id = ? AND artifact_id = ?",
                (trace_id, artifact_id),
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT * FROM artifacts
                WHERE trace_id = ? AND kind = ?
                ORDER BY artifact_id
                LIMIT 1
                """,
                (trace_id, kind),
            ).fetchone()
    if row is None:
        raise KeyError(artifact_id or kind or "")
    artifact = dict(row)
    artifact["metadata"] = json.loads(artifact.pop("metadata_json") or "{}")
    return artifact


def _int_value(value: Any) -> int:
    if value in (None, ""):
        return 0
    return int(float(value))


def _float_value(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    return float(value)


def import_provider_usage_records(
    db_path: Path,
    records: list[dict[str, Any]],
    *,
    trace_id: str | None = None,
    provider: str = "unknown",
    reconciliation_status: str = "unreconciled",
) -> dict[str, Any]:
    init_observability_store(db_path)
    if not records:
        return {
            "imported_count": 0,
            "trace_ids": [],
            "total_input_tokens": 0,
            "total_cached_input_tokens": 0,
            "total_output_tokens": 0,
            "total_request_cost_usd": 0.0,
        }
    imported_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    imported_count = 0
    trace_ids: set[str] = set()
    total_input_tokens = 0
    total_cached_input_tokens = 0
    total_output_tokens = 0
    total_request_cost = 0.0
    with _connect(db_path) as connection:
        for index, record in enumerate(records):
            resolved_trace_id = str(record.get("trace_id") or trace_id or "").strip()
            if not resolved_trace_id:
                raise ValueError("Provider usage import requires --trace-id or per-record trace_id.")
            trace = connection.execute(
                "SELECT trace_id FROM traces WHERE trace_id = ?",
                (resolved_trace_id,),
            ).fetchone()
            if trace is None:
                raise KeyError(resolved_trace_id)
            record_provider = str(record.get("provider") or provider or "unknown")
            model = str(record.get("model") or record.get("gen_ai.response.model") or "")
            request_id = str(
                record.get("request_id")
                or record.get("response_id")
                or record.get("id")
                or record.get("gen_ai.response.id")
                or ""
            )
            policy_id = str(record.get("policy_id") or "")
            input_tokens = _int_value(record.get("input_tokens"))
            cached_input_tokens = _int_value(record.get("cached_input_tokens"))
            output_tokens = _int_value(record.get("output_tokens"))
            request_cost = _float_value(record.get("request_cost_usd"))
            latency_ms = _float_value(record.get("latency_ms"))
            fingerprint = hashlib.sha1(
                json.dumps(record, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()[:12]
            usage_id = f"{resolved_trace_id}:provider_usage:{request_id or index}:{fingerprint}"
            connection.execute(
                """
                INSERT OR REPLACE INTO provider_usage(
                    usage_id, trace_id, provider, model, request_id, policy_id,
                    input_tokens, cached_input_tokens, output_tokens,
                    request_cost_usd, latency_ms, reconciliation_status,
                    imported_at, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    usage_id,
                    resolved_trace_id,
                    record_provider,
                    model,
                    request_id,
                    policy_id,
                    input_tokens,
                    cached_input_tokens,
                    output_tokens,
                    request_cost,
                    latency_ms,
                    reconciliation_status,
                    imported_at,
                    json.dumps(record, sort_keys=True, default=str),
                ),
            )
            imported_count += 1
            trace_ids.add(resolved_trace_id)
            total_input_tokens += input_tokens
            total_cached_input_tokens += cached_input_tokens
            total_output_tokens += output_tokens
            total_request_cost += request_cost
    return {
        "imported_count": imported_count,
        "trace_ids": sorted(trace_ids),
        "total_input_tokens": total_input_tokens,
        "total_cached_input_tokens": total_cached_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_request_cost_usd": round(total_request_cost, 8),
        "reconciliation_status": reconciliation_status,
    }


def _quality_status_from_label(label: str) -> str:
    if label in _FAILURE_LABELS:
        return "failed"
    if label in _SUCCESS_LABELS:
        return "passed"
    return "annotated"


def annotate_trace(
    db_path: Path,
    trace_id: str,
    *,
    label: str,
    note: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    init_observability_store(db_path)
    if label not in ANNOTATION_LABELS:
        allowed = ", ".join(sorted(ANNOTATION_LABELS))
        raise ValueError(f"Unsupported annotation label: {label}. Expected one of: {allowed}")
    created_ns = time.time_ns()
    created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(created_ns / 1_000_000_000))
    annotation_id = f"{trace_id}:annotation:{created_ns}"
    quality_status = _quality_status_from_label(label)
    with _connect(db_path) as connection:
        trace = connection.execute(
            "SELECT trace_id FROM traces WHERE trace_id = ?",
            (trace_id,),
        ).fetchone()
        if trace is None:
            raise KeyError(trace_id)
        connection.execute(
            """
            INSERT INTO annotations(annotation_id, trace_id, label, note, created_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                annotation_id,
                trace_id,
                label,
                note,
                created_at,
                json.dumps(metadata or {}, sort_keys=True),
            ),
        )
        connection.execute(
            "UPDATE traces SET quality_status = ? WHERE trace_id = ?",
            (quality_status, trace_id),
        )
    return {
        "annotation_id": annotation_id,
        "trace_id": trace_id,
        "label": label,
        "note": note,
        "created_at": created_at,
        "metadata": metadata or {},
        "quality_status": quality_status,
    }


def _load_dashboard_traces(db_path: Path, *, limit: int = 100) -> list[dict[str, Any]]:
    traces = list_traces(db_path, limit=limit)
    return [load_trace(db_path, trace.trace_id) for trace in traces]


def _fmt_int(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def _fmt_float(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _dashboard_summary(traces: list[dict[str, Any]]) -> dict[str, Any]:
    if not traces:
        return {
            "trace_count": 0,
            "total_tokens_saved": 0,
            "median_reduction_percent": 0.0,
            "median_packed_tokens": 0,
            "warning_count": 0,
            "failed_count": 0,
            "annotated_count": 0,
            "provider_usage_count": 0,
            "provider_reported_cost_usd": 0.0,
        }
    reductions = sorted(float(trace.get("reduction_percent") or 0.0) for trace in traces)
    packed = sorted(int(trace.get("packed_prompt_tokens") or 0) for trace in traces)
    midpoint = len(traces) // 2
    if len(traces) % 2:
        median_reduction = reductions[midpoint]
        median_packed = packed[midpoint]
    else:
        median_reduction = (reductions[midpoint - 1] + reductions[midpoint]) / 2
        median_packed = int((packed[midpoint - 1] + packed[midpoint]) / 2)
    return {
        "trace_count": len(traces),
        "total_tokens_saved": sum(int(trace.get("tokens_saved") or 0) for trace in traces),
        "median_reduction_percent": round(median_reduction, 2),
        "median_packed_tokens": median_packed,
        "warning_count": sum(
            1 for trace in traces if (trace.get("metadata") or {}).get("missing_signals")
        ),
        "failed_count": sum(1 for trace in traces if trace.get("quality_status") == "failed"),
        "annotated_count": sum(1 for trace in traces if trace.get("annotations")),
        "provider_usage_count": sum(len(trace.get("provider_usage") or []) for trace in traces),
        "provider_reported_cost_usd": round(
            sum(
                float(usage.get("request_cost_usd") or 0.0)
                for trace in traces
                for usage in (trace.get("provider_usage") or [])
            ),
            8,
        ),
    }


def render_observability_dashboard(db_path: Path, *, limit: int = 100) -> str:
    traces = _load_dashboard_traces(db_path, limit=limit)
    summary = _dashboard_summary(traces)
    trace_cards = []
    for trace in traces:
        metadata = trace.get("metadata") or {}
        selected_files = metadata.get("selected_files") or []
        missing = metadata.get("missing_signals") or []
        spans = trace.get("spans") or []
        annotations = trace.get("annotations") or []
        provider_usage = trace.get("provider_usage") or []
        selected_html = (
            "".join(f"<li>{html.escape(str(path))}</li>" for path in selected_files)
            if selected_files
            else "<li class=\"muted\">No selected files recorded.</li>"
        )
        warnings_html = (
            "".join(f"<li>{html.escape(str(item))}</li>" for item in missing)
            if missing
            else "<li class=\"muted\">No missing-signal warnings.</li>"
        )
        spans_html = (
            "".join(
                "<li>"
                f"<span>{html.escape(str(span.get('kind', 'span')))}</span>"
                f"<strong>{_fmt_float(span.get('duration_ms'))} ms</strong>"
                f"<em>{_fmt_int(span.get('input_tokens'))} input tokens</em>"
                "</li>"
                for span in spans
            )
            if spans
            else "<li class=\"muted\">No spans recorded.</li>"
        )
        annotations_html = (
            "".join(
                "<li>"
                f"<strong>{html.escape(str(annotation.get('label', 'annotation')).replace('_', ' '))}</strong>"
                f"<span>{html.escape(str(annotation.get('note') or 'No note.'))}</span>"
                f"<em>{html.escape(str(annotation.get('created_at', '')))}</em>"
                "</li>"
                for annotation in annotations
            )
            if annotations
            else "<li class=\"muted\">No user quality labels yet.</li>"
        )
        provider_usage_html = (
            "".join(
                "<li>"
                f"<strong>{html.escape(str(usage.get('provider', 'provider')))}</strong>"
                f"<span>{_fmt_int(usage.get('input_tokens'))} in / "
                f"{_fmt_int(usage.get('cached_input_tokens'))} cached / "
                f"{_fmt_int(usage.get('output_tokens'))} out</span>"
                f"<em>${_fmt_float(usage.get('request_cost_usd'))} "
                f"{html.escape(str(usage.get('reconciliation_status', 'unreconciled')))}</em>"
                "</li>"
                for usage in provider_usage
            )
            if provider_usage
            else "<li class=\"muted\">No provider-reported usage imported.</li>"
        )
        trace_cards.append(
            f"""
            <article class="trace-card">
              <div class="trace-topline">
                <div>
                  <h3>{html.escape(str(trace.get('task', 'Untitled task')))}</h3>
                  <p>{html.escape(str(trace.get('trace_id', '')))}</p>
                </div>
                <span class="status">{html.escape(str(trace.get('quality_status', 'unknown')))}</span>
              </div>
              <div class="trace-metrics">
                <div><strong>{_fmt_int(trace.get('full_scan_prompt_tokens'))}</strong><span>full scan</span></div>
                <div><strong>{_fmt_int(trace.get('packed_prompt_tokens'))}</strong><span>packed</span></div>
                <div><strong>{_fmt_int(trace.get('tokens_saved'))}</strong><span>tokens saved</span></div>
                <div><strong>{_fmt_float(trace.get('reduction_percent'))}%</strong><span>reduction</span></div>
              </div>
              <details open>
                <summary>Selected files</summary>
                <ul>{selected_html}</ul>
              </details>
              <details>
                <summary>Warnings</summary>
                <ul>{warnings_html}</ul>
              </details>
              <details>
                <summary>Spans</summary>
                <ul class="spans">{spans_html}</ul>
              </details>
              <details>
                <summary>User quality labels</summary>
                <ul class="annotations">{annotations_html}</ul>
              </details>
              <details>
                <summary>Provider-reported usage</summary>
                <ul class="provider-usage">{provider_usage_html}</ul>
              </details>
            </article>
            """
        )
    cards_html = "\n".join(trace_cards) if trace_cards else (
        "<section class=\"empty\"><h2>No traces yet</h2>"
        "<p>Run <code>agenvantage observe pack --task \"Add tests for upload limits\"</code> "
        "from a repository to create your first trace.</p></section>"
    )
    return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>AgenVantage Observability</title>
    <style>
      :root {{
        color-scheme: light;
        --ink: #14213d;
        --muted: #64748b;
        --bg: #f5f1e8;
        --panel: #fffaf0;
        --line: #dfd4bd;
        --accent: #0f766e;
        --accent-2: #b45309;
        --danger: #b91c1c;
        font-family: ui-sans-serif, "Avenir Next", "Helvetica Neue", sans-serif;
      }}
      body {{
        margin: 0;
        color: var(--ink);
        background:
          radial-gradient(circle at top left, rgba(15, 118, 110, 0.16), transparent 28rem),
          linear-gradient(135deg, #f5f1e8 0%, #ede4d1 100%);
      }}
      header {{
        padding: 2.25rem clamp(1rem, 4vw, 4rem) 1.5rem;
      }}
      header h1 {{
        margin: 0;
        font-size: clamp(2rem, 5vw, 4.5rem);
        letter-spacing: -0.06em;
        line-height: 0.95;
      }}
      header p {{
        max-width: 48rem;
        color: var(--muted);
        font-size: 1.05rem;
      }}
      main {{
        padding: 0 clamp(1rem, 4vw, 4rem) 4rem;
      }}
      .stats {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        gap: 1rem;
        margin: 1.25rem 0 1.75rem;
      }}
      .stat, .trace-card, .empty {{
        background: rgba(255, 250, 240, 0.88);
        border: 1px solid var(--line);
        border-radius: 20px;
        box-shadow: 0 18px 50px rgba(80, 61, 31, 0.08);
      }}
      .stat {{
        padding: 1.15rem;
      }}
      .stat strong {{
        display: block;
        font-size: 2rem;
        letter-spacing: -0.04em;
      }}
      .stat span, .trace-topline p, .trace-metrics span, .muted {{
        color: var(--muted);
      }}
      .trace-grid {{
        display: grid;
        gap: 1rem;
      }}
      .trace-card {{
        padding: 1.15rem;
      }}
      .trace-topline {{
        display: flex;
        justify-content: space-between;
        gap: 1rem;
        align-items: flex-start;
      }}
      h3 {{
        margin: 0;
        font-size: 1.15rem;
      }}
      .trace-topline p {{
        margin: 0.2rem 0 0;
        font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        font-size: 0.78rem;
      }}
      .status {{
        border: 1px solid rgba(15, 118, 110, 0.25);
        color: var(--accent);
        border-radius: 999px;
        padding: 0.18rem 0.55rem;
        font-size: 0.78rem;
        white-space: nowrap;
      }}
      .trace-metrics {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
        gap: 0.75rem;
        margin: 1rem 0;
      }}
      .trace-metrics div {{
        border-left: 3px solid var(--accent);
        padding-left: 0.65rem;
      }}
      .trace-metrics strong {{
        display: block;
        font-size: 1.3rem;
      }}
      details {{
        border-top: 1px solid var(--line);
        padding: 0.65rem 0;
      }}
      summary {{
        cursor: pointer;
        font-weight: 700;
      }}
      ul {{
        margin: 0.55rem 0 0;
        padding-left: 1.2rem;
      }}
      .spans li {{
        display: grid;
        grid-template-columns: 1fr auto auto;
        gap: 0.8rem;
        padding: 0.25rem 0;
      }}
      .annotations li {{
        display: grid;
        grid-template-columns: 12rem 1fr auto;
        gap: 0.8rem;
        padding: 0.25rem 0;
      }}
      .provider-usage li {{
        display: grid;
        grid-template-columns: 8rem 1fr auto;
        gap: 0.8rem;
        padding: 0.25rem 0;
      }}
      code {{
        background: #eee2c8;
        border-radius: 6px;
        padding: 0.12rem 0.32rem;
      }}
      .empty {{
        padding: 1.5rem;
      }}
    </style>
  </head>
  <body>
    <header>
      <h1>Agent Observability</h1>
      <p>Local Datadog-style visibility for AI coding-agent tasks: traces, spans,
      context selection, token savings, and missing-signal warnings.</p>
      <p><strong>Database:</strong> {html.escape(str(db_path.resolve()))}</p>
    </header>
    <main>
      <section class="stats">
        <div class="stat"><strong>{_fmt_int(summary['trace_count'])}</strong><span>traces</span></div>
        <div class="stat"><strong>{_fmt_int(summary['total_tokens_saved'])}</strong><span>tokens saved</span></div>
        <div class="stat"><strong>{_fmt_float(summary['median_reduction_percent'])}%</strong><span>median reduction</span></div>
        <div class="stat"><strong>{_fmt_int(summary['median_packed_tokens'])}</strong><span>median packed tokens</span></div>
        <div class="stat"><strong>{_fmt_int(summary['warning_count'])}</strong><span>traces with warnings</span></div>
        <div class="stat"><strong>{_fmt_int(summary['failed_count'])}</strong><span>failed quality labels</span></div>
        <div class="stat"><strong>{_fmt_int(summary['annotated_count'])}</strong><span>annotated traces</span></div>
        <div class="stat"><strong>{_fmt_int(summary['provider_usage_count'])}</strong><span>provider usage records</span></div>
        <div class="stat"><strong>${_fmt_float(summary['provider_reported_cost_usd'])}</strong><span>provider reported cost</span></div>
      </section>
      <section class="trace-grid">
        {cards_html}
      </section>
    </main>
  </body>
</html>
"""


def write_observability_dashboard(
    db_path: Path,
    output_path: Path,
    *,
    limit: int = 100,
) -> Path:
    init_observability_store(db_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_observability_dashboard(db_path, limit=limit), encoding="utf-8")
    return output_path


def record_trace_artifact(
    db_path: Path,
    trace_id: str,
    *,
    kind: str,
    content: str,
    path: Path | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    init_observability_store(db_path)
    artifact_id = f"{trace_id}:{kind}:{hashlib.sha1(content.encode('utf-8')).hexdigest()[:10]}"
    with _connect(db_path) as connection:
        trace = connection.execute(
            "SELECT trace_id FROM traces WHERE trace_id = ?",
            (trace_id,),
        ).fetchone()
        if trace is None:
            raise KeyError(trace_id)
        connection.execute(
            """
            INSERT OR REPLACE INTO artifacts(artifact_id, trace_id, kind, path, content, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                trace_id,
                kind,
                str(path or ""),
                content,
                json.dumps(metadata or {}, sort_keys=True),
            ),
        )
    return artifact_id


def record_trace_span(
    db_path: Path,
    trace_id: str,
    *,
    name: str,
    kind: str,
    duration_ms: float = 0.0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    estimated_cost: float = 0.0,
    metadata: dict[str, Any] | None = None,
) -> str:
    init_observability_store(db_path)
    created = time.time()
    span_id = f"{trace_id}:{kind}:{time.time_ns()}"
    with _connect(db_path) as connection:
        trace = connection.execute(
            "SELECT trace_id FROM traces WHERE trace_id = ?",
            (trace_id,),
        ).fetchone()
        if trace is None:
            raise KeyError(trace_id)
        connection.execute(
            """
            INSERT INTO spans(
                span_id, trace_id, parent_span_id, name, kind, started_at, ended_at,
                duration_ms, input_tokens, output_tokens, estimated_cost, metadata_json
            ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                span_id,
                trace_id,
                name,
                kind,
                created,
                created + (max(duration_ms, 0.0) / 1000),
                max(duration_ms, 0.0),
                input_tokens,
                output_tokens,
                estimated_cost,
                json.dumps(metadata or {}, sort_keys=True),
            ),
        )
    return span_id
