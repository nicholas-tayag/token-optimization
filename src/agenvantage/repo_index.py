from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

_INDEX_FORMAT_VERSION = 5
_SYMBOL_LIMIT = 40
_IMPORT_LIMIT = 40
_LOCAL_IMPORT_LIMIT = 40
_LOCAL_IMPORT_SUFFIXES = (
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".jsx",
    ".py",
    ".css",
    ".scss",
    ".sass",
    ".less",
    ".json",
    ".svg",
    ".vue",
    ".svelte",
)
_STYLE_SUFFIXES = {".css", ".scss", ".sass", ".less", ".styl", ".stylus"}
_DOC_SUFFIXES = {".md", ".mdx", ".rst", ".txt", ".adoc"}
_CONFIG_SUFFIXES = {".json", ".jsonc", ".toml", ".yaml", ".yml", ".ini", ".cfg"}
_CONFIG_NAMES = {
    ".env",
    ".env.example",
    "babel.config.js",
    "eslint.config.js",
    "jest.config.js",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "pyproject.toml",
    "tsconfig.json",
    "vite.config.js",
    "vite.config.ts",
    "webpack.config.js",
}
_GENERATED_PARTS = {"artifacts", "build", "coverage", "dist", "generated", "out", "target"}
_VENDOR_PARTS = {"bower_components", "node_modules", "third_party", "vendor"}


@dataclass(frozen=True)
class SymbolOccurrence:
    line_number: int
    name: str
    kind: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "line_number": self.line_number,
            "name": self.name,
            "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SymbolOccurrence":
        return cls(
            line_number=int(payload["line_number"]),
            name=str(payload["name"]),
            kind=str(payload["kind"]),
        )


@dataclass(frozen=True)
class RepositoryFileIndexEntry:
    relative_path: str
    size_bytes: int
    mtime_ns: int
    ctime_ns: int
    file_id: str
    content_hash: str
    symbols: tuple[str, ...]
    imports: tuple[str, ...]
    local_import_paths: tuple[str, ...]
    imported_by_paths: tuple[str, ...]
    symbol_occurrences: tuple[SymbolOccurrence, ...]
    role: str = "source"
    language: str = ""
    landmarks: tuple[str, ...] = ()

    @property
    def file_role(self) -> str:
        return self.role

    @property
    def is_generated(self) -> bool:
        return self.role == "generated"

    @property
    def is_vendor(self) -> bool:
        return self.role == "vendor"

    @property
    def is_test(self) -> bool:
        return self.role == "test"

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "size_bytes": self.size_bytes,
            "mtime_ns": self.mtime_ns,
            "ctime_ns": self.ctime_ns,
            "file_id": self.file_id,
            "content_hash": self.content_hash,
            "symbols": list(self.symbols),
            "imports": list(self.imports),
            "local_import_paths": list(self.local_import_paths),
            "imported_by_paths": list(self.imported_by_paths),
            "symbol_occurrences": [item.to_dict() for item in self.symbol_occurrences],
            "role": self.role,
            "file_role": self.role,
            "language": self.language,
            "landmarks": list(self.landmarks),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RepositoryFileIndexEntry":
        return cls(
            relative_path=str(payload["relative_path"]),
            size_bytes=int(payload["size_bytes"]),
            mtime_ns=int(payload["mtime_ns"]),
            ctime_ns=int(payload["ctime_ns"]),
            file_id=str(payload["file_id"]),
            content_hash=str(payload["content_hash"]),
            symbols=tuple(str(item) for item in payload.get("symbols", [])),
            imports=tuple(str(item) for item in payload.get("imports", [])),
            local_import_paths=tuple(str(item) for item in payload.get("local_import_paths", [])),
            imported_by_paths=tuple(str(item) for item in payload.get("imported_by_paths", [])),
            symbol_occurrences=tuple(
                SymbolOccurrence.from_dict(item)
                for item in payload.get("symbol_occurrences", [])
                if isinstance(item, dict)
            ),
            role=str(payload.get("role", payload.get("file_role", "source"))),
            language=str(payload.get("language", "")),
            landmarks=tuple(str(item) for item in payload.get("landmarks", [])),
        )


@dataclass(frozen=True)
class RepositoryIndexBuildResult:
    entries: dict[str, RepositoryFileIndexEntry]
    stats: dict[str, Any]


