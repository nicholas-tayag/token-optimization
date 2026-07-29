"""Deterministic metadata-only queries over a repository index.

This module deliberately knows nothing about repository files or source text.
It consumes the structural metadata produced by an indexer and returns a
small, explainable candidate set for a later materialization step.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import re
from typing import Any, Protocol

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
_DEFAULT_TOP_K = 20
_CHANNELS = (
    "paths",
    "symbols",
    "imports",
    "reverse_imports",
    "roles",
    "languages",
    "landmarks",
)
_WEIGHTS = {
    "paths": 100.0,
    "symbols": 80.0,
    "imports": 55.0,
    "reverse_imports": 45.0,
    "roles": 25.0,
    "languages": 20.0,
    "landmarks": 35.0,
}


class MetadataRecord(Protocol):
    """Structural metadata required from an index record."""

    relative_path: str


@dataclass(frozen=True)
class MetadataQuery:
    """Query terms grouped by the metadata channel they target."""

    paths: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    imports: tuple[str, ...] = ()
    reverse_imports: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    landmarks: tuple[str, ...] = ()
    top_k: int = _DEFAULT_TOP_K

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "MetadataQuery":
        """Build a query from JSON-like data without retaining mutable input."""

        values: dict[str, Any] = {}
        for channel in _CHANNELS:
            values[channel] = _as_strings(payload.get(channel, ()))
        values["top_k"] = int(payload.get("top_k", _DEFAULT_TOP_K))
        return cls(**values)


@dataclass(frozen=True)
class IndexedPathCandidate:
    """A scored path and the metadata evidence that selected it."""

    path: str
    score: float
    matched_channels: tuple[str, ...]
    reasons: tuple[str, ...]
    matched_paths: tuple[str, ...] = ()
    matched_symbols: tuple[str, ...] = ()

    @property
    def relative_path(self) -> str:
        """Alias matching the index entry terminology."""

        return self.path

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "relative_path": self.path,
            "score": self.score,
            "matched_channels": list(self.matched_channels),
            "reasons": list(self.reasons),
            "matched_paths": list(self.matched_paths),
            "matched_symbols": list(self.matched_symbols),
        }


def query_index(
    entries: Mapping[str, Any] | Iterable[Any],
    query: MetadataQuery | Mapping[str, Any] | Any,
    *,
    top_k: int | None = None,
) -> list[IndexedPathCandidate]:
    """Rank indexed records using metadata only.

    ``entries`` may be the ``entries`` dictionary returned by the repository
    index, an iterable of compatible objects, or an iterable of mappings.
    ``query`` may be :class:`MetadataQuery`, a mapping, or an object with the
    channel attributes. No record attribute other than the listed metadata
    fields is accessed, and source paths are never opened.

    The scan is ``O(F * Q + U log U)`` for ``F`` records, ``Q`` query terms,
    and ``U`` scored unique paths. Query terms are normalized into sets once;
    original path and symbol strings are retained in the result.
    """

    metadata_query = _coerce_query(query)
    limit = metadata_query.top_k if top_k is None else top_k
    if limit < 0:
        raise ValueError("top_k must be non-negative")
    if limit == 0:
        return []

    records = _iter_records(entries)
    scored_by_path: dict[str, IndexedPathCandidate] = {}
    for fallback_path, record in records:
        path = _string_value(_field(record, "relative_path", "path", default=fallback_path))
        if not path:
            continue
        score, channels, reasons, matched_paths, matched_symbols = _score_record(
            record, path, metadata_query
        )
        if score <= 0:
            continue
        candidate = IndexedPathCandidate(
            path=path,
            score=score,
            matched_channels=tuple(channels),
            reasons=tuple(reasons),
            matched_paths=tuple(matched_paths),
            matched_symbols=tuple(matched_symbols),
        )
        previous = scored_by_path.get(path)
        if previous is None or (candidate.score, candidate.reasons) > (
            previous.score,
            previous.reasons,
        ):
            scored_by_path[path] = candidate

    scored = list(scored_by_path.values())
    scored.sort(key=lambda candidate: (-candidate.score, candidate.path))
    return scored[:limit]


def query_metadata(
    entries: Mapping[str, Any] | Iterable[Any],
    query: MetadataQuery | Mapping[str, Any] | Any,
    *,
    top_k: int | None = None,
) -> list[IndexedPathCandidate]:
    """Compatibility alias for callers that name the query plane explicitly."""

    return query_index(entries, query, top_k=top_k)


def _coerce_query(query: MetadataQuery | Mapping[str, Any] | Any) -> MetadataQuery:
    if isinstance(query, MetadataQuery):
        return query
    if isinstance(query, Mapping):
        return MetadataQuery.from_mapping(query)
    values: dict[str, Any] = {
        channel: _as_strings(getattr(query, channel, ())) for channel in _CHANNELS
    }
    values["top_k"] = int(getattr(query, "top_k", _DEFAULT_TOP_K))
    return MetadataQuery(**values)


def _iter_records(
    entries: Mapping[str, Any] | Iterable[Any],
) -> Iterable[tuple[str, Any]]:
    if isinstance(entries, Mapping):
        for key, record in entries.items():
            yield str(key), record
        return
    for record in entries:
        yield "", record


def _field(record: Any, *names: str, default: Any = ()) -> Any:
    if isinstance(record, Mapping):
        for name in names:
            if name in record:
                return record[name]
        return default
    for name in names:
        value = getattr(record, name, _MISSING)
        if value is not _MISSING:
            return value
    return default


_MISSING = object()


def _score_record(
    record: Any,
    path: str,
    query: MetadataQuery,
) -> tuple[float, list[str], list[str], list[str], list[str]]:
    score = 0.0
    channels: list[str] = []
    reasons: list[str] = []
    matched_paths: list[str] = []
    matched_symbols: list[str] = []
    for channel in _CHANNELS:
        query_values = getattr(query, channel)
        if not query_values:
            continue
        record_values = _record_values(record, channel, path)
        exact, overlap = _matches(query_values, record_values, path_channel=channel)
        if not exact and not overlap:
            continue
        channels.append(channel)
        score += _WEIGHTS[channel] * len(exact)
        score += (_WEIGHTS[channel] * 0.2) * len(overlap)
        for value in exact:
            reasons.append(f"{channel}:exact:{value}")
        for value in overlap:
            reasons.append(f"{channel}:token:{value}")
        if channel == "paths":
            matched_paths.extend(exact)
        elif channel == "symbols":
            matched_symbols.extend(exact)
    return score, channels, reasons, matched_paths, matched_symbols


def _record_values(record: Any, channel: str, path: str) -> tuple[str, ...]:
    if channel == "paths":
        return (path,)
    names = {
        "reverse_imports": ("imported_by_paths", "reverse_imports", "imported_by"),
        "roles": ("role", "file_role", "roles"),
        "languages": ("language", "languages"),
    }.get(channel, (channel,))
    value: Any = ()
    for name in names:
        value = _field(record, name, default=_MISSING)
        if value is not _MISSING:
            break
    return _as_strings(value if value is not _MISSING else ())


def _matches(
    queries: Sequence[str],
    values: Sequence[str],
    *,
    path_channel: str,
) -> tuple[list[str], list[str]]:
    exact: list[str] = []
    overlap: list[str] = []
    normalized_values = {value.casefold(): value for value in values if value}
    value_tokens = {
        token
        for value in values
        for token in _tokens(value)
    }
    for query in queries:
        if not query:
            continue
        if query.casefold() in normalized_values:
            exact.append(normalized_values[query.casefold()])
            continue
        query_tokens = set(_tokens(query))
        if path_channel == "paths":
            query_tokens.update(_tokens(query.replace("/", " ")))
        if query_tokens & value_tokens:
            overlap.append(query)
    return _unique(exact), _unique(overlap)


def _as_strings(value: Any) -> tuple[str, ...]:
    if value is None or value is _MISSING:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(str(item) for item in value if item is not None)
    return (str(value),)


def _string_value(value: Any) -> str:
    return value if isinstance(value, str) else str(value) if value else ""


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(token.casefold() for token in _TOKEN_RE.findall(value))


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


__all__ = [
    "IndexedPathCandidate",
    "MetadataQuery",
    "MetadataRecord",
    "query_index",
    "query_metadata",
]
