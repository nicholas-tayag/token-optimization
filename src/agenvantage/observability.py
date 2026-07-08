from __future__ import annotations

import json
import hashlib
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


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


def load_trace(db_path: Path, trace_id: str) -> dict[str, Any]:
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
        artifacts = connection.execute(
            "SELECT artifact_id, kind, path, metadata_json FROM artifacts WHERE trace_id = ? ORDER BY kind",
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
    return payload