def _line_number_for_offset(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


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


def _file_identity(stat: os.stat_result) -> str:
    return f"{getattr(stat, 'st_dev', 0)}:{getattr(stat, 'st_ino', 0)}"


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


def _read_cache_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    manifest = payload.get("manifest", {})
    return manifest if isinstance(manifest, dict) else {}


def _write_cache(
    path: Path,
    repo: Path,
    entries: dict[str, RepositoryFileIndexEntry],
    manifest: dict[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format_version": _INDEX_FORMAT_VERSION,
        "repo": str(repo.resolve()),
        "manifest": manifest,
        "entries": {relative_path: entry.to_dict() for relative_path, entry in entries.items()},
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _language_for_path(relative_path: str) -> str:
    suffix = Path(relative_path).suffix.lower()
    if suffix in {".tsx", ".jsx"}:
        return "typescript-react" if suffix == ".tsx" else "javascript-react"
    return {
        ".js": "javascript",
        ".mjs": "javascript",
        ".cjs": "javascript",
        ".ts": "typescript",
        ".py": "python",
        ".css": "css",
        ".scss": "scss",
        ".sass": "sass",
        ".json": "json",
        ".md": "markdown",
    }.get(suffix, suffix.lstrip(".") or "text")


def classify_file_role(relative_path: str, text: str = "") -> str:
    """Classify a path using stable path/name signals before content hints."""
    path = Path(relative_path)
    parts = {part.casefold() for part in path.parts}
    name = path.name.casefold()
    suffix = path.suffix.casefold()
    if parts & _VENDOR_PARTS:
        return "vendor"
    if (
        parts & _GENERATED_PARTS
        or ".generated." in name
        or name.endswith((".min.js", ".min.css", ".map"))
    ):
        return "generated"
    if (
        "/tests/" in f"/{relative_path.casefold()}"
        or "/test/" in f"/{relative_path.casefold()}"
        or "/spec/" in f"/{relative_path.casefold()}"
        or re.search(r"(?:^|[._-])(test|spec)(?:[._-]|$)", name)
    ):
        return "test"
    if suffix in _STYLE_SUFFIXES:
        return "style"
    if suffix in _DOC_SUFFIXES or name.startswith(("readme", "changelog", "license")):
        return "docs"
    if name in _CONFIG_NAMES or suffix in _CONFIG_SUFFIXES:
        return "config"
    if text and re.search(r"^\s*#!.*\b(?:bash|sh|python)\b", text):
        return "source"
    return "source"


def _file_landmarks(relative_path: str, text: str, role: str) -> tuple[str, ...]:
    path = Path(relative_path)
    name = path.name.casefold()
    suffix = path.suffix.casefold()
    values: list[str] = []
    if role == "test":
        values.append("test-layout")
    if role == "style":
        values.append("style")
    if role == "config" and name in _CONFIG_NAMES:
        values.append("config")
    if name in {"package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"}:
        values.append("dependency-manifest")
    if name.startswith("vite.config"):
        values.append("vite-config")
    if suffix in {".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".py", ".go", ".rs"} and (
        name in {
            "main.js",
            "main.jsx",
            "main.ts",
            "main.tsx",
            "index.js",
            "index.jsx",
            "index.ts",
            "index.tsx",
            "app.py",
            "manage.py",
            "server.js",
            "server.ts",
            "cli.py",
        }
        or "entrypoint" in name
    ):
        values.append("entrypoint")
    if suffix in {".tsx", ".jsx"}:
        if name in {"main.tsx", "main.jsx", "index.tsx", "index.jsx", "client.tsx", "client.jsx"}:
            values.append("react-entrypoint")
        if re.search(r"\b(?:createRoot|ReactDOM\.render|hydrateRoot)\b", text):
            values.append("react-root")
        if re.search(r"<([A-Z][A-Za-z0-9]*|[a-z]+)[\s/>]", text):
            values.append("react-component")
    if name.startswith("app.") or path.stem.casefold() in {"app", "root", "layout"}:
        values.append("app-shell")
    if re.search(r"\b(?:useState|useReducer|createContext|Redux|zustand|recoil)\b", text) or any(
        term in path.stem.casefold() for term in ("state", "store", "reducer", "context")
    ):
        values.append("state")
    if re.search(r"\b(?:interface|type|enum)\s+[A-Za-z_]", text) or "types" in path.parts:
        values.append("types")
    if re.search(r"\b(?:BrowserRouter|HashRouter|createBrowserRouter|Routes|Route)\b", text):
        values.append("router")
    return _ordered_unique(values, 12)


def _repository_revision(repo: Path) -> tuple[str, str]:
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        revision = ""
    if revision:
        return revision, "git"
    return hashlib.sha256(str(repo).encode("utf-8")).hexdigest(), "path"


def extract_symbol_occurrences(path: Path, text: str) -> tuple[SymbolOccurrence, ...]:
    occurrences: list[SymbolOccurrence] = []
    patterns = (
        (r"\b(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)\b", 1, 0, "function"),
        (r"\b(?:class|interface|enum|struct)\s+([A-Za-z_][A-Za-z0-9_]*)\b", 1, 0, "type"),
        (r"\b(const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=", 2, 0, "variable"),
        (r"^\s*(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)\b", 1, re.MULTILINE, "function"),
        (r"\bfunc\s+([A-Za-z_][A-Za-z0-9_]*)\b", 1, 0, "function"),
        (r"\b(?:test|it|describe)\(\s*['\"]([^'\"]+)", 1, 0, "test"),
        (
            r"\b(?:app|router)\.(?:get|post|put|patch|delete|use)\(\s*['\"]([^'\"]+)",
            1,
            0,
            "route",
        ),
    )
    for pattern, group_index, flags, kind in patterns:
        for match in re.finditer(pattern, text, flags):
            occurrences.append(
                SymbolOccurrence(
                    line_number=_line_number_for_offset(text, match.start()),
                    name=match.group(group_index),
                    kind=kind,
                )
            )
    for export_match in re.finditer(r"\bexport\s*\{([^}]+)\}", text):
        line_number = _line_number_for_offset(text, export_match.start())
        for piece in export_match.group(1).split(","):
            candidate = piece.strip().split(" as ")[0].strip()
            if candidate:
                occurrences.append(
                    SymbolOccurrence(line_number=line_number, name=candidate, kind="export")
                )
    return tuple(occurrences)


def _extract_symbols(path: Path, text: str) -> tuple[str, ...]:
    return _ordered_unique(
        [item.name for item in extract_symbol_occurrences(path, text)] + [path.stem],
        _SYMBOL_LIMIT,
    )


def _extract_imports(text: str) -> tuple[str, ...]:
    matches: list[tuple[int, str]] = []
    patterns = (
        # ES module source imports, including side-effect-only CSS imports.
        r"\bfrom\s*['\"]([^'\"]+)['\"]",
        r"\bimport\s*['\"]([^'\"]+)['\"]",
        r"\bimport\s*\(\s*['\"]([^'\"]+)['\"]\s*\)",
        r"\brequire\(\s*['\"]([^'\"]+)['\"]\s*\)",
        # Python's `from package.module import name` form.
        r"^\s*from\s+([A-Za-z_][A-Za-z0-9_.]*)\s+import\b",
    )
    for pattern in patterns:
        matches.extend((match.start(), match.group(1)) for match in re.finditer(pattern, text, re.MULTILINE))
    values = [value for _, value in sorted(matches, key=lambda item: (item[0], item[1]))]
    return _ordered_unique(values, _IMPORT_LIMIT)


def _resolve_local_imports(
    relative_path: str, imports: tuple[str, ...], candidate_paths: set[str]
) -> tuple[str, ...]:
    base_dir = Path(relative_path).parent
    resolved: list[str] = []
    for value in imports:
        if value.startswith(("./", "../")):
            target_path = Path(os.path.normpath((base_dir / value).as_posix()))
            candidates = [target_path]
            if not target_path.suffix:
                candidates.extend(target_path.with_suffix(suffix) for suffix in _LOCAL_IMPORT_SUFFIXES)
                candidates.extend(target_path / f"index{suffix}" for suffix in _LOCAL_IMPORT_SUFFIXES)
        elif Path(relative_path).suffix == ".py" and re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*",
            value,
        ):
            # Resolve absolute imports that name modules inside the repository.
            target_path = Path(value.replace(".", "/"))
            candidates = [target_path.with_suffix(".py"), target_path / "__init__.py"]
        else:
            continue
        for candidate in candidates:
            normalized = candidate.as_posix()
            if normalized in candidate_paths:
                resolved.append(normalized)
                break
    return _ordered_unique(resolved, _LOCAL_IMPORT_LIMIT)


def _reverse_local_imports(
    entries: dict[str, RepositoryFileIndexEntry],
) -> dict[str, tuple[str, ...]]:
    imported_by: dict[str, list[str]] = {path: [] for path in entries}
    for relative_path, entry in entries.items():
        for target in entry.local_import_paths:
            if target in imported_by:
                imported_by[target].append(relative_path)
    return {
        path: _ordered_unique(paths, _LOCAL_IMPORT_LIMIT)
        for path, paths in imported_by.items()
    }


def build_repository_index(repo: Path, files: Iterable[Path]) -> RepositoryIndexBuildResult:
    repo = repo.resolve()
    file_list = tuple(sorted((Path(path).resolve() for path in files), key=lambda path: path.as_posix()))
    candidate_paths = {path.relative_to(repo).as_posix() for path in file_list}
    cache_path = repository_index_path(repo)
    cached_entries = _read_cache(cache_path)
    cached_manifest = _read_cache_manifest(cache_path)
    revision, revision_source = _repository_revision(repo)
    entries: dict[str, RepositoryFileIndexEntry] = {}
    reused_files = 0
    metadata_fast_path_reused_files = 0
    rebuilt_files = 0
    skipped_files = 0
    validated_files = 0
    metadata_checked_files = 0
    for path in file_list:
        relative_path = path.relative_to(repo).as_posix()
        try:
            stat = path.stat()
        except (OSError, UnicodeDecodeError):
            skipped_files += 1
            continue
        cached_entry = cached_entries.get(relative_path)
        metadata_checked_files += 1
        if (
            cached_entry is not None
            and cached_entry.size_bytes == stat.st_size
            and cached_entry.mtime_ns == stat.st_mtime_ns
            and cached_entry.ctime_ns == stat.st_ctime_ns
            and cached_entry.file_id == _file_identity(stat)
        ):
            # Stable size, timestamps, and file identity let us reuse parsed
            # metadata without reopening the file. Any metadata change falls
            # through to content-hash validation.
            entries[relative_path] = RepositoryFileIndexEntry(
                relative_path=cached_entry.relative_path,
                size_bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                ctime_ns=stat.st_ctime_ns,
                file_id=_file_identity(stat),
                content_hash=cached_entry.content_hash,
                symbols=cached_entry.symbols,
                imports=cached_entry.imports,
                local_import_paths=cached_entry.local_import_paths,
                imported_by_paths=(),
                symbol_occurrences=cached_entry.symbol_occurrences,
                role=cached_entry.role,
                language=cached_entry.language or _language_for_path(relative_path),
                landmarks=cached_entry.landmarks,
            )
            reused_files += 1
            metadata_fast_path_reused_files += 1
            continue
        try:
            raw = path.read_bytes()
            content_hash = hashlib.sha256(raw).hexdigest()
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            skipped_files += 1
            continue
        validated_files += 1
        if cached_entry is not None and cached_entry.content_hash == content_hash:
            entries[relative_path] = RepositoryFileIndexEntry(
                relative_path=cached_entry.relative_path,
                size_bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                ctime_ns=stat.st_ctime_ns,
                file_id=_file_identity(stat),
                content_hash=content_hash,
                symbols=cached_entry.symbols,
                imports=cached_entry.imports,
                local_import_paths=cached_entry.local_import_paths,
                imported_by_paths=(),
                symbol_occurrences=cached_entry.symbol_occurrences,
                role=cached_entry.role,
                language=cached_entry.language or _language_for_path(relative_path),
                landmarks=cached_entry.landmarks,
            )
            reused_files += 1
            continue
        role = classify_file_role(relative_path, text)
        entry = RepositoryFileIndexEntry(
            relative_path=relative_path,
            size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            ctime_ns=stat.st_ctime_ns,
            file_id=_file_identity(stat),
            content_hash=content_hash,
            symbols=_extract_symbols(path, text),
            imports=_extract_imports(text),
            local_import_paths=(),
            imported_by_paths=(),
            symbol_occurrences=extract_symbol_occurrences(path, text),
            role=role,
            language=_language_for_path(relative_path),
            landmarks=_file_landmarks(relative_path, text, role),
        )
        entries[relative_path] = entry
        rebuilt_files += 1
    for relative_path, entry in tuple(entries.items()):
        entries[relative_path] = RepositoryFileIndexEntry(
            relative_path=entry.relative_path,
            size_bytes=entry.size_bytes,
            mtime_ns=entry.mtime_ns,
            ctime_ns=entry.ctime_ns,
            file_id=entry.file_id,
            content_hash=entry.content_hash,
            symbols=entry.symbols,
            imports=entry.imports,
            local_import_paths=_resolve_local_imports(
                entry.relative_path, entry.imports, candidate_paths
            ),
            imported_by_paths=(),
            symbol_occurrences=entry.symbol_occurrences,
            role=entry.role,
            language=entry.language,
            landmarks=entry.landmarks,
        )
    imported_by_paths = _reverse_local_imports(entries)
    for relative_path, entry in tuple(entries.items()):
        entries[relative_path] = RepositoryFileIndexEntry(
            relative_path=entry.relative_path,
            size_bytes=entry.size_bytes,
            mtime_ns=entry.mtime_ns,
            ctime_ns=entry.ctime_ns,
            file_id=entry.file_id,
            content_hash=entry.content_hash,
            symbols=entry.symbols,
            imports=entry.imports,
            local_import_paths=entry.local_import_paths,
            imported_by_paths=imported_by_paths.get(entry.relative_path, ()),
            symbol_occurrences=entry.symbol_occurrences,
            role=entry.role,
            language=entry.language,
            landmarks=entry.landmarks,
        )
    indexed_at = datetime.now(timezone.utc).isoformat()
    role_counts: dict[str, int] = {}
    landmark_records: list[dict[str, str]] = []
    test_files: list[str] = []
    test_directories: set[str] = set()
    for relative_path, entry in sorted(entries.items()):
        role_counts[entry.role] = role_counts.get(entry.role, 0) + 1
        if entry.role == "test":
            test_files.append(relative_path)
            test_directories.add(str(Path(relative_path).parent.as_posix()))
        landmark_records.extend(
            {"path": relative_path, "kind": landmark} for landmark in entry.landmarks
        )
    manifest = {
        "revision": revision,
        "revision_source": revision_source,
        "indexed_at": indexed_at,
        "indexed_files": len(entries),
        "content_hashes_validated": validated_files,
        "role_counts": dict(sorted(role_counts.items())),
    }
    _write_cache(cache_path, repo, entries, manifest)
    revision_match = cached_manifest.get("revision") == revision
    stats = {
        "cache_path": str(cache_path),
        "format_version": _INDEX_FORMAT_VERSION,
        "revision": revision,
        "revision_source": revision_source,
        "indexed_at": indexed_at,
        "freshness": {
            "status": "fresh" if rebuilt_files == 0 and revision_match else "refreshed",
            "revision_match": revision_match,
            "content_hashes_validated": validated_files,
            "cached_revision": cached_manifest.get("revision"),
        },
        "freshness_status": "fresh" if rebuilt_files == 0 and revision_match else "refreshed",
        "indexed_files": len(entries),
        "reused_files": reused_files,
        "metadata_fast_path_reused_files": metadata_fast_path_reused_files,
        "rebuilt_files": rebuilt_files,
        "skipped_files": skipped_files,
        "metadata_checked_files": metadata_checked_files,
        "hashes_avoided": metadata_fast_path_reused_files,
        "content_hashes_validated": validated_files,
        "symbol_count": sum(len(entry.symbols) for entry in entries.values()),
        "import_count": sum(len(entry.imports) for entry in entries.values()),
        "local_import_count": sum(len(entry.local_import_paths) for entry in entries.values()),
        "role_counts": dict(sorted(role_counts.items())),
        "landmarks": sorted(landmark_records, key=lambda item: (item["path"], item["kind"])),
        "test_layout": {
            "directories": sorted(test_directories),
            "files": test_files,
        },
    }
    return RepositoryIndexBuildResult(entries=entries, stats=stats)
