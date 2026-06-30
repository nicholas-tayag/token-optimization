from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

_INDEX_FORMAT_VERSION = 2
_SYMBOL_LIMIT = 40
_IMPORT_LIMIT = 40
_LOCAL_IMPORT_LIMIT = 40
_LOCAL_IMPORT_SUFFIXES = (".js", ".mjs", ".ts", ".tsx", ".jsx", ".py")


@dataclass(frozen=True)
class RepositoryFileIndexEntry:
    relative_path: str
    size_bytes: int
    mtime_ns: int
    content_hash: str
    symbols: tuple[str, ...]
    imports: tuple[str, ...]
    local_import_paths: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "size_bytes": self.size_bytes,
            "mtime_ns": self.mtime_ns,
            "content_hash": self.content_hash,
            "symbols": list(self.symbols),
            "imports": list(self.imports),
            "local_import_paths": list(self.local_import_paths),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RepositoryFileIndexEntry":
        return cls(
            relative_path=str(payload["relative_path"]),
            size_bytes=int(payload["size_bytes"]),
            mtime_ns=int(payload["mtime_ns"]),
            content_hash=str(payload["content_hash"]),
            symbols=tuple(str(item) for item in payload.get("symbols", [])),
            imports=tuple(str(item) for item in payload.get("imports", [])),
            local_import_paths=tuple(str(item) for item in payload.get("local_import_paths", [])),
        )


@dataclass(frozen=True)
class RepositoryIndexBuildResult:
    entries: dict[str, RepositoryFileIndexEntry]
    stats: dict[str, Any]


def _ordered_unique(values: Iterable[str], limit: int) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
        if len(ordered) >= limit:
            break
    return tuple(ordered)


def _default_index_root() -> Path:
    configured = os.environ.get("AGENVANTAGE_INDEX_ROOT")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".agenvantage" / "repo-index-v1"


def repository_index_path(repo: Path) -> Path:
    resolved = repo.resolve()
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12]
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", resolved.name or "repo")
    return _default_index_root() / f"{safe_name}-{digest}.json"


def _read_cache(path: Path) -> dict[str, RepositoryFileIndexEntry]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if payload.get("format_version") != _INDEX_FORMAT_VERSION:
        return {}
    entries = payload.get("entries", {})
    if not isinstance(entries, dict):
        return {}
    result: dict[str, RepositoryFileIndexEntry] = {}
    for relative_path, raw_entry in entries.items():
        if not isinstance(raw_entry, dict):
            continue
        try:
            entry = RepositoryFileIndexEntry.from_dict(raw_entry)
        except (KeyError, TypeError, ValueError):
            continue
        result[str(relative_path)] = entry
    return result


