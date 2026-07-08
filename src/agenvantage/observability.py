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


def list_traces(
    db_path: Path,
    *,
    limit: int = 20,
    quality_status: str | None = None,
    workflow: str | None = None,
    min_tokens_saved: int | None = None,
    attention_only: bool = False,
) -> list[TraceRecord]:
    init_observability_store(db_path)
    predicates = []
    params: list[Any] = []
    if quality_status:
        predicates.append("quality_status = ?")
        params.append(quality_status)
    if workflow:
        predicates.append("workflow = ?")
        params.append(workflow)
    if min_tokens_saved is not None:
        predicates.append("tokens_saved >= ?")
        params.append(min_tokens_saved)
    if attention_only:
        predicates.append(
            """
            quality_status IN ('failed', 'warning', 'unknown', 'unverified')
            """
        )
    where_clause = f"WHERE {' AND '.join(predicates)}" if predicates else ""
    params.append(limit)
    with _connect(db_path) as connection:
        rows = connection.execute(
            f"""
            SELECT * FROM traces
            {where_clause}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            params,
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


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
    return ordered[index]


def _workflow_breakdown(traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for trace in traces:
        workflow = str(trace.get("workflow") or "unknown")
        bucket = grouped.setdefault(
            workflow,
            {
                "workflow": workflow,
                "trace_count": 0,
                "tokens_saved": 0,
                "packed_tokens": [],
                "reduction_percent": [],
                "warning_count": 0,
                "failed_count": 0,
            },
        )
        bucket["trace_count"] += 1
        bucket["tokens_saved"] += int(trace.get("tokens_saved") or 0)
        bucket["packed_tokens"].append(float(trace.get("packed_prompt_tokens") or 0))
        bucket["reduction_percent"].append(float(trace.get("reduction_percent") or 0))
        if (trace.get("metadata") or {}).get("missing_signals"):
            bucket["warning_count"] += 1
        if trace.get("quality_status") == "failed":
            bucket["failed_count"] += 1
    rows = []
    for bucket in grouped.values():
        rows.append(
            {
                "workflow": bucket["workflow"],
                "trace_count": bucket["trace_count"],
                "tokens_saved": bucket["tokens_saved"],
                "median_packed_tokens": int(_median(bucket["packed_tokens"])),
                "median_reduction_percent": round(_median(bucket["reduction_percent"]), 2),
                "warning_count": bucket["warning_count"],
                "failed_count": bucket["failed_count"],
            }
        )
    return sorted(rows, key=lambda item: (-int(item["trace_count"]), str(item["workflow"])))


def _dashboard_action_items(traces: list[dict[str, Any]], *, limit: int = 8) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for trace in traces:
        metadata = trace.get("metadata") or {}
        missing = metadata.get("missing_signals") or []
        quality_status = str(trace.get("quality_status") or "unknown")
        annotations = trace.get("annotations") or []
        if quality_status == "failed":
            items.append(
                {
                    "severity": "critical",
                    "title": "Failed quality label",
                    "trace_id": trace.get("trace_id"),
                    "task": trace.get("task"),
                    "detail": "Review selected files and rerun with a narrower task or more context.",
                }
            )
        if missing:
            items.append(
                {
                    "severity": "warning",
                    "title": "Missing context signals",
                    "trace_id": trace.get("trace_id"),
                    "task": trace.get("task"),
                    "detail": "; ".join(str(item) for item in missing[:2]),
                }
            )
        if quality_status in {"unknown", "unverified"} and not annotations:
            items.append(
                {
                    "severity": "info",
                    "title": "Needs quality label",
                    "trace_id": trace.get("trace_id"),
                    "task": trace.get("task"),
                    "detail": "Annotate whether the packed context was sufficient after the agent run.",
                }
            )
    severity_rank = {"critical": 0, "warning": 1, "info": 2}
    return sorted(items, key=lambda item: severity_rank.get(str(item["severity"]), 9))[:limit]


def _dashboard_summary(traces: list[dict[str, Any]]) -> dict[str, Any]:
    if not traces:
        return {
            "trace_count": 0,
            "total_tokens_saved": 0,
            "median_reduction_percent": 0.0,
            "median_packed_tokens": 0,
            "p95_packed_tokens": 0,
            "warning_count": 0,
            "failed_count": 0,
            "annotated_count": 0,
            "provider_usage_count": 0,
            "provider_reported_cost_usd": 0.0,
            "provider_input_tokens": 0,
            "provider_cached_input_tokens": 0,
            "cache_hit_rate_percent": 0.0,
            "total_span_duration_ms": 0.0,
            "p95_span_duration_ms": 0.0,
            "workflow_breakdown": [],
            "action_items": [],
        }
    reductions = [float(trace.get("reduction_percent") or 0.0) for trace in traces]
    packed = [float(trace.get("packed_prompt_tokens") or 0) for trace in traces]
    provider_input_tokens = sum(
        int(usage.get("input_tokens") or 0)
        for trace in traces
        for usage in (trace.get("provider_usage") or [])
    )
    provider_cached_tokens = sum(
        int(usage.get("cached_input_tokens") or 0)
        for trace in traces
        for usage in (trace.get("provider_usage") or [])
    )
    span_durations = [
        float(span.get("duration_ms") or 0.0)
        for trace in traces
        for span in (trace.get("spans") or [])
    ]
    return {
        "trace_count": len(traces),
        "total_tokens_saved": sum(int(trace.get("tokens_saved") or 0) for trace in traces),
        "median_reduction_percent": round(_median(reductions), 2),
        "median_packed_tokens": int(_median(packed)),
        "p95_packed_tokens": int(_percentile(packed, 0.95)),
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
        "provider_input_tokens": provider_input_tokens,
        "provider_cached_input_tokens": provider_cached_tokens,
        "cache_hit_rate_percent": round(
            (provider_cached_tokens / provider_input_tokens * 100) if provider_input_tokens else 0.0,
            2,
        ),
        "total_span_duration_ms": round(sum(span_durations), 2),
        "p95_span_duration_ms": round(_percentile(span_durations, 0.95), 2),
        "workflow_breakdown": _workflow_breakdown(traces),
        "action_items": _dashboard_action_items(traces),
    }


def render_observability_dashboard(db_path: Path, *, limit: int = 100) -> str:
    traces = _load_dashboard_traces(db_path, limit=limit)
    summary = _dashboard_summary(traces)
    workflow_options = "".join(
        f"<option value=\"{html.escape(workflow)}\">{html.escape(workflow)}</option>"
        for workflow in sorted({str(trace.get("workflow") or "unknown") for trace in traces})
    )
    action_items = summary.get("action_items") or []
    action_items_html = (
        "".join(
            "<li>"
            f"<span class=\"severity {html.escape(str(item.get('severity', 'info')))}\">"
            f"{html.escape(str(item.get('severity', 'info')))}</span>"
            f"<strong>{html.escape(str(item.get('title', 'Action item')))}</strong>"
            f"<span>{html.escape(str(item.get('task') or 'Untitled task'))}</span>"
            f"<em>{html.escape(str(item.get('detail') or ''))}</em>"
            "</li>"
            for item in action_items
        )
        if action_items
        else "<li class=\"muted\">No active action items. Keep labeling real agent outcomes.</li>"
    )
    workflow_rows = summary.get("workflow_breakdown") or []
    workflow_html = (
        "".join(
            "<tr>"
            f"<td>{html.escape(str(row.get('workflow', 'unknown')))}</td>"
            f"<td>{_fmt_int(row.get('trace_count'))}</td>"
            f"<td>{_fmt_int(row.get('tokens_saved'))}</td>"
            f"<td>{_fmt_int(row.get('median_packed_tokens'))}</td>"
            f"<td>{_fmt_float(row.get('median_reduction_percent'))}%</td>"
            f"<td>{_fmt_int(row.get('warning_count'))}</td>"
            f"<td>{_fmt_int(row.get('failed_count'))}</td>"
            "</tr>"
            for row in workflow_rows
        )
        if workflow_rows
        else "<tr><td colspan=\"7\" class=\"muted\">No workflow data yet.</td></tr>"
    )
    trace_cards = []
    for trace in traces:
        metadata = trace.get("metadata") or {}
        selected_files = metadata.get("selected_files") or []
        missing = metadata.get("missing_signals") or []
        spans = trace.get("spans") or []
        annotations = trace.get("annotations") or []
        provider_usage = trace.get("provider_usage") or []
        trace_text = " ".join(
            [
                str(trace.get("task") or ""),
                str(trace.get("trace_id") or ""),
                str(trace.get("repo_path") or ""),
                str(trace.get("workflow") or ""),
                str(trace.get("quality_status") or ""),
                " ".join(str(path) for path in selected_files),
                " ".join(str(item) for item in missing),
            ]
        ).lower()
        attention_flag = (
            "true"
            if missing or trace.get("quality_status") in {"failed", "warning", "unknown", "unverified"}
            else "false"
        )
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
            <article class="trace-card"
              data-quality="{html.escape(str(trace.get('quality_status', 'unknown')))}"
              data-workflow="{html.escape(str(trace.get('workflow', 'unknown')))}"
              data-attention="{attention_flag}"
              data-search="{html.escape(trace_text)}">
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
        --shadow: rgba(15, 23, 42, 0.08);
        font-family: "Avenir Next", ui-sans-serif, "Helvetica Neue", sans-serif;
      }}
      body {{
        margin: 0;
        color: var(--ink);
        background:
          radial-gradient(circle at top left, rgba(37, 99, 235, 0.14), transparent 28rem),
          radial-gradient(circle at 90% 15%, rgba(15, 118, 110, 0.16), transparent 24rem),
          linear-gradient(135deg, #eef2f6 0%, #e6edf5 100%);
      }}
      header {{
        padding: 2.25rem clamp(1rem, 4vw, 4rem) 1rem;
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
      .layout {{
        display: grid;
        grid-template-columns: minmax(0, 1fr) minmax(280px, 380px);
        gap: 1rem;
        align-items: start;
      }}
      .stats {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        gap: 1rem;
        margin: 1.25rem 0 1.75rem;
      }}
      .stat, .trace-card, .empty, .panel {{
        background: rgba(255, 255, 255, 0.9);
        border: 1px solid var(--line);
        border-radius: 18px;
        box-shadow: 0 18px 50px var(--shadow);
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
      .controls {{
        display: grid;
        grid-template-columns: minmax(220px, 1fr) repeat(3, max-content);
        gap: 0.75rem;
        align-items: center;
        margin: 0 0 1rem;
        background: rgba(255, 255, 255, 0.78);
        border: 1px solid var(--line);
        border-radius: 18px;
        box-shadow: 0 18px 50px var(--shadow);
        padding: 0.85rem;
      }}
      .controls input, .controls select {{
        border: 1px solid var(--line);
        border-radius: 12px;
        padding: 0.65rem 0.75rem;
        color: var(--ink);
        background: var(--panel);
        font: inherit;
      }}
      .controls label {{
        display: inline-flex;
        gap: 0.35rem;
        align-items: center;
        color: var(--muted);
        font-size: 0.9rem;
        white-space: nowrap;
      }}
      .result-count {{
        color: var(--muted);
        font-size: 0.9rem;
        text-align: right;
      }}
      .panel {{
        padding: 1rem;
        margin-bottom: 1rem;
      }}
      .panel h2 {{
        margin: 0 0 0.75rem;
        font-size: 1rem;
        letter-spacing: -0.02em;
      }}
      .ops-grid {{
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 0.65rem;
      }}
      .ops-grid div {{
        background: var(--panel-soft);
        border: 1px solid var(--line);
        border-radius: 14px;
        padding: 0.75rem;
      }}
      .ops-grid strong {{
        display: block;
        font-size: 1.25rem;
      }}
      .actions {{
        list-style: none;
        padding: 0;
        margin: 0;
      }}
      .actions li {{
        display: grid;
        gap: 0.25rem;
        border-top: 1px solid var(--line);
        padding: 0.75rem 0;
      }}
      .actions li:first-child {{
        border-top: 0;
        padding-top: 0;
      }}
      .severity {{
        width: fit-content;
        border-radius: 999px;
        padding: 0.12rem 0.45rem;
        font-size: 0.72rem;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        background: #e0f2fe;
        color: #0369a1;
      }}
      .severity.critical {{
        background: #fee2e2;
        color: var(--danger);
      }}
      .severity.warning {{
        background: #fef3c7;
        color: var(--warn);
      }}
      table {{
        width: 100%;
        border-collapse: collapse;
        font-size: 0.86rem;
      }}
      th, td {{
        border-top: 1px solid var(--line);
        padding: 0.5rem 0.35rem;
        text-align: right;
      }}
      th:first-child, td:first-child {{
        text-align: left;
      }}
      th {{
        color: var(--muted);
        font-weight: 700;
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
      @media (max-width: 980px) {{
        .layout {{
          grid-template-columns: 1fr;
        }}
        .controls {{
          grid-template-columns: 1fr;
        }}
        .result-count {{
          text-align: left;
        }}
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
        <div class="stat"><strong>{_fmt_int(summary['p95_packed_tokens'])}</strong><span>p95 packed tokens</span></div>
        <div class="stat"><strong>{_fmt_int(summary['warning_count'])}</strong><span>traces with warnings</span></div>
        <div class="stat"><strong>{_fmt_int(summary['failed_count'])}</strong><span>failed quality labels</span></div>
        <div class="stat"><strong>{_fmt_int(summary['annotated_count'])}</strong><span>annotated traces</span></div>
        <div class="stat"><strong>{_fmt_int(summary['provider_usage_count'])}</strong><span>provider usage records</span></div>
        <div class="stat"><strong>${_fmt_float(summary['provider_reported_cost_usd'])}</strong><span>provider reported cost</span></div>
      </section>
      <section class="layout">
        <div>
          <section class="controls" aria-label="Trace filters">
            <input id="traceSearch" type="search" placeholder="Search task, trace id, repo, file, warning..." />
            <select id="qualityFilter" aria-label="Filter by quality">
              <option value="">All quality</option>
              <option value="failed">failed</option>
              <option value="warning">warning</option>
              <option value="unverified">unverified</option>
              <option value="passed">passed</option>
              <option value="annotated">annotated</option>
              <option value="unknown">unknown</option>
            </select>
            <select id="workflowFilter" aria-label="Filter by workflow">
              <option value="">All workflows</option>
              {workflow_options}
            </select>
            <label><input id="attentionFilter" type="checkbox" /> attention</label>
            <span id="traceResultCount" class="result-count"></span>
          </section>
          <div class="trace-grid" id="traceGrid">
          {cards_html}
          </div>
        </div>
        <aside>
          <section class="panel">
            <h2>Operations</h2>
            <div class="ops-grid">
              <div><strong>{_fmt_int(summary['provider_input_tokens'])}</strong><span>provider input tokens</span></div>
              <div><strong>{_fmt_int(summary['provider_cached_input_tokens'])}</strong><span>cached input tokens</span></div>
              <div><strong>{_fmt_float(summary['cache_hit_rate_percent'])}%</strong><span>cache hit rate</span></div>
              <div><strong>{_fmt_float(summary['p95_span_duration_ms'])} ms</strong><span>p95 span latency</span></div>
              <div><strong>{_fmt_float(summary['total_span_duration_ms'])} ms</strong><span>total traced duration</span></div>
              <div><strong>{_fmt_int(summary['annotated_count'])}/{_fmt_int(summary['trace_count'])}</strong><span>quality labels</span></div>
            </div>
          </section>
          <section class="panel">
            <h2>Action queue</h2>
            <ul class="actions">{action_items_html}</ul>
          </section>
          <section class="panel">
            <h2>Workflow breakdown</h2>
            <table>
              <thead>
                <tr><th>Workflow</th><th>Traces</th><th>Saved</th><th>Median pack</th><th>Reduction</th><th>Warn</th><th>Fail</th></tr>
              </thead>
              <tbody>{workflow_html}</tbody>
            </table>
          </section>
        </aside>
      </section>
    </main>
    <script>
      const traceCards = Array.from(document.querySelectorAll(".trace-card"));
      const searchInput = document.querySelector("#traceSearch");
      const qualityFilter = document.querySelector("#qualityFilter");
      const workflowFilter = document.querySelector("#workflowFilter");
      const attentionFilter = document.querySelector("#attentionFilter");
      const traceResultCount = document.querySelector("#traceResultCount");

      function updateTraceFilters() {{
        const query = (searchInput?.value || "").trim().toLowerCase();
        const quality = qualityFilter?.value || "";
        const workflow = workflowFilter?.value || "";
        const attentionOnly = Boolean(attentionFilter?.checked);
        let visible = 0;
        for (const card of traceCards) {{
          const matchesSearch = !query || card.dataset.search.includes(query);
          const matchesQuality = !quality || card.dataset.quality === quality;
          const matchesWorkflow = !workflow || card.dataset.workflow === workflow;
          const matchesAttention = !attentionOnly || card.dataset.attention === "true";
          const show = matchesSearch && matchesQuality && matchesWorkflow && matchesAttention;
          card.hidden = !show;
          if (show) visible += 1;
        }}
        if (traceResultCount) {{
          traceResultCount.textContent = `${{visible}} / ${{traceCards.length}} traces`;
        }}
      }}

      for (const control of [searchInput, qualityFilter, workflowFilter, attentionFilter]) {{
        control?.addEventListener("input", updateTraceFilters);
        control?.addEventListener("change", updateTraceFilters);
      }}
      updateTraceFilters();
    </script>
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
