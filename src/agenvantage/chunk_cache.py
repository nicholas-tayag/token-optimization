"""Persistent, repository-scoped cache for redacted context chunks.

The cache deliberately knows nothing about repository parsing or chunk classes. A
caller supplies a stable key and a JSON-compatible payload that has already been
redacted. Each operation opens and closes its own SQLite connection, which keeps
the object safe to share between threads and processes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

CACHE_FORMAT_VERSION = 1
_DEFAULT_ROOT = Path.home() / ".agenvantage" / "chunk-cache-v1"
_SCHEMA_VERSION = 1
_FORBIDDEN_PAYLOAD_KEYS = {
    "original_text",
    "raw_source",
    "source",
    "source_text",
    "unredacted_source",
}
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")

JSONValue: TypeAlias = None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]
ChunkPayload: TypeAlias = JSONValue


@dataclass(frozen=True, slots=True)
class ChunkCacheKey:
    """Identity of one tokenized chunk generation."""

    relative_path: str
    content_hash: str
    tokenizer_profile: str
    chunk_lines: int
    overlap_lines: int
    cache_format_version: int = CACHE_FORMAT_VERSION

    def __post_init__(self) -> None:
        path = Path(self.relative_path)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("relative_path must be repository-relative")
        if not self.relative_path or self.relative_path == ".":
            raise ValueError("relative_path must not be empty")
        if not self.content_hash or not self.tokenizer_profile:
            raise ValueError("content_hash and tokenizer_profile are required")
        if self.chunk_lines <= 0:
            raise ValueError("chunk_lines must be positive")
        if self.overlap_lines < 0 or self.overlap_lines >= self.chunk_lines:
            raise ValueError("overlap_lines must be non-negative and smaller than chunk_lines")
        if self.cache_format_version <= 0:
            raise ValueError("cache_format_version must be positive")

    def as_tuple(self) -> tuple[str, str, str, int, int, int]:
        return (
            self.relative_path,
            self.content_hash,
            self.tokenizer_profile,
            self.chunk_lines,
            self.overlap_lines,
            self.cache_format_version,
        )


@dataclass(frozen=True, slots=True)
class CachedChunk:
    key: ChunkCacheKey
    payload: ChunkPayload


def _validate_redacted_payload(payload: ChunkPayload, *, inherited_redacted: bool = False) -> None:
    if isinstance(payload, Mapping):
        forbidden = _FORBIDDEN_PAYLOAD_KEYS.intersection(payload)
        if forbidden:
            names = ", ".join(sorted(forbidden))
            raise ValueError(f"payload contains unredacted source field(s): {names}")
        redacted = inherited_redacted or payload.get("redacted") is True
        if "text" in payload and not redacted:
            raise ValueError("payloads containing text must set redacted=true")
        for value in payload.values():
            _validate_redacted_payload(value, inherited_redacted=redacted)
    elif isinstance(payload, list):
        for value in payload:
            _validate_redacted_payload(value, inherited_redacted=inherited_redacted)


def _json_payload(payload: ChunkPayload) -> str:
    _validate_redacted_payload(payload)
    try:
        return json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise TypeError("payload must be JSON-compatible") from exc


def _repository_db_path(repository: Path, root: Path) -> Path:
    resolved = repository.expanduser().resolve()
    digest = hashlib.sha256(os.fsencode(str(resolved))).hexdigest()[:16]
    name = _SAFE_NAME.sub("-", resolved.name or "repository").strip("-") or "repository"
    return root.expanduser() / f"{name}-{digest}.sqlite3"


class ChunkCache:
    """A persistent cache with one SQLite database per repository."""

    def __init__(self, repository: str | os.PathLike[str], root: str | os.PathLike[str] | None = None) -> None:
        self.repository = Path(repository).expanduser().resolve()
        configured_root = root or os.environ.get("AGENVANTAGE_INDEX_ROOT")
        self.root = Path(configured_root).expanduser() if configured_root else _DEFAULT_ROOT
        self.db_path = _repository_db_path(self.repository, self.root)
        self._hits = 0
        self._misses = 0
        self._writes = 0
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        self.root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=30.0)
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            connection.execute("PRAGMA journal_mode = WAL")
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower():
                connection.close()
                raise
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS cache_metadata (
                        name TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS chunks (
                        relative_path TEXT NOT NULL,
                        content_hash TEXT NOT NULL,
                        tokenizer_profile TEXT NOT NULL,
                        chunk_lines INTEGER NOT NULL,
                        overlap_lines INTEGER NOT NULL,
                        cache_format_version INTEGER NOT NULL,
                        payload_json TEXT NOT NULL,
                        PRIMARY KEY (
                            relative_path, content_hash, tokenizer_profile,
                            chunk_lines, overlap_lines, cache_format_version
                        )
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS chunks_generation_idx
                    ON chunks (
                        relative_path, tokenizer_profile, chunk_lines,
                        overlap_lines, cache_format_version
                    )
                    """
                )
                connection.execute(
                    "INSERT OR REPLACE INTO cache_metadata(name, value) VALUES (?, ?)",
                    ("schema_version", str(_SCHEMA_VERSION)),
                )
        finally:
            connection.close()

    @staticmethod
    def _coerce_entry(entry: CachedChunk | tuple[ChunkCacheKey, ChunkPayload]) -> CachedChunk:
        if isinstance(entry, CachedChunk):
            return entry
        key, payload = entry
        return CachedChunk(key, payload)

    def load_many(self, keys: Iterable[ChunkCacheKey]) -> dict[ChunkCacheKey, ChunkPayload]:
        """Load matching chunks in one read transaction and update hit statistics."""
        unique_keys = tuple(dict.fromkeys(keys))
        if not unique_keys:
            return {}
        connection = self._connect()
        found: dict[ChunkCacheKey, ChunkPayload] = {}
        try:
            with connection:
                for key in unique_keys:
                    row = connection.execute(
                        """
                        SELECT payload_json FROM chunks
                        WHERE relative_path = ? AND content_hash = ?
                          AND tokenizer_profile = ? AND chunk_lines = ?
                          AND overlap_lines = ? AND cache_format_version = ?
                        """,
                        key.as_tuple(),
                    ).fetchone()
                    if row is None:
                        self._misses += 1
                        continue
                    found[key] = json.loads(row[0])
                    self._hits += 1
        finally:
            connection.close()
        return found

    def save_many(self, entries: Iterable[CachedChunk | tuple[ChunkCacheKey, ChunkPayload]]) -> int:
        """Atomically save entries, replacing older content generations for a chunk."""
        normalized = tuple(self._coerce_entry(entry) for entry in entries)
        if not normalized:
            return 0
        serialized = tuple((entry, _json_payload(entry.payload)) for entry in normalized)
        connection = self._connect()
        writes = 0
        try:
            with connection:
                for entry, payload_json in serialized:
                    key = entry.key
                    connection.execute(
                        """
                        DELETE FROM chunks
                        WHERE relative_path = ? AND tokenizer_profile = ?
                          AND chunk_lines = ? AND overlap_lines = ?
                          AND cache_format_version = ? AND content_hash <> ?
                        """,
                        (
                            key.relative_path,
                            key.tokenizer_profile,
                            key.chunk_lines,
                            key.overlap_lines,
                            key.cache_format_version,
                            key.content_hash,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT OR REPLACE INTO chunks(
                            relative_path, content_hash, tokenizer_profile,
                            chunk_lines, overlap_lines, cache_format_version,
                            payload_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (*key.as_tuple(), payload_json),
                    )
                    writes += 1
        finally:
            connection.close()
        self._writes += writes
        return writes

    bulk_load = load_many
    bulk_save = save_many

    def stats(self) -> dict[str, int]:
        return {"hits": self._hits, "misses": self._misses, "writes": self._writes}

    def reset_stats(self) -> None:
        self._hits = 0
        self._misses = 0
        self._writes = 0


__all__ = ["CACHE_FORMAT_VERSION", "CachedChunk", "ChunkCache", "ChunkCacheKey"]