def _write_cache(path: Path, repo: Path, entries: dict[str, RepositoryFileIndexEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format_version": _INDEX_FORMAT_VERSION,
        "repo": str(repo.resolve()),
        "entries": {relative_path: entry.to_dict() for relative_path, entry in entries.items()},
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _extract_symbols(path: Path, text: str) -> tuple[str, ...]:
    values: list[str] = []
    values.extend(
        match.group(1)
        for match in re.finditer(
            r"\b(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)\b",
            text,
        )
    )
    values.extend(
        match.group(1)
        for match in re.finditer(
            r"\b(?:class|interface|enum|struct)\s+([A-Za-z_][A-Za-z0-9_]*)\b",
            text,
        )
    )
    values.extend(
        match.group(2)
        for match in re.finditer(
            r"\b(const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=",
            text,
        )
    )
    values.extend(
        match.group(1)
        for match in re.finditer(
            r"^\s*(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)\b",
            text,
            re.MULTILINE,
        )
    )
    values.extend(
        match.group(1)
        for match in re.finditer(r"\bfunc\s+([A-Za-z_][A-Za-z0-9_]*)\b", text)
    )
    values.extend(
        match.group(1)
        for match in re.finditer(
            r"\b(?:app|router)\.(?:get|post|put|patch|delete|use)\(\s*['\"]([^'\"]+)",
            text,
        )
    )
    for export_match in re.finditer(r"\bexport\s*\{([^}]+)\}", text):
        for piece in export_match.group(1).split(","):
            candidate = piece.strip().split(" as ")[0].strip()
            if candidate:
                values.append(candidate)
    values.append(path.stem)
    return _ordered_unique(values, _SYMBOL_LIMIT)


def _extract_imports(text: str) -> tuple[str, ...]:
    values: list[str] = []
    values.extend(
        match.group(1)
        for match in re.finditer(r"\bfrom\s+['\"]([^'\"]+)['\"]", text)
    )
    values.extend(
        match.group(1)
        for match in re.finditer(r"\brequire\(\s*['\"]([^'\"]+)['\"]\s*\)", text)
    )
    values.extend(
        match.group(1)
        for match in re.finditer(r"^\s*import\s+([A-Za-z0-9_., ]+)", text, re.MULTILINE)
    )
    values.extend(
        match.group(1)
        for match in re.finditer(r"^\s*from\s+([A-Za-z0-9_\.]+)\s+import\b", text, re.MULTILINE)
    )
    values.extend(
        match.group(1)
        for match in re.finditer(r'^\s*import\s+"([^"]+)"', text, re.MULTILINE)
    )
    return _ordered_unique(values, _IMPORT_LIMIT)


def _resolve_local_imports(
    relative_path: str, imports: tuple[str, ...], candidate_paths: set[str]
) -> tuple[str, ...]:
    base_dir = Path(relative_path).parent
    resolved: list[str] = []
    for value in imports:
        if not value.startswith(("./", "../")):
            continue
        raw_target = (base_dir / value).as_posix()
        target_path = Path(raw_target)
        candidates = [target_path]
        if target_path.suffix:
            candidates.append(target_path.with_suffix(target_path.suffix))
        else:
            candidates.extend(target_path.with_suffix(suffix) for suffix in _LOCAL_IMPORT_SUFFIXES)
            candidates.extend(
                (target_path / f"index{suffix}") for suffix in _LOCAL_IMPORT_SUFFIXES
            )
        for candidate in candidates:
            normalized = candidate.as_posix()
            if normalized in candidate_paths:
                resolved.append(normalized)
                break
    return _ordered_unique(resolved, _LOCAL_IMPORT_LIMIT)


def build_repository_index(repo: Path, files: Iterable[Path]) -> RepositoryIndexBuildResult:
    repo = repo.resolve()
    file_list = tuple(files)
    candidate_paths = {path.relative_to(repo).as_posix() for path in file_list}
    cache_path = repository_index_path(repo)
    cached_entries = _read_cache(cache_path)
    entries: dict[str, RepositoryFileIndexEntry] = {}
    reused_files = 0
    rebuilt_files = 0
    skipped_files = 0
    for path in file_list:
        relative_path = path.relative_to(repo).as_posix()
        stat = path.stat()
        cached_entry = cached_entries.get(relative_path)
        if (
            cached_entry is not None
            and cached_entry.size_bytes == stat.st_size
            and cached_entry.mtime_ns == stat.st_mtime_ns
        ):
            entries[relative_path] = cached_entry
            reused_files += 1
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            skipped_files += 1
            continue
        entry = RepositoryFileIndexEntry(
            relative_path=relative_path,
            size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            symbols=_extract_symbols(path, text),
            imports=_extract_imports(text),
            local_import_paths=(),
        )
        entries[relative_path] = entry
        rebuilt_files += 1
    for relative_path, entry in tuple(entries.items()):
        entries[relative_path] = RepositoryFileIndexEntry(
            relative_path=entry.relative_path,
            size_bytes=entry.size_bytes,
            mtime_ns=entry.mtime_ns,
            content_hash=entry.content_hash,
            symbols=entry.symbols,
            imports=entry.imports,
            local_import_paths=_resolve_local_imports(
                entry.relative_path, entry.imports, candidate_paths
            ),
        )
    _write_cache(cache_path, repo, entries)
    stats = {
        "cache_path": str(cache_path),
        "format_version": _INDEX_FORMAT_VERSION,
        "indexed_files": len(entries),
        "reused_files": reused_files,
        "rebuilt_files": rebuilt_files,
        "skipped_files": skipped_files,
        "symbol_count": sum(len(entry.symbols) for entry in entries.values()),
        "import_count": sum(len(entry.imports) for entry in entries.values()),
        "local_import_count": sum(len(entry.local_import_paths) for entry in entries.values()),
    }
    return RepositoryIndexBuildResult(entries=entries, stats=stats)
