from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import sqlite3
import subprocess
import time
from collections import Counter
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from agenvantage.chunk_cache import CachedChunk, ChunkCache, ChunkCacheKey
from agenvantage.context_planner import ContextPlan, plan_context
from agenvantage.graph_backend import (
    GRAPHIFY_EXPECTED_COMMIT,
    GRAPHIFY_EXPECTED_VERSION,
    GraphCandidate,
)
from agenvantage.graph_policy import decide_graph_policy
from agenvantage.graphify_adapter import GraphifyAdapter
from agenvantage.graphify_runner import run_graphify
from agenvantage.pruning import goal_hints_from_context, prune_lines
from agenvantage.repo_index import (
    RepositoryFileIndexEntry,
    build_repository_index,
    extract_symbol_occurrences,
)
from agenvantage.repo_map import build_repository_map
from agenvantage.repo_provenance import ProvenanceSection, build_repo_provenance_sections
from agenvantage.tokenizer import TokenCounter

_SUPPORTED_SUFFIXES = {
    ".c",
    ".cpp",
    ".cs",
    ".css",
    ".go",
    ".graphql",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".mjs",
    ".py",
    ".rs",
    ".cfg",
    ".conf",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}
_SUPPORTED_NAMES = {
    ".env.example",
    ".env.sample",
    ".env.template",
    "constraints.txt",
    "dockerfile",
    "makefile",
    "package.json",
    "pipfile",
    "pipfile.lock",
    "poetry.lock",
    "requirements.txt",
    "readme",
    "pyproject.toml",
    "readme.md",
    "uv.lock",
}
_SUPPORTED_NAME_PREFIXES = (
    "containerfile.",
    "dockerfile.",
    "makefile.",
    "readme.",
)
_SAFE_ENV_EXAMPLE_NAMES = frozenset(
    {
        ".env.example",
        ".env.sample",
        ".env.template",
    }
)
_IGNORED_PARTS = {
    ".git",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "artifacts",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "vendor",
}
_STOP_WORDS = {
    "about",
    "and",
    "behavior",
    "code",
    "does",
    "explain",
    "find",
    "for",
    "from",
    "happens",
    "how",
    "including",
    "incoming",
    "implementation",
    "is",
    "its",
    "project",
    "requests",
    "show",
    "that",
    "the",
    "then",
    "test",
    "tests",
    "this",
    "use",
    "with",
    "where",
    "when",
    "what",
}
_MIN_RELEVANCE_SCORE = 6.0
_CHUNK_SYMBOL_LOOKBACK_LINES = 120
_CHUNK_SYMBOL_LIMIT = 8
_TERM_EXPANSIONS = {
    "expiration": ("expire", "ttl"),
    "extract": ("extraction",),
    "extraction": ("extract",),
    "expiry": ("expire", "ttl"),
    "limit": ("limiter", "limiting", "max", "maximum"),
    "limiter": ("limit", "limiting"),
    "limiting": ("limit", "limiter"),
    "max": ("limit", "maximum"),
    "maximum": ("max", "limit"),
    "plan": ("planning",),
    "planning": ("plan",),
    "size": ("byte", "bytes"),
    "byte": ("bytes", "size"),
    "bytes": ("byte", "size"),
    "dashboard": ("results", "summary", "overview", "insight", "stats"),
    "visualization": ("chart", "diagram", "graph", "meter", "progress"),
    "workout": ("activity", "exercise", "fitness"),
    "streak": ("consecutive", "history", "series"),
}
_DEFAULT_INSTRUCTIONS = (
    "You are helping with a software engineering task. Base conclusions on the "
    "provided repository excerpts, cite source chunk identifiers when explaining "
    "behavior, state when context is insufficient, and avoid inventing files, "
    "tests, or runtime results."
)
_FEATURE_TEST_TERMS = {
    "test",
    "tests",
    "spec",
    "coverage",
    "smoke",
    "verify",
    "validation",
}
_FEATURE_CONFIG_TERMS = {
    "config",
    "configured",
    "configuration",
    "schema",
    "manifest",
    "env",
    "flag",
    "flags",
    "setting",
    "settings",
}
_TEST_PATH_MARKERS = (
    ".test.",
    ".spec.",
    "/tests/",
    "/test/",
    "/qa/",
    "tests/",
    "test/",
    "qa/",
)
_CONFIG_SUFFIXES = {".cfg", ".conf", ".ini", ".json", ".toml", ".yaml", ".yml"}
_CONFIG_NAMES = {
    "package.json",
    "pyproject.toml",
    "tsconfig.json",
    "manifest.json",
    ".env.example",
}
_FEATURE_RESERVED_COUNTS = {
    "edit_targets": 2,
    "test_targets": 2,
    "config_targets": 1,
    "supporting_targets": 1,
}
# Feature packs should stop after high-signal edit/test/config coverage instead
# of spending the caller's whole budget on low-marginal repository context.
_FEATURE_TARGET_CONTEXT_BUDGET = 1_800
_FEATURE_CHUNK_OVERLAP_LINES = 4
_LIBRARY_CLI_TASK_TERMS = {
    "api",
    "cli",
    "command",
    "command-line",
    "commandline",
    "console",
    "deprecation",
    "library",
    "module",
    "package",
    "public",
    "terminal",
}
_GUARD_TEST_SIGNAL_PATTERNS: tuple[tuple[str, re.Pattern[str], float], ...] = (
    (
        "test_imports",
        re.compile(r"\btest[_ -]?imports?\b|\bimports?[_ -]?test\b", re.IGNORECASE),
        9.0,
    ),
    ("sys_modules", re.compile(r"\bsys\s*\.\s*modules\b", re.IGNORECASE), 9.0),
    (
        "lightweight_imports",
        re.compile(
            r"(?<![A-Za-z0-9])(?:lightweight|cheap|minimal)[-_ ]imports?\b",
            re.IGNORECASE,
        ),
        8.0,
    ),
    (
        "lazy_imports",
        re.compile(
            r"(?<![A-Za-z0-9])(?:lazy|deferred|on[- ]demand)[-_ ]imports?\b|\bimportlib(?:\.import_module)?\b",
            re.IGNORECASE,
        ),
        8.0,
    ),
    (
        "compatibility",
        re.compile(r"\bbackward[- ]compat(?:ibility)?\b|\bcompatib\w*\b", re.IGNORECASE),
        5.0,
    ),
    (
        "public_api",
        re.compile(
            r"\bpublic[- ]api\b|\bapi[- ]surface\b|\b__all__\b|\bexports?\b",
            re.IGNORECASE,
        ),
        5.0,
    ),
    (
        "deprecation_contracts",
        re.compile(r"\bdeprecat\w*\b|\blegacy[- ]api\b", re.IGNORECASE),
        5.0,
    ),
)
_GUARD_TEST_PATH_TERMS = {
    "api",
    "compat",
    "compatibility",
    "deprecat",
    "guard",
    "import",
    "public",
}
_PRIVATE_KEY_BLOCK_PATTERN = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_SECRET_VALUE_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "openai_api_key",
        re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
        "[REDACTED_OPENAI_KEY]",
    ),
    (
        "github_token",
        re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{16,}\b"),
        "[REDACTED_GITHUB_TOKEN]",
    ),
    (
        "github_fine_grained_token",
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b"),
        "[REDACTED_GITHUB_TOKEN]",
    ),
    (
        "aws_access_key",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        "[REDACTED_AWS_ACCESS_KEY]",
    ),
    (
        "bearer_token",
        re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/=-]{16,}\b"),
        r"\1[REDACTED_BEARER_TOKEN]",
    ),
)
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"""(?im)
    \b
    (?P<key>[A-Z0-9_.-]*
        (?:
            API[_-]?KEY
            |SECRET
            |TOKEN
            |PASSWORD
            |PASSWD
            |PRIVATE[_-]?KEY
            |CLIENT[_-]?SECRET
            |DATABASE[_-]?URL
            |AUTHORIZATION
        )
        [A-Z0-9_.-]*
    )
    (?P<sep>\s*[:=]\s*)
    (?P<quote>['"]?)
    (?P<value>[^'"\s#,\]}]+)
    (?P=quote)
    """,
    re.VERBOSE,
)
_CAMEL_BOUNDARY_PATTERN = re.compile(r"([a-z0-9])([A-Z])")
_TERM_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_-]{1,}")
_COMMAND_MARKERS = {"cli", "command", "subcommand"}
_SECRET_ASSIGNMENT_MARKERS = (
    "api_key",
    "apikey",
    "secret",
    "token",
    "password",
    "passwd",
    "private_key",
    "private-key",
    "client_secret",
    "client-secret",
    "database_url",
    "database-url",
    "authorization",
)


@dataclass(frozen=True)
class CodeChunk:
    chunk_id: str
    relative_path: str
    display_path: str
    repo_label: str
    repo_path: str
    start_line: int
    end_line: int
    text: str
    # Token count for the separator-wrapped block as inserted into prompts.
    tokens: int
    chunk_symbols: tuple[str, ...] = ()
    file_symbols: tuple[str, ...] = ()
    file_imports: tuple[str, ...] = ()
    file_local_import_paths: tuple[str, ...] = ()
    file_imported_by_paths: tuple[str, ...] = ()
    redaction_count: int = 0
    redaction_types: tuple[str, ...] = ()
    score: float = 0.0
    matched_terms: tuple[str, ...] = ()
    addition_tokens: int = 0
    file_role: str = "source"
    file_landmarks: tuple[str, ...] = ()
    graph_score: float = 0.0
    graph_relations: tuple[str, ...] = ()
    graph_confidences: tuple[str, ...] = ()
    graph_communities: tuple[str, ...] = ()
    graph_provenance: tuple[str, ...] = ()
    graph_hops: tuple[int, ...] = ()

    def render(self) -> str:
        return (
            f"[SOURCE:{self.chunk_id}]\n"
            f"```{_language_for_path(self.display_path)}\n{self.text.rstrip()}\n```"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.chunk_id,
            "path": self.display_path,
            "relative_path": self.relative_path,
            "repo_label": self.repo_label,
            "repo_path": self.repo_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            # Preserve the immutable selected excerpt so validators score the
            # exact handoff rather than rereading mutable source files.
            "text": self.text,
            "tokens": self.tokens,
            "redaction_count": self.redaction_count,
            "redaction_types": list(self.redaction_types),
            "score": round(self.score, 3),
            "matched_terms": list(self.matched_terms),
            "graph_score": round(self.graph_score, 4),
            "graph_relations": list(self.graph_relations),
            "graph_confidences": list(self.graph_confidences),
            "graph_communities": list(self.graph_communities),
            "graph_provenance": list(self.graph_provenance),
            "graph_hops": list(self.graph_hops),
        }


@dataclass(frozen=True)
class RepositoryInput:
    root: Path
    label: str


def _language_for_path(relative_path: str) -> str:
    suffix = Path(relative_path).suffix.lower().lstrip(".")
    return {"tsx": "tsx", "ts": "typescript", "py": "python", "md": "markdown"}.get(
        suffix, suffix or "text"
    )


@lru_cache(maxsize=16_384)
def _term_variants(term: str) -> tuple[str, ...]:
    variants = {term, *(piece for piece in term.split("-") if piece and piece != term)}
    for candidate in tuple(variants):
        variants.update(_TERM_EXPANSIONS.get(candidate, ()))
        if len(candidate) > 3:
            if candidate.endswith("ies") and len(candidate) > 4:
                variants.add(f"{candidate[:-3]}y")
            elif candidate.endswith(("xes", "zes", "ches", "shes")):
                variants.add(candidate[:-2])
            elif candidate.endswith("s") and not candidate.endswith(("ss", "us", "is")):
                variants.add(candidate[:-1])
    return tuple(
        candidate
        for candidate in sorted(variants)
        if len(candidate) > 1 and candidate not in _STOP_WORDS
    )


def _terms(value: str) -> list[str]:
    split_camel = _CAMEL_BOUNDARY_PATTERN.sub(r"\1 \2", value).replace("_", " ")
    terms: list[str] = []
    for raw_term in _TERM_PATTERN.findall(split_camel):
        term = raw_term.lower()
        terms.extend(_term_variants(term))
    return terms


def _literal_terms(value: str) -> list[str]:
    split_camel = _CAMEL_BOUNDARY_PATTERN.sub(r"\1 \2", value).replace("_", " ")
    return [term.lower() for term in _TERM_PATTERN.findall(split_camel)]


def _query_concepts(value: str) -> tuple[tuple[str, set[str]], ...]:
    split_camel = _CAMEL_BOUNDARY_PATTERN.sub(r"\1 \2", value).replace("_", " ")
    concepts: list[tuple[str, set[str]]] = []
    for raw_term in _TERM_PATTERN.findall(split_camel):
        label = raw_term.lower()
        if len(label) <= 1 or label in _STOP_WORDS:
            continue
        concepts.append((label, set(_term_variants(label))))
    return tuple(concepts)


def _command_anchor_terms(task: str, repo_label: str) -> tuple[str, ...]:
    """Keep command names that generic stop-word filtering would discard."""
    raw_terms = [term.lower() for term in _TERM_PATTERN.findall(task)]
    repo_terms = {
        term.lower()
        for term in _TERM_PATTERN.findall(
            _CAMEL_BOUNDARY_PATTERN.sub(r"\1 \2", repo_label).replace("_", " ")
        )
    }
    anchors: list[str] = []
    for index, term in enumerate(raw_terms[:-1]):
        if term not in repo_terms and term not in _COMMAND_MARKERS:
            continue
        candidate = raw_terms[index + 1]
        if candidate not in _STOP_WORDS or candidate in {"explain", "show"}:
            anchors.append(candidate)
    return _ordered_unique(anchors, 8)


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


def _redact_sensitive_text(text: str) -> tuple[str, dict[str, int]]:
    redaction_counts: Counter[str] = Counter()

    def _replace_private_key(match: re.Match[str]) -> str:
        redaction_counts["private_key_block"] += 1
        line_count = match.group(0).count("\n") + 1
        return "\n".join(
            ["[REDACTED_PRIVATE_KEY]"]
            + ["[REDACTED_PRIVATE_KEY_CONTINUED]"] * (line_count - 1)
        )

    def _replace_assignment(match: re.Match[str]) -> str:
        value = match.group("value")
        if "[REDACTED_" in value or "[REDACTED_" in match.group(0):
            return match.group(0)
        redaction_counts["secret_assignment"] += 1
        quote = match.group("quote")
        return f"{match.group('key')}{match.group('sep')}{quote}[REDACTED_SECRET]{quote}"

    redacted = text
    lowered = text.lower()
    if "private key" in lowered:
        redacted = _PRIVATE_KEY_BLOCK_PATTERN.sub(_replace_private_key, redacted)
    for label, pattern, replacement in _SECRET_VALUE_PATTERNS:
        if (
            (label == "openai_api_key" and "sk-" not in redacted)
            or (
                label == "github_token"
                and not any(
                    marker in redacted
                    for marker in ("ghp_", "gho_", "ghu_", "ghs_", "ghr_")
                )
            )
            or (label == "github_fine_grained_token" and "github_pat_" not in redacted)
            or (label == "aws_access_key" and "AKIA" not in redacted)
            or (label == "bearer_token" and "bearer" not in redacted.lower())
        ):
            continue
        redacted, count = pattern.subn(replacement, redacted)
        if count:
            redaction_counts[label] += count
    if any(marker in lowered for marker in _SECRET_ASSIGNMENT_MARKERS):
        redacted = _SECRET_ASSIGNMENT_PATTERN.sub(_replace_assignment, redacted)
    return redacted, dict(redaction_counts)


def _chunk_local_symbols(
    symbol_occurrences: tuple[Any, ...],
    *,
    start_line: int,
    end_line: int,
) -> tuple[str, ...]:
    window_start = max(1, start_line - _CHUNK_SYMBOL_LOOKBACK_LINES)
    nearby = [
        (item.name, item.kind)
        for item in symbol_occurrences
        if window_start <= int(item.line_number) <= end_line
    ]
    prioritized = [
        name
        for name, kind in reversed(nearby)
        if kind in {"function", "type", "route", "export"}
    ]
    fallback = [name for name, _ in reversed(nearby)]
    return _ordered_unique([*prioritized, *fallback], _CHUNK_SYMBOL_LIMIT)


def _eligible_file(path: Path, repo: Path) -> bool:
    relative = path.relative_to(repo)
    lowered_parts = {part.lower() for part in relative.parts}
    if lowered_parts & _IGNORED_PARTS:
        return False
    lowered_name = path.name.lower()
    if lowered_name.startswith(".env") and lowered_name not in _SAFE_ENV_EXAMPLE_NAMES:
        return False
    return (
        path.suffix.lower() in _SUPPORTED_SUFFIXES
        or lowered_name in _SUPPORTED_NAMES
        or lowered_name.startswith(_SUPPORTED_NAME_PREFIXES)
    )


def _is_test_path(relative_path: str) -> bool:
    lowered = relative_path.lower()
    name = Path(lowered).name
    return (
        any(marker in lowered for marker in _TEST_PATH_MARKERS)
        or ".test." in name
        or ".spec." in name
        or name.startswith("test_")
        or name.endswith("-test.js")
        or name.endswith("-test.mjs")
        or name.endswith("-spec.js")
        or name.endswith("-spec.mjs")
    )


def _is_config_path(relative_path: str) -> bool:
    path = Path(relative_path)
    return (
        path.suffix.lower() in _CONFIG_SUFFIXES
        or path.name.lower() in _CONFIG_NAMES
        or path.stem.lower() in {"config", "configuration", "settings"}
    )


def _is_doc_like_path(relative_path: str) -> bool:
    lowered = relative_path.lower()
    return lowered.endswith(".md") or "/docs/" in lowered or lowered.startswith("docs/")


def _path_terms(relative_path: str) -> set[str]:
    return set(_terms(relative_path))


def _repo_display_labels(repos: Iterable[Path]) -> tuple[str, ...]:
    seen: Counter[str] = Counter()
    labels: list[str] = []
    for repo in repos:
        label = repo.name or "repo"
        seen[label] += 1
        labels.append(label if seen[label] == 1 else f"{label}-{seen[label]}")
    return tuple(labels)


def _repo_inputs(repos: Iterable[Path]) -> tuple[RepositoryInput, ...]:
    resolved = tuple(repo.resolve() for repo in repos)
    if not resolved:
        raise ValueError("At least one repository path is required.")
    if len(resolved) == 1:
        return (RepositoryInput(resolved[0], resolved[0].name or "repo"),)
    labels = _repo_display_labels(resolved)
    return tuple(
        RepositoryInput(root=repo, label=label) for repo, label in zip(resolved, labels, strict=True)
    )


def _matches_any_glob(relative_path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(relative_path, pattern) for pattern in patterns)


_BUILTIN_EXCLUDE_GLOBS = (".agenvantage/**",)


def source_files(
    repo: Path,
    max_file_bytes: int = 300_000,
    *,
    include_globs: tuple[str, ...] = (),
    exclude_globs: tuple[str, ...] = (),
) -> tuple[Path, ...]:
    repo = repo.resolve()
    if not repo.is_dir():
        raise ValueError(f"Repository path does not exist: {repo}")

    git_files = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    if git_files.returncode == 0:
        git_paths = {repo / item for item in git_files.stdout.split("\0") if item}
        candidates = iter(git_paths)
    else:
        candidates = (path for path in repo.rglob("*") if path.is_file())

    files = []
    for path in candidates:
        relative_path = path.relative_to(repo).as_posix()
        if (
            path.is_file()
            and _eligible_file(path, repo)
            and path.stat().st_size <= max_file_bytes
            and (not include_globs or _matches_any_glob(relative_path, include_globs))
            and not _matches_any_glob(relative_path, _BUILTIN_EXCLUDE_GLOBS + exclude_globs)
        ):
            files.append(path)
    return tuple(sorted(files))


def chunks_for_repo(
    repo: Path,
    counter: TokenCounter,
    chunk_lines: int = 50,
    overlap_lines: int = 8,
    *,
    files: Iterable[Path] | None = None,
    repo_label: str | None = None,
    file_index: dict[str, RepositoryFileIndexEntry] | None = None,
    chunk_cache_stats: dict[str, Any] | None = None,
) -> tuple[CodeChunk, ...]:
    if chunk_lines <= 0 or overlap_lines < 0 or overlap_lines >= chunk_lines:
        raise ValueError("Chunk settings require chunk_lines > overlap_lines >= 0.")

    chunks: list[CodeChunk] = []
    stride = chunk_lines - overlap_lines
    repo = repo.resolve()
    file_list = tuple(files or source_files(repo))
    cache: ChunkCache | None = None
    cached_payloads: dict[ChunkCacheKey, Any] = {}
    keys_by_path: dict[str, ChunkCacheKey] = {}
    pending_cache_entries: list[CachedChunk] = []
    if file_index is not None:
        try:
            cache = ChunkCache(repo)
            tokenizer_profile = f"{counter.model}:{counter.encoding_name}"
            for path in file_list:
                relative = path.relative_to(repo).as_posix()
                indexed_entry = file_index.get(relative)
                if indexed_entry is None:
                    continue
                keys_by_path[relative] = ChunkCacheKey(
                    relative,
                    indexed_entry.content_hash,
                    tokenizer_profile,
                    chunk_lines,
                    overlap_lines,
                )
            cached_payloads = cache.load_many(keys_by_path.values())
        except (OSError, sqlite3.Error, ValueError):
            cache = None
            cached_payloads = {}
            keys_by_path = {}

    for path in file_list:
        relative = path.relative_to(repo).as_posix()
        display_path = f"{repo_label}/{relative}" if repo_label else relative
        indexed_entry = file_index.get(relative) if file_index is not None else None
        cache_key = keys_by_path.get(relative)
        cached_payload = cached_payloads.get(cache_key) if cache_key is not None else None
        cached_chunks = cached_payload.get("chunks") if isinstance(cached_payload, dict) else None
        if isinstance(cached_chunks, list):
            valid_payload = True
            restored: list[CodeChunk] = []
            for item in cached_chunks:
                if not isinstance(item, dict):
                    valid_payload = False
                    break
                try:
                    start_line = int(item["start_line"])
                    end_line = int(item["end_line"])
                    text = str(item["text"])
                    tokens = int(item["tokens"])
                    restored.append(
                        CodeChunk(
                            chunk_id=f"{display_path}#L{start_line}-L{end_line}",
                            relative_path=relative,
                            display_path=display_path,
                            repo_label=repo_label or repo.name or "repo",
                            repo_path=str(repo),
                            start_line=start_line,
                            end_line=end_line,
                            text=text,
                            tokens=tokens,
                            chunk_symbols=tuple(str(value) for value in item.get("chunk_symbols", [])),
                            file_symbols=indexed_entry.symbols if indexed_entry is not None else (),
                            file_imports=indexed_entry.imports if indexed_entry is not None else (),
                            file_local_import_paths=(
                                indexed_entry.local_import_paths if indexed_entry is not None else ()
                            ),
                            file_imported_by_paths=(
                                indexed_entry.imported_by_paths if indexed_entry is not None else ()
                            ),
                            redaction_count=int(item.get("redaction_count", 0)),
                            redaction_types=tuple(
                                str(value) for value in item.get("redaction_types", [])
                            ),
                            addition_tokens=int(item.get("addition_tokens", tokens)),
                            file_role=(
                                indexed_entry.role if indexed_entry is not None else _file_kind(relative)
                            ),
                            file_landmarks=(
                                indexed_entry.landmarks if indexed_entry is not None else ()
                            ),
                        )
                    )
                except (KeyError, TypeError, ValueError):
                    valid_payload = False
                    break
            if valid_payload:
                chunks.extend(restored)
                continue
        try:
            raw_text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        redacted_text, file_redactions = _redact_sensitive_text(raw_text)
        original_lines = raw_text.splitlines()
        lines = redacted_text.splitlines()
        if len(original_lines) != len(lines):
            original_lines = lines
        symbol_occurrences = (
            indexed_entry.symbol_occurrences
            if indexed_entry is not None and indexed_entry.symbol_occurrences
            else extract_symbol_occurrences(path, "\n".join(lines))
        )
        file_chunk_payloads: list[dict[str, Any]] = []
        for start in range(0, len(lines), stride):
            content_lines = lines[start : start + chunk_lines]
            if not any(line.strip() for line in content_lines):
                continue
            end_line = start + len(content_lines)
            text = "\n".join(content_lines)
            original_text = "\n".join(original_lines[start:end_line])
            chunk_redactions = (
                _redact_sensitive_text(original_text)[1] if file_redactions else {}
            )
            chunk_id = f"{display_path}#L{start + 1}-L{end_line}"
            rendered = (
                f"[SOURCE:{chunk_id}]\n"
                f"```{_language_for_path(display_path)}\n{text.rstrip()}\n```"
            )
            addition_tokens = counter.count(rendered + "\n\n")
            chunks.append(
                CodeChunk(
                    chunk_id,
                    relative,
                    display_path,
                    repo_label or repo.name or "repo",
                    str(repo),
                    start + 1,
                    end_line,
                    text,
                    addition_tokens,
                    _chunk_local_symbols(
                        symbol_occurrences,
                        start_line=start + 1,
                        end_line=end_line,
                    ),
                    indexed_entry.symbols if indexed_entry is not None else (),
                    indexed_entry.imports if indexed_entry is not None else (),
                    indexed_entry.local_import_paths if indexed_entry is not None else (),
                    indexed_entry.imported_by_paths if indexed_entry is not None else (),
                    sum(chunk_redactions.values()),
                    tuple(sorted(chunk_redactions)),
                    addition_tokens=addition_tokens,
                    file_role=indexed_entry.role if indexed_entry is not None else _file_kind(relative),
                    file_landmarks=indexed_entry.landmarks if indexed_entry is not None else (),
                )
            )
            file_chunk_payloads.append(
                {
                    "redacted": True,
                    "start_line": start + 1,
                    "end_line": end_line,
                    "text": text,
                    "tokens": addition_tokens,
                    "chunk_symbols": list(
                        _chunk_local_symbols(
                            symbol_occurrences,
                            start_line=start + 1,
                            end_line=end_line,
                        )
                    ),
                    "redaction_count": sum(chunk_redactions.values()),
                    "redaction_types": sorted(chunk_redactions),
                    "addition_tokens": addition_tokens,
                }
            )
            if end_line == len(lines):
                break
        if cache_key is not None:
            cache_payload: Any = {"redacted": True, "chunks": file_chunk_payloads}
            pending_cache_entries.append(
                CachedChunk(
                    cache_key,
                    cache_payload,
                )
            )
    if cache is not None and pending_cache_entries:
        try:
            cache.save_many(pending_cache_entries)
        except (OSError, sqlite3.Error, ValueError, TypeError):
            pass
    if chunk_cache_stats is not None:
        chunk_cache_stats.update(cache.stats() if cache is not None else {})
        chunk_cache_stats["enabled"] = cache is not None
    return tuple(chunks)


def _feature_role_boost(chunk: CodeChunk, evidence_slots: set[str]) -> float:
    boost = 0.0
    landmarks = set(chunk.file_landmarks)
    path = chunk.relative_path.casefold()
    if "implementation" in evidence_slots and chunk.file_role == "source":
        boost += 5.0
    if "integration/render" in evidence_slots:
        if landmarks & {"app-shell", "react-root", "react-entrypoint"}:
            boost += 28.0
        elif landmarks & {"react-component", "entrypoint"}:
            boost += 14.0
    if "state/data" in evidence_slots and (
        landmarks & {"state", "types"}
        or any(term in _path_terms(path) for term in {"state", "store", "types", "model", "data"})
    ):
        boost += 18.0
    if "validation/tests" in evidence_slots and chunk.file_role == "test":
        boost += 14.0
    if "style" in evidence_slots and chunk.file_role == "style":
        boost += 24.0
    if any(part in path.split("/") for part in ("archive", "legacy", "mocks", "experiments", "distractors")):
        boost -= 45.0
    return boost


def _graphify_cache_root(repo: Path, revision: str) -> Path:
    digest = hashlib.sha256(str(repo.resolve()).encode("utf-8")).hexdigest()[:12]
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", repo.name or "repo")
    return (
        Path.home()
        / ".agenvantage"
        / "graphify-cache-v1"
        / f"{safe_name}-{digest}"
        / (revision or "unknown")
    )


def _graph_gate(plan: ContextPlan, workflow: str) -> tuple[bool, str]:
    if workflow != "feature":
        return False, "workflow_not_feature"
    if plan.grounding.status != "grounded":
        return True, f"grounding_{plan.grounding.status}"
    if "compound" in plan.shapes:
        return True, "compound_task"
    if plan.paths or plan.identifiers:
        return True, "explicit_cross_file_signal"
    task_terms = set(_terms(plan.task))
    if task_terms & {
        "across",
        "caller",
        "callee",
        "cross-file",
        "dependencies",
        "dependency",
        "flow",
        "integration",
        "related",
    }:
        return True, "cross_file_task_language"
    return False, "already_grounded"


def _graph_signals_for_chunks(
    chunks: Iterable[CodeChunk],
    candidates: Iterable[GraphCandidate],
) -> dict[str, tuple[GraphCandidate, ...]]:
    by_path: dict[str, list[CodeChunk]] = {}
    for chunk in chunks:
        by_path.setdefault(chunk.relative_path, []).append(chunk)
    signals: dict[str, list[GraphCandidate]] = {}
    for candidate in candidates:
        path_chunks = by_path.get(candidate.source_path, [])
        if not path_chunks:
            continue
        line_number = 1
        if candidate.source_location and candidate.source_location.startswith("L"):
            try:
                line_number = int(candidate.source_location[1:])
            except ValueError:
                line_number = 1
        containing = [
            chunk
            for chunk in path_chunks
            if chunk.start_line <= line_number <= chunk.end_line
        ]
        target = (
            min(containing, key=lambda chunk: (chunk.end_line - chunk.start_line, chunk.chunk_id))
            if containing
            else min(
                path_chunks,
                key=lambda chunk: (
                    min(abs(chunk.start_line - line_number), abs(chunk.end_line - line_number)),
                    chunk.chunk_id,
                ),
            )
        )
        signals.setdefault(target.chunk_id, []).append(candidate)
    return {
        chunk_id: tuple(
            sorted(
                values,
                key=lambda item: (
                    -item.score,
                    item.hop,
                    item.source_path,
                    item.source_location or "",
                    item.node_id,
                ),
            )
        )
        for chunk_id, values in signals.items()
    }


def rank_chunks(
    chunks: Iterable[CodeChunk],
    task: str,
    *,
    evidence_slots: Iterable[str] = (),
    graph_signals: dict[str, tuple[GraphCandidate, ...]] | None = None,
) -> tuple[CodeChunk, ...]:
    query_counts = Counter(_terms(task))
    slot_names = set(evidence_slots)
    ranked: list[CodeChunk] = []
    for chunk in chunks:
        chunk_graph_signals = (graph_signals or {}).get(chunk.chunk_id, ())
        command_anchors = Counter(_command_anchor_terms(task, chunk.repo_label))
        path_counts = Counter(_terms(chunk.relative_path))
        literal_path_counts = Counter(_literal_terms(chunk.relative_path))
        repo_counts = Counter(_terms(chunk.repo_label))
        text_counts = Counter(_terms(chunk.text))
        literal_text_counts = Counter(_literal_terms(chunk.text))
        landmark_counts = Counter(_terms(" ".join(chunk.file_landmarks)))
        chunk_symbol_counts = Counter(_terms(" ".join(chunk.chunk_symbols)))
        literal_chunk_symbol_counts = Counter(_literal_terms(" ".join(chunk.chunk_symbols)))
        symbol_counts = Counter(_terms(" ".join(chunk.file_symbols)))
        import_counts = Counter(_terms(" ".join(chunk.file_imports)))
        imported_by_counts = Counter(_terms(" ".join(chunk.file_imported_by_paths)))
        matches = tuple(
            term
            for term in query_counts
            if path_counts.get(term, 0)
            or repo_counts.get(term, 0)
            or text_counts.get(term, 0)
            or landmark_counts.get(term, 0)
            or chunk_symbol_counts.get(term, 0)
            or symbol_counts.get(term, 0)
            or import_counts.get(term, 0)
            or imported_by_counts.get(term, 0)
        )
        path_score = sum(query_counts[term] * min(path_counts[term], 3) * 5 for term in matches)
        repo_score = sum(query_counts[term] * min(repo_counts[term], 2) for term in matches)
        text_score = sum(query_counts[term] * min(text_counts[term], 5) for term in matches)
        # File landmarks are deterministic, low-noise summaries extracted by
        # the indexer. Scoring them separately preserves chunk meaning without
        # paying for an embedding pass or a second repository scan.
        landmark_score = sum(
            query_counts[term] * min(landmark_counts[term], 3) * 3 for term in matches
        )
        chunk_symbol_score = sum(
            query_counts[term] * min(chunk_symbol_counts[term], 2) * 4 for term in matches
        )
        symbol_score = sum(
            query_counts[term] * min(symbol_counts[term], 2) * 2 for term in matches
        )
        import_score = sum(
            query_counts[term] * min(import_counts[term], 2) for term in matches
        )
        imported_by_score = sum(
            query_counts[term] * min(imported_by_counts[term], 2) for term in matches
        )
        command_anchor_score = sum(
            count
            * (
                min(literal_path_counts.get(term, 0), 2) * 30
                + min(literal_text_counts.get(term, 0), 4) * 8
                + min(literal_chunk_symbol_counts.get(term, 0), 2) * 20
            )
            for term, count in command_anchors.items()
        )
        coverage_bonus = 2 * len(matches)
        score = float(
            path_score
            + repo_score
            + text_score
            + landmark_score
            + chunk_symbol_score
            + symbol_score
            + import_score
            + imported_by_score
            + command_anchor_score
            + coverage_bonus
        )
        if slot_names:
            score += _feature_role_boost(chunk, slot_names)
        graph_score = min(sum(candidate.score for candidate in chunk_graph_signals), 2.0)
        # Graphify expands the evidence surface but does not displace primary
        # lexical/structural edit and test targets. The confidence-weighted
        # graph score is used for supporting-target selection and provenance.
        graph_relations = _ordered_unique(
            (
                relation
                for candidate in chunk_graph_signals
                for relation in candidate.relations
            ),
            16,
        )
        graph_confidences = _ordered_unique(
            (candidate.confidence for candidate in chunk_graph_signals),
            3,
        )
        graph_communities = _ordered_unique(
            (
                candidate.community
                for candidate in chunk_graph_signals
                if candidate.community is not None
            ),
            12,
        )
        graph_provenance = _ordered_unique(
            (
                value
                for candidate in chunk_graph_signals
                for value in candidate.provenance
            ),
            24,
        )
        graph_hops = tuple(sorted({candidate.hop for candidate in chunk_graph_signals}))
        ranked.append(
            CodeChunk(
                chunk.chunk_id,
                chunk.relative_path,
                chunk.display_path,
                chunk.repo_label,
                chunk.repo_path,
                chunk.start_line,
                chunk.end_line,
                chunk.text,
                chunk.tokens,
                chunk.chunk_symbols,
                chunk.file_symbols,
                chunk.file_imports,
                chunk.file_local_import_paths,
                chunk.file_imported_by_paths,
                chunk.redaction_count,
                chunk.redaction_types,
                score,
                matches,
                chunk.addition_tokens,
                chunk.file_role,
                chunk.file_landmarks,
                graph_score,
                graph_relations,
                graph_confidences,
                graph_communities,
                graph_provenance,
                graph_hops,
            )
        )
    return tuple(
        sorted(ranked, key=lambda chunk: (chunk.score, -chunk.tokens, chunk.chunk_id), reverse=True)
    )


def _build_candidate_pool(ranked: tuple[CodeChunk, ...], top_k: int) -> list[CodeChunk]:
    grouped: dict[str, list[CodeChunk]] = {}
    ordered_paths: list[str] = []
    for chunk in ranked:
        if chunk.display_path not in grouped:
            grouped[chunk.display_path] = []
            ordered_paths.append(chunk.display_path)
        grouped[chunk.display_path].append(chunk)

    candidate_pool: list[CodeChunk] = []
    offset = 0
    while len(candidate_pool) < top_k:
        added_any = False
        for path in ordered_paths:
            file_chunks = grouped[path]
            if offset >= len(file_chunks):
                continue
            candidate_pool.append(file_chunks[offset])
            added_any = True
            if len(candidate_pool) >= top_k:
                break
        if not added_any:
            break
        offset += 1
    return candidate_pool


def _best_chunk_for_path(
    ranked: tuple[CodeChunk, ...],
    display_path: str,
) -> CodeChunk | None:
    for chunk in ranked:
        if chunk.display_path == display_path:
            return chunk
    return None


def _file_kind(relative_path: str) -> str:
    if _is_test_path(relative_path):
        return "test"
    if _is_config_path(relative_path):
        return "config"
    if _is_doc_like_path(relative_path):
        return "supporting"
    if Path(relative_path).suffix.lower() in {".css", ".scss", ".sass", ".less", ".styl"}:
        return "style"
    return "source"


def _evidence_target_categories(
    ordered: list[dict[str, Any]],
    plan: ContextPlan,
    edit_targets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    slots = {slot.name for slot in plan.evidence_slots}
    targets: list[dict[str, Any]] = []
    predicates = []
    if "integration/render" in slots:
        predicates.append(
            lambda item: bool(
                set(item["landmarks"])
                & {"app-shell", "react-root", "react-entrypoint", "react-component", "entrypoint"}
            )
        )
    if "state/data" in slots:
        predicates.append(
            lambda item: bool(set(item["landmarks"]) & {"state", "types"})
            or bool(_path_terms(item["relative_path"]) & {"state", "store", "types", "model", "data"})
        )
    if "style" in slots:
        predicates.append(lambda item: item["role"] == "style")
    edit_paths = {item["relative_path"] for item in edit_targets}
    for predicate in predicates:
        candidates = [item for item in ordered if predicate(item)]
        match = (
            max(
                candidates,
                key=lambda item: (
                    bool(set(item["imported_by"]) & edit_paths),
                    item["score"],
                ),
            )
            if candidates
            else None
        )
        if match is not None and match not in targets:
            targets.append(match)
    return targets


def _feature_path_score(
    chunk: CodeChunk,
    *,
    task_terms: set[str],
    feature_mentions_tests: bool,
    feature_mentions_config: bool,
) -> float:
    path_terms = _path_terms(chunk.relative_path)
    symbol_terms = set(_terms(" ".join(chunk.file_symbols)))
    chunk_terms = set(chunk.matched_terms)
    exact_path_hits = len(task_terms & path_terms)
    exact_symbol_hits = len(task_terms & symbol_terms)
    exact_chunk_hits = len(task_terms & chunk_terms)
    score = chunk.score + (exact_path_hits * 12) + (exact_symbol_hits * 8) + (exact_chunk_hits * 5)
    if _is_test_path(chunk.relative_path):
        score += 10 if feature_mentions_tests else 4
    if _is_config_path(chunk.relative_path):
        score += 8 if feature_mentions_config else 2
        if Path(chunk.relative_path).name.lower() in _CONFIG_NAMES:
            score += 12 if feature_mentions_tests else 6
    return score


def _is_library_cli_feature_task(task: str) -> bool:
    return bool(set(_terms(task)) & _LIBRARY_CLI_TASK_TERMS)


def _guard_test_signals(chunk: CodeChunk) -> tuple[tuple[str, ...], float]:
    if chunk.file_role != "test" and not _is_test_path(chunk.relative_path):
        return (), 0.0
    haystack = f"{chunk.relative_path}\n{chunk.text}"
    signals = tuple(
        name for name, pattern, _ in _GUARD_TEST_SIGNAL_PATTERNS if pattern.search(haystack)
    )
    if not signals:
        return (), 0.0
    path_terms = _path_terms(chunk.relative_path)
    path_bonus = 2.0 * len(path_terms & _GUARD_TEST_PATH_TERMS)
    content_score = sum(
        weight
        for name, _, weight in _GUARD_TEST_SIGNAL_PATTERNS
        if name in signals
    )
    return signals, content_score + path_bonus


def _guard_test_target(ranked: tuple[CodeChunk, ...]) -> dict[str, Any] | None:
    candidates: list[tuple[CodeChunk, tuple[str, ...], float]] = []
    for chunk in ranked:
        signals, signal_score = _guard_test_signals(chunk)
        if signals:
            candidates.append((chunk, signals, signal_score))
    if not candidates:
        return None
    chunk, signals, signal_score = max(
        candidates,
        key=lambda item: (item[2], len(item[1]), -item[0].tokens, item[0].chunk_id),
    )
    return {
        "path": chunk.display_path,
        "relative_path": chunk.relative_path,
        "repo_label": chunk.repo_label,
        "repo_path": chunk.repo_path,
        "best_chunk_id": chunk.chunk_id,
        "matched_terms": list(chunk.matched_terms),
        "score": signal_score,
        "kind": "test",
        "role": chunk.file_role,
        "landmarks": list(chunk.file_landmarks),
        "local_imports": list(chunk.file_local_import_paths),
        "imported_by": list(chunk.file_imported_by_paths),
        "signals": list(signals),
    }


def _discover_full_suite_command(repo: Path) -> str | None:
    """Find a repository's conventional full-suite command without running it."""
    for filename in ("Makefile", "GNUmakefile"):
        path = repo / filename
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if re.search(r"(?m)^\s*(?:test|test-suite|test-all)\s*:", text):
            return "make test"

    package_path = repo / "package.json"
    try:
        package = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        package = {}
    if isinstance(package, dict) and isinstance(package.get("scripts"), dict):
        if "test" in package["scripts"]:
            if (repo / "pnpm-lock.yaml").is_file():
                return "pnpm test"
            if (repo / "yarn.lock").is_file():
                return "yarn test"
            return "npm test"

    for filename in ("pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg"):
        path = repo / filename
        try:
            text = path.read_text(encoding="utf-8").lower()
        except (OSError, UnicodeDecodeError):
            continue
        if "pytest" in text:
            return "pytest"
        if filename == "tox.ini" and "[tox]" in text:
            return "tox"

    for filename in ("README.md", "README.rst", "CONTRIBUTING.md"):
        path = repo / filename
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line in lines:
            command = line.strip().lstrip("$> ")
            for candidate in (
                "make test",
                "python -m pytest",
                "pytest",
                "npm test",
                "npm run test",
                "pnpm test",
                "yarn test",
                "tox",
            ):
                if command == candidate or command.startswith(f"{candidate} "):
                    return candidate
    return None


def _build_feature_change_surface(
    ranked: tuple[CodeChunk, ...],
    task: str,
    include_diff: bool,
    include_log: bool,
    plan: ContextPlan,
) -> dict[str, Any]:
    task_terms = set(_terms(task))
    feature_mentions_tests = bool(task_terms & _FEATURE_TEST_TERMS)
    feature_mentions_config = bool(task_terms & _FEATURE_CONFIG_TERMS)

    file_candidates: dict[str, dict[str, Any]] = {}
    for chunk in ranked:
        candidate = file_candidates.get(chunk.display_path)
        file_score = _feature_path_score(
            chunk,
            task_terms=task_terms,
            feature_mentions_tests=feature_mentions_tests,
            feature_mentions_config=feature_mentions_config,
        )
        if candidate is None or file_score > candidate["score"]:
            file_candidates[chunk.display_path] = {
                "path": chunk.display_path,
                "relative_path": chunk.relative_path,
                "repo_label": chunk.repo_label,
                "repo_path": chunk.repo_path,
                "best_chunk_id": chunk.chunk_id,
                "matched_terms": list(chunk.matched_terms),
                "score": file_score,
                "kind": _file_kind(chunk.relative_path),
                "role": chunk.file_role,
                "landmarks": list(chunk.file_landmarks),
                "local_imports": list(chunk.file_local_import_paths),
                "imported_by": list(chunk.file_imported_by_paths),
                "graph_score": chunk.graph_score,
                "graph_relations": list(chunk.graph_relations),
                "graph_confidences": list(chunk.graph_confidences),
                "graph_communities": list(chunk.graph_communities),
                "graph_hops": list(chunk.graph_hops),
            }

    ordered = sorted(
        file_candidates.values(),
        key=lambda item: (item["score"], item["path"]),
        reverse=True,
    )
    source_candidates = [item for item in ordered if item["kind"] == "source"]
    deprecated_parts = {"archive", "legacy", "mocks", "experiments", "distractors"}
    active_sources = [
        item
        for item in source_candidates
        if not (set(Path(item["relative_path"]).parts) & deprecated_parts)
    ]
    structural_landmarks = {
        "app-shell",
        "entrypoint",
        "react-component",
        "react-entrypoint",
        "react-root",
    }
    ui_feature = any(slot.name == "style" and slot.required for slot in plan.evidence_slots)
    structural_sources = (
        [
            item
            for item in active_sources
            if set(item["landmarks"]) & structural_landmarks
        ]
        if ui_feature
        else []
    )
    ordered_edit_candidates = structural_sources + [
        item for item in active_sources if item not in structural_sources
    ]
    if not ordered_edit_candidates:
        ordered_edit_candidates = source_candidates
    edit_targets = ordered_edit_candidates[: _FEATURE_RESERVED_COUNTS["edit_targets"]]

    edit_paths = {item["relative_path"] for item in edit_targets}
    support_paths: list[str] = []
    for target in edit_targets:
        support_paths.extend(target["imported_by"])
        support_paths.extend(target["local_imports"])
    related_paths = set(_ordered_unique(support_paths, 24))
    # `ordered` already carries task relevance. Preserve that ranking instead
    # of allowing import traversal order to choose an unrelated helper before
    # a caller that contains the requested behavior.
    supporting_targets = [
        item
        for item in ordered
        if (
            item["relative_path"] in related_paths
            or any(hop > 0 for hop in item["graph_hops"])
        )
        and item["relative_path"] not in edit_paths
        and item["kind"] not in {"test", "config"}
    ][: _FEATURE_RESERVED_COUNTS["supporting_targets"]]

    test_targets: list[dict[str, Any]] = []
    edit_paths = {edit["relative_path"] for edit in edit_targets}
    for item in ordered:
        if item["kind"] != "test":
            continue
        if (
            feature_mentions_tests
            or any(term in _path_terms(item["relative_path"]) for term in task_terms)
            or any(
                Path(edit["relative_path"]).stem.split(".")[0] in item["relative_path"]
                for edit in edit_targets
            )
            or bool(set(item["local_imports"]) & edit_paths)
        ):
            test_targets.append(item)
        if len(test_targets) >= _FEATURE_RESERVED_COUNTS["test_targets"]:
            break

    config_targets: list[dict[str, Any]] = []
    config_candidates = sorted(
        (item for item in ordered if item["kind"] == "config"),
        key=lambda item: (
            feature_mentions_tests
            and Path(item["relative_path"]).name.lower() in _CONFIG_NAMES,
            item["score"],
            item["path"],
        ),
        reverse=True,
    )
    for item in config_candidates:
        if (
            feature_mentions_config
            or item["relative_path"] in related_paths
            or any(term in _path_terms(item["relative_path"]) for term in task_terms)
        ):
            config_targets.append(item)
        if len(config_targets) >= _FEATURE_RESERVED_COUNTS["config_targets"]:
            break
    evidence_targets = _evidence_target_categories(ordered, plan, edit_targets)
    guard_test_targets = []
    if _is_library_cli_feature_task(task):
        guard_target = _guard_test_target(ranked)
        if guard_target is not None:
            guard_test_targets.append(guard_target)

    missing_signals: list[str] = []
    if not test_targets:
        missing_signals.append("No likely test target was identified for this feature task.")
    if feature_mentions_config and not config_targets:
        missing_signals.append("Task suggests config or schema work, but no strong config target was identified.")
    if not supporting_targets and edit_targets:
        missing_signals.append(
            "No strong supporting implementation neighbor was identified from local imports or graph edges."
        )
    if not edit_targets:
        missing_signals.append("No likely edit target was identified from exact path, symbol, or chunk matches.")
    if _is_library_cli_feature_task(task) and not guard_test_targets:
        missing_signals.append(
            "No repository-wide library or CLI guard test was identified from import, API, or compatibility signals."
        )
    if (include_diff or include_log) and not (include_diff and include_log):
        missing_signals.append("Feature work may benefit from both diff and recent log provenance for changed-behavior context.")

    return {
        "edit_targets": edit_targets,
        "test_targets": test_targets,
        "config_targets": config_targets,
        "supporting_targets": supporting_targets,
        "evidence_targets": evidence_targets,
        "guard_test_targets": guard_test_targets,
        "missing_signals": missing_signals,
    }


def _select_feature_reserved_chunks(
    ranked: tuple[CodeChunk, ...],
    change_surface: dict[str, Any],
) -> tuple[list[CodeChunk], list[CodeChunk]]:
    mandatory: list[CodeChunk] = []
    optional: list[CodeChunk] = []
    seen_ids: set[str] = set()

    def add_unique(target: dict[str, Any], destination: list[CodeChunk]) -> CodeChunk | None:
        path = target["path"]
        chunk = next(
            (
                item
                for item in ranked
                if item.chunk_id == target.get("best_chunk_id")
            ),
            None,
        ) or _best_chunk_for_path(ranked, path)
        if chunk is None or chunk.chunk_id in seen_ids:
            return None
        destination.append(chunk)
        seen_ids.add(chunk.chunk_id)
        return chunk

    # Primaries are the minimum feature packet: one best excerpt per target.
    for category in (
        "edit_targets",
        "test_targets",
        "guard_test_targets",
        "config_targets",
        "supporting_targets",
    ):
        for target in change_surface.get(category, []):
            add_unique(target, mandatory)

    # Test imports and setup commonly live at the top of the file while the
    # best behavior match is deeper. Both are mandatory when they differ so an
    # agent receives the test's integration surface as well as its assertion.
    for target in change_surface.get("test_targets", []):
        path = target["path"]
        earliest = min(
            (item for item in ranked if item.display_path == path),
            key=lambda item: item.start_line,
            default=None,
        )
        if earliest is not None and earliest.chunk_id not in seen_ids:
            mandatory.append(earliest)
            seen_ids.add(earliest.chunk_id)

    # Expansions are useful only after all mandatory classes have had a chance
    # to enter the packet and therefore compete for the remaining budget.
    for target in change_surface.get("edit_targets", []):
        path = target["path"]
        stem_terms = set(_terms(Path(target["relative_path"]).stem))
        anchored = [
            item
            for item in ranked
            if item.display_path == path
            and stem_terms & set(_terms(" ".join(item.chunk_symbols)))
        ]
        if anchored:
            anchor = min(anchored, key=lambda item: item.start_line)
            if anchor.chunk_id not in seen_ids:
                optional.append(anchor)
                seen_ids.add(anchor.chunk_id)
            next_chunk = min(
                (
                    item
                    for item in ranked
                    if item.display_path == path and item.start_line > anchor.start_line
                ),
                key=lambda item: item.start_line,
                default=None,
            )
            if next_chunk is not None and next_chunk.chunk_id not in seen_ids:
                optional.append(next_chunk)
                seen_ids.add(next_chunk.chunk_id)
    for category in ("evidence_targets",):
        for target in change_surface.get(category, []):
            add_unique(target, optional)
    return mandatory, optional


def _feature_sufficiency(
    selected: list[CodeChunk],
    plan: ContextPlan,
    *,
    map_included: bool,
    budget_exhausted: bool,
    repository_maps: list[dict[str, Any]],
) -> dict[str, Any]:
    roles = {chunk.file_role for chunk in selected}
    landmarks = {landmark for chunk in selected for landmark in chunk.file_landmarks}
    path_terms = {
        term
        for chunk in selected
        for term in _path_terms(chunk.relative_path)
    }
    source_paths = {
        chunk.display_path for chunk in selected if chunk.file_role == "source"
    }
    covered: set[str] = set()
    if source_paths:
        covered.add("implementation")
    if landmarks & {"state", "types"} or path_terms & {
        "state",
        "store",
        "types",
        "model",
        "data",
        "catalog",
    }:
        covered.add("state/data")
    if landmarks & {
        "app-shell",
        "entrypoint",
        "react-component",
        "react-entrypoint",
        "react-root",
    } or path_terms & {"app", "main", "route", "router", "view", "cli", "command"}:
        covered.add("integration/render")
    if len(source_paths) >= 2:
        covered.add("analogous behavior")
    if "test" in roles:
        covered.add("validation/tests")
    if map_included or roles & {"config", "docs"}:
        covered.add("repo constraints")
    if "style" in roles:
        covered.add("style")
    if "config" in roles:
        covered.add("config")
    if path_terms & {"schema"}:
        covered.add("schema")
    if path_terms & {"migration", "migrations"}:
        covered.add("migration")

    required = [slot.name for slot in plan.evidence_slots if slot.required]
    missing = [name for name in required if name not in covered]
    if not missing:
        status = "sufficient"
    elif budget_exhausted:
        status = "insufficient_budget"
    else:
        status = "insufficient"

    slot_roles = {
        "state/data": {"state", "types"},
        "integration/render": {
            "app-shell",
            "entrypoint",
            "react-component",
            "react-entrypoint",
            "react-root",
        },
        "validation/tests": {"test-layout"},
        "style": {"style"},
    }
    selected_paths = {chunk.relative_path for chunk in selected}
    recommendations: list[str] = []
    for missing_slot in missing:
        desired = slot_roles.get(missing_slot, set())
        for repository_map in repository_maps:
            for node in repository_map["manifest"].get("selected_nodes", []):
                if node["path"] in selected_paths:
                    continue
                if desired and not (set(node.get("landmarks", [])) & desired or node.get("role") in desired):
                    continue
                recommendations.append(node["path"])
                break
            if recommendations and len(recommendations) >= len(missing):
                break
    recommendations = list(dict.fromkeys(recommendations))
    return {
        "status": status,
        "confidence": round((len(required) - len(missing)) / len(required), 4)
        if required
        else 1.0,
        "required_slots": required,
        "covered_slots": sorted(covered),
        "missing_slots": missing,
        "recommended_expansions": recommendations,
    }


def _apply_line_pruning_to_selected(
    selected: list[CodeChunk],
    *,
    prefix: str,
    task: str,
    change_surface: dict[str, Any] | None,
    workflow: str,
    counter: TokenCounter,
) -> tuple[list[CodeChunk], dict[str, Any], str]:
    preset = workflow if workflow in {"feature", "debug", "change", "review"} else "change"
    hints = goal_hints_from_context(task, change_surface=change_surface, preset=preset)
    if not selected:
        return (
            selected,
            {
                "enabled": True,
                "applied": False,
                "preset": preset,
                "goal_hint_count": len(hints),
                "reason": "no_selected_chunks",
            },
            prefix,
        )
    if workflow == "feature":
        rendered = prefix + "".join(f"{chunk.render()}\n\n" for chunk in selected)
        return (
            selected,
            {
                "enabled": True,
                "applied": False,
                "preset": preset,
                "goal_hint_count": len(hints),
                "reason": "feature_workflow_recall_guard",
                "chunks": [],
                "total_dropped_lines": 0,
                "selected_block_tokens_before": 0,
                "selected_block_tokens_after": 0,
                "selected_block_tokens_saved": 0,
            },
            rendered,
        )

    pruned_selected: list[CodeChunk] = []
    chunk_stats: list[dict[str, Any]] = []
    tokens_before = 0
    tokens_after = 0
    total_dropped = 0
    for chunk in selected:
        block_before = chunk.render()
        tokens_before += counter.count(block_before)
        if chunk.redaction_count > 0:
            tokens_after += counter.count(block_before)
            pruned_selected.append(chunk)
            chunk_stats.append(
                {
                    "chunk_id": chunk.chunk_id,
                    "applied": False,
                    "reason": "secret_redactions_present",
                    "original_lines": len(chunk.text.splitlines()),
                    "kept_lines": len(chunk.text.splitlines()),
                    "dropped_lines": 0,
                    "dropped_line_numbers": [],
                    "goal_hint_count": len(hints),
                }
            )
            continue
        chunk_hints = set(hints)
        chunk_hints.update(term.casefold() for term in chunk.matched_terms)
        chunk_hints.update(term.casefold() for term in chunk.chunk_symbols)
        min_score = 0.25 if workflow == "feature" else 0.5
        pruned_text, stats = prune_lines(chunk.text, goal_hints=chunk_hints, min_score=min_score)
        dropped = int(stats.get("dropped_lines") or 0)
        total_dropped += dropped
        if dropped:
            pruned_block = (
                f"[SOURCE:{chunk.chunk_id}]\n"
                f"```{_language_for_path(chunk.display_path)}\n{pruned_text.rstrip()}\n```"
            )
            tokens_after += counter.count(pruned_block)
            pruned_selected.append(
                replace(
                    chunk,
                    text=pruned_text,
                    tokens=counter.count(pruned_block),
                    addition_tokens=0,
                )
            )
            chunk_stats.append({"chunk_id": chunk.chunk_id, "applied": True, **stats})
        else:
            tokens_after += counter.count(block_before)
            pruned_selected.append(chunk)
            chunk_stats.append({"chunk_id": chunk.chunk_id, "applied": False, **stats})

    rendered = prefix + "".join(f"{chunk.render()}\n\n" for chunk in pruned_selected)
    manifest = {
        "enabled": True,
        "applied": total_dropped > 0,
        "preset": preset,
        "goal_hint_count": len(hints),
        "chunks": chunk_stats,
        "total_dropped_lines": total_dropped,
        "selected_block_tokens_before": tokens_before,
        "selected_block_tokens_after": tokens_after,
        "selected_block_tokens_saved": max(tokens_before - tokens_after, 0),
    }
    return pruned_selected, manifest, rendered


def build_multi_repo_context_package(
    repos: Iterable[Path],
    task: str,
    budget: int,
    counter: TokenCounter,
    top_k: int = 20,
    instructions: str = _DEFAULT_INSTRUCTIONS,
    include_diff: bool = False,
    include_log: bool = False,
    include_globs: tuple[str, ...] = (),
    exclude_globs: tuple[str, ...] = (),
    workflow: str = "generic",
    include_full_scan_prompt: bool = False,
    graph_backend: str = "off",
    graph_json: Path | None = None,
    graph_hops: int = 2,
    graph_timeout: float = 120.0,
    graphify_executable: str = "graphify",
) -> tuple[str, dict[str, Any]]:
    if budget <= 0:
        raise ValueError("Token budget must be positive.")
    if top_k <= 0:
        raise ValueError("top_k must be positive.")
    if graph_backend not in {"auto", "off", "graphify"}:
        raise ValueError("graph_backend must be 'auto', 'off', or 'graphify'.")
    if graph_hops not in {1, 2}:
        raise ValueError("graph_hops must be 1 or 2.")
    if graph_timeout <= 0:
        raise ValueError("graph_timeout must be positive.")
    task = task.strip()
    if not task:
        raise ValueError("Task must not be empty.")
    task_plan = plan_context(task)
    evidence_slot_names = tuple(slot.name for slot in task_plan.evidence_slots)

    repo_inputs = _repo_inputs(repos)
    repo_summaries: list[dict[str, Any]] = []
    candidate_chunks: list[CodeChunk] = []
    provenance_sections: list[ProvenanceSection] = []
    repository_maps: list[dict[str, Any]] = []
    chunks_by_repo: dict[str, tuple[CodeChunk, ...]] = {}
    revisions_by_repo: dict[str, str] = {}
    index_totals = {
        "indexed_files": 0,
        "reused_files": 0,
        "metadata_fast_path_reused_files": 0,
        "rebuilt_files": 0,
        "skipped_files": 0,
        "metadata_checked_files": 0,
        "hashes_avoided": 0,
        "content_hashes_validated": 0,
        "symbol_count": 0,
        "import_count": 0,
    }
    chunk_cache_totals: dict[str, Any] = {
        "enabled": True,
        "hits": 0,
        "misses": 0,
        "writes": 0,
    }
    for repo_input in repo_inputs:
        files = source_files(
            repo_input.root,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
        )
        index_result = build_repository_index(repo_input.root, files)
        repo_provenance = build_repo_provenance_sections(
            repo_input.root,
            repo_input.label,
            counter=counter,
            include_diff=include_diff,
            include_log=include_log,
        )
        repo_chunk_cache: dict[str, Any] = {}
        chunks = chunks_for_repo(
            repo_input.root,
            counter,
            overlap_lines=_FEATURE_CHUNK_OVERLAP_LINES if workflow == "feature" else 8,
            files=files,
            repo_label=repo_input.label if len(repo_inputs) > 1 else None,
            file_index=index_result.entries,
            chunk_cache_stats=repo_chunk_cache,
        )
        map_markdown, map_manifest = build_repository_map(
            repo_input.root,
            task,
            budget=700,
            files=files,
            index_result=index_result,
            counter=counter,
        )
        repository_maps.append(
            {
                "repo_label": repo_input.label,
                "markdown": map_markdown,
                "manifest": map_manifest,
            }
        )
        chunks_by_repo[str(repo_input.root)] = chunks
        revisions_by_repo[str(repo_input.root)] = str(index_result.stats.get("revision") or "")
        repo_summaries.append(
            {
                "label": repo_input.label,
                "path": str(repo_input.root),
                "scanned_files": len(files),
                "candidate_chunks": len(chunks),
                "index": index_result.stats,
                "chunk_cache": repo_chunk_cache,
                "provenance_sections": [section.to_dict() for section in repo_provenance],
            }
        )
        for key in index_totals:
            index_totals[key] += int(index_result.stats[key])
        chunk_cache_totals["enabled"] = bool(
            chunk_cache_totals["enabled"] and repo_chunk_cache.get("enabled", False)
        )
        for key in ("hits", "misses", "writes"):
            chunk_cache_totals[key] += int(repo_chunk_cache.get(key, 0))
        candidate_chunks.extend(chunks)
        provenance_sections.extend(repo_provenance)

    if not candidate_chunks:
        raise ValueError("No eligible source files were found in the provided repositories.")

    ranked = rank_chunks(
        candidate_chunks,
        task,
        evidence_slots=evidence_slot_names if workflow == "feature" else (),
    )
    top_ranked = ranked[: min(len(ranked), 16)]
    observed_concepts = sorted({term for chunk in top_ranked for term in chunk.matched_terms})
    observed_roles = sorted(
        {
            signal
            for chunk in top_ranked
            for signal in (chunk.file_role, *chunk.file_landmarks)
        }
    )
    explored_plan = plan_context(
        task,
        observed_concepts=observed_concepts,
        observed_roles=observed_roles,
    )
    graph_allowed, graph_gate_reason = _graph_gate(explored_plan, workflow)
    graph_policy = decide_graph_policy(
        requested_mode=graph_backend,
        plan=explored_plan,
        workflow=workflow,
        scanned_files=sum(int(repo["scanned_files"]) for repo in repo_summaries),
        candidate_chunks=len(candidate_chunks),
        repo_count=len(repo_inputs),
        explicit_graph_allowed=graph_allowed,
        explicit_graph_reason=graph_gate_reason,
    )
    graph_manifest: dict[str, Any] = {
        "backend": graph_backend,
        "requested_mode": graph_backend,
        "enabled": graph_policy.enabled,
        "attempted": False,
        "used": False,
        "decision": graph_policy.decision,
        "reasons": list(graph_policy.reasons),
        "policy": graph_policy.to_dict(),
        "gate_reason": (
            graph_gate_reason if graph_backend == "graphify" else graph_policy.reasons[0]
        ),
        "hop_limit": graph_hops,
        "provider_version": GRAPHIFY_EXPECTED_VERSION,
        "provider_commit": GRAPHIFY_EXPECTED_COMMIT,
        "repos": [],
    }
    graph_signals: dict[str, tuple[GraphCandidate, ...]] = {}
    if graph_policy.enabled:
        graph_manifest["attempted"] = True
        if graph_json is not None and len(repo_inputs) != 1:
            graph_manifest["repos"].append(
                {
                    "status": "unavailable",
                    "reason": "explicit_graph_requires_single_repo",
                }
            )
        else:
            for repo_input in repo_inputs:
                repo_started = time.perf_counter()
                explicit_graph = Path(graph_json).resolve() if graph_json is not None else None
                graph_path = explicit_graph
                invocation: dict[str, Any] = {
                    "status": "not_run",
                    "reason": None,
                    "version": GRAPHIFY_EXPECTED_VERSION,
                }
                if graph_path is None:
                    output_root = _graphify_cache_root(
                        repo_input.root,
                        revisions_by_repo[str(repo_input.root)],
                    )
                    cached_path = output_root / "graphify-out" / "graph.json"
                    if cached_path.is_file():
                        graph_path = cached_path
                        invocation["status"] = "cached"
                    else:
                        run_result = run_graphify(
                            repo_input.root,
                            output_root,
                            executable=graphify_executable,
                            timeout_seconds=graph_timeout,
                        )
                        invocation = {
                            "status": run_result.status,
                            "reason": run_result.reason,
                            "version": run_result.version,
                        }
                        graph_path = run_result.graph_path
                else:
                    invocation["status"] = "provided"

                backend_result = (
                    GraphifyAdapter(graph_path).candidates_for_task(
                        repo_input.root,
                        task,
                        max_hops=graph_hops,
                        limit=max(50, top_k * 3),
                    )
                    if graph_path is not None
                    else None
                )
                candidates = (
                    backend_result.candidates
                    if backend_result is not None and backend_result.available
                    else ()
                )
                repo_chunks = chunks_by_repo[str(repo_input.root)]
                repo_signals = _graph_signals_for_chunks(repo_chunks, candidates)
                graph_signals.update(repo_signals)
                graph_manifest["repos"].append(
                    {
                        "repo_label": repo_input.label,
                        "repo_path": str(repo_input.root),
                        "graph_path": str(graph_path) if graph_path is not None else None,
                        "status": (
                            backend_result.status
                            if backend_result is not None
                            else invocation["status"]
                        ),
                        "reason": (
                            backend_result.reason
                            if backend_result is not None
                            else invocation["reason"]
                        ),
                        "invocation": invocation,
                        "candidate_count": len(candidates),
                        "signaled_chunk_count": len(repo_signals),
                        "relations": sorted(
                            {
                                relation
                                for candidate in candidates
                                for relation in candidate.relations
                            }
                        ),
                        "confidences": sorted(
                            {candidate.confidence for candidate in candidates}
                        ),
                        "runtime_ms": round(
                            (time.perf_counter() - repo_started) * 1000,
                            2,
                        ),
                    }
                )
            if graph_signals:
                ranked = rank_chunks(
                    candidate_chunks,
                    task,
                    evidence_slots=evidence_slot_names if workflow == "feature" else (),
                    graph_signals=graph_signals,
                )
                graph_manifest["used"] = True
    use_repository_map = (
        workflow == "feature"
        and budget >= 1_500
        and explored_plan.grounding.status != "grounded"
    )
    repo_section = ""
    if len(repo_inputs) > 1:
        repo_lines = "\n".join(
            f"- `{repo_input.label}`: {repo_input.root}" for repo_input in repo_inputs
        )
        repo_section = f"## Repositories\n\n{repo_lines}\n\n"
    provenance_section = ""
    if provenance_sections:
        provenance_rendered = "\n\n".join(section.render() for section in provenance_sections)
        provenance_section = f"## Repository Provenance\n\n{provenance_rendered}\n\n"
    repository_map_section = ""
    if use_repository_map:
        rendered_maps = "\n\n".join(item["markdown"].rstrip() for item in repository_maps)
        repository_map_section = f"## Repository Map\n\n{rendered_maps}\n\n"
    prefix = (
        "# AgenVantage Context Package\n\n"
        "## Instructions\n\n"
        f"{instructions}\n\n"
        "## Task\n\n"
        f"{task}\n\n"
        f"{repo_section}"
        f"{provenance_section}"
        f"{repository_map_section}"
        "## Selected Repository Context\n\n"
    )
    required_tokens = counter.count(prefix)
    if required_tokens > budget:
        raise ValueError(
            f"Instructions and task use {required_tokens} tokens, exceeding budget {budget}."
        )
    effective_budget = budget
    if workflow == "feature" and budget > _FEATURE_TARGET_CONTEXT_BUDGET:
        if explored_plan.grounding.status == "grounded":
            target_budget = _FEATURE_TARGET_CONTEXT_BUDGET
        elif explored_plan.grounding.status == "partial":
            target_budget = 4_000
        else:
            target_budget = budget
        effective_budget = min(budget, max(target_budget, required_tokens))

    selected: list[CodeChunk] = []
    excluded: list[dict[str, Any]] = []
    rendered = prefix
    candidate_pool = _build_candidate_pool(ranked, top_k)
    change_surface = (
        _build_feature_change_surface(ranked, task, include_diff, include_log, task_plan)
        if workflow == "feature"
        else None
    )
    selected_paths: Counter[str] = Counter()
    selected_repos: Counter[str] = Counter()
    selected_terms: set[str] = set()
    selected_dependency_targets: Counter[tuple[str, str]] = Counter()
    selected_graph_communities: Counter[str] = Counter()
    multiple_repos = len(repo_inputs) > 1
    selected_budget_tokens = required_tokens
    addition_token_cache: dict[str, tuple[str, int]] = {}
    missing_mandatory_chunks: list[dict[str, str]] = []
    required_minimum_budget = required_tokens

    def _addition_for(chunk: CodeChunk) -> tuple[str, int]:
        cached = addition_token_cache.get(chunk.chunk_id)
        if cached is not None:
            return cached
        addition = chunk.render() + "\n\n"
        # Budget checks use cached per-chunk additions instead of repeatedly
        # tokenizing the full growing prompt. Final accounting below remains exact.
        cached = (addition, chunk.addition_tokens or counter.count(addition))
        addition_token_cache[chunk.chunk_id] = cached
        return cached

    def _fits_budget(addition_tokens: int, limit: int) -> bool:
        return selected_budget_tokens + addition_tokens <= limit

    def _reserve_chunks(chunks: Iterable[CodeChunk], *, mandatory: bool = False) -> None:
        nonlocal rendered, selected_budget_tokens, candidate_pool
        for chunk in chunks:
            addition, addition_tokens = _addition_for(chunk)
            if not _fits_budget(addition_tokens, effective_budget):
                excluded.append(
                    {"id": chunk.chunk_id, "reason": "exceeds effective token budget"}
                )
                if mandatory:
                    missing_mandatory_chunks.append(
                        {"id": chunk.chunk_id, "path": chunk.display_path}
                    )
                continue
            selected.append(chunk)
            rendered += addition
            selected_budget_tokens += addition_tokens
            selected_paths[chunk.display_path] += 1
            selected_repos[chunk.repo_label] += 1
            selected_terms.update(chunk.matched_terms)
            selected_graph_communities.update(chunk.graph_communities)
            for local_import_path in chunk.file_local_import_paths:
                selected_dependency_targets[(chunk.repo_label, local_import_path)] += 1
            candidate_pool = [
                candidate for candidate in candidate_pool if candidate.chunk_id != chunk.chunk_id
            ]

    if change_surface is not None:
        mandatory_chunks, optional_chunks = _select_feature_reserved_chunks(
            ranked, change_surface
        )
        required_minimum_budget = required_tokens + sum(
            _addition_for(chunk)[1] for chunk in mandatory_chunks
        )
        # The adaptive target is soft. Mandatory evidence may expand it up to
        # the caller's hard cap, but the hard cap is never exceeded.
        if required_minimum_budget <= budget:
            effective_budget = max(effective_budget, required_minimum_budget)
        _reserve_chunks(mandatory_chunks, mandatory=True)
        if not missing_mandatory_chunks:
            _reserve_chunks(optional_chunks)
    while candidate_pool and not missing_mandatory_chunks:
        chunk = max(
            candidate_pool,
            key=lambda candidate: (
                candidate.score
                * (1 + (0.25 * len(set(candidate.matched_terms) - selected_terms)))
                * (
                    1
                    + (
                        0.45
                        * selected_dependency_targets[(candidate.repo_label, candidate.relative_path)]
                    )
                )
                * (
                    1.35
                    if change_surface is not None
                    and any(
                        candidate.display_path
                        == target["path"]
                        for category in (
                            "edit_targets",
                            "test_targets",
                            "guard_test_targets",
                            "config_targets",
                            "supporting_targets",
                            "evidence_targets",
                        )
                        for target in change_surface.get(category, [])
                    )
                    else 1
                )
                / (
                    1
                    + (0.5 * selected_paths[candidate.display_path])
                    + (0.12 * len(set(candidate.matched_terms) & selected_terms))
                    + (
                        0.2 * selected_repos[candidate.repo_label]
                        if multiple_repos
                        else 0
                    )
                    + (
                        0.1
                        * sum(
                            selected_graph_communities[community]
                            for community in candidate.graph_communities
                        )
                    )
                ),
                candidate.score,
                -candidate.tokens,
            ),
        )
        candidate_pool.remove(chunk)
        if chunk.score < _MIN_RELEVANCE_SCORE:
            excluded.append({"id": chunk.chunk_id, "reason": "below relevance threshold"})
            continue
        addition, addition_tokens = _addition_for(chunk)
        if _fits_budget(addition_tokens, effective_budget):
            selected.append(chunk)
            selected_paths[chunk.display_path] += 1
            selected_repos[chunk.repo_label] += 1
            selected_terms.update(chunk.matched_terms)
            selected_graph_communities.update(chunk.graph_communities)
            for local_import_path in chunk.file_local_import_paths:
                selected_dependency_targets[(chunk.repo_label, local_import_path)] += 1
            rendered += addition
            selected_budget_tokens += addition_tokens
        else:
            excluded.append({"id": chunk.chunk_id, "reason": "exceeds effective token budget"})

    selected, line_pruning_manifest, rendered = _apply_line_pruning_to_selected(
        selected,
        prefix=prefix,
        task=task,
        change_surface=change_surface,
        workflow=workflow,
        counter=counter,
    )
    selected_tokens = counter.count(rendered)
    # The full-scan prompt is prefix + the same separator-wrapped chunk blocks.
    # Counting those blocks additively avoids building and tokenizing a huge
    # counterfactual prompt on normal runs while preserving the same accounting.
    candidate_corpus_tokens = required_tokens + sum(
        _addition_for(chunk)[1] for chunk in candidate_chunks
    )
    user_prompt_tokens = counter.count(task)
    savings = candidate_corpus_tokens - selected_tokens
    packed_context_plus_instructions_tokens = max(selected_tokens - user_prompt_tokens, 0)
    full_scan_context_plus_instructions_tokens = max(
        candidate_corpus_tokens - user_prompt_tokens, 0
    )
    query_concepts = _query_concepts(task)
    matched_selected_terms = {term for chunk in selected for term in chunk.matched_terms}
    covered_terms = sorted(
        label for label, variants in query_concepts if variants & matched_selected_terms
    )
    uncovered_terms = sorted(
        label for label, variants in query_concepts if not variants & matched_selected_terms
    )
    candidate_redaction_count = sum(chunk.redaction_count for chunk in candidate_chunks)
    selected_redaction_count = sum(chunk.redaction_count for chunk in selected)
    selected_redaction_types = sorted(
        {label for chunk in selected for label in chunk.redaction_types}
    )
    context_sufficiency = (
        _feature_sufficiency(
            selected,
            task_plan,
            map_included=use_repository_map,
            budget_exhausted=bool(missing_mandatory_chunks),
            repository_maps=repository_maps,
        )
        if workflow == "feature"
        else None
    )
    if context_sufficiency is not None and missing_mandatory_chunks:
        context_sufficiency["status"] = "insufficient_budget"
        context_sufficiency["missing_mandatory_evidence"] = missing_mandatory_chunks
        context_sufficiency["required_minimum_budget"] = required_minimum_budget
    guard_test_targets = (change_surface or {}).get("guard_test_targets", [])
    guard_test_target = guard_test_targets[0] if guard_test_targets else None
    selected_ids = {chunk.chunk_id for chunk in selected}
    full_suite_command = None
    if guard_test_target is not None:
        guard_repo = Path(guard_test_target["repo_path"])
        full_suite_command = _discover_full_suite_command(guard_repo)
        if full_suite_command is None:
            for repo_input in repo_inputs:
                full_suite_command = _discover_full_suite_command(repo_input.root)
                if full_suite_command is not None:
                    break
    handoff_ready = bool(
        not missing_mandatory_chunks
        and (
            context_sufficiency is None
            or context_sufficiency.get("status") == "sufficient"
        )
    )
    validation_requirements = {
        "full_suite_required": guard_test_target is not None,
        "full_suite_command": full_suite_command,
        "ready": not missing_mandatory_chunks,
        "missing_mandatory_evidence": missing_mandatory_chunks,
        "required_minimum_budget": required_minimum_budget,
        "guard_test": (
            {
                "path": guard_test_target["path"],
                "chunk_id": guard_test_target["best_chunk_id"],
                "selected": guard_test_target["best_chunk_id"] in selected_ids,
            }
            if guard_test_target is not None
            else None
        ),
    }
    graph_manifest["selected_chunk_count"] = sum(
        bool(chunk.graph_score) for chunk in selected
    )
    graph_manifest["selected_paths"] = sorted(
        {chunk.display_path for chunk in selected if chunk.graph_score}
    )
    graph_manifest["selected_relations"] = sorted(
        {
            relation
            for chunk in selected
            for relation in chunk.graph_relations
        }
    )
    report = {
        "project": "AgenVantage",
        "workflow": "repository_context_package",
        "pack_workflow": workflow,
        "task": task,
        "tokenizer": {"model": counter.model, "encoding": counter.encoding_name},
        "budget": budget,
        "effective_budget": effective_budget,
        "handoff_ready": handoff_ready,
        "required_minimum_budget": required_minimum_budget,
        "path_filters": {
            "include_globs": list(include_globs),
            "exclude_globs": list(exclude_globs),
        },
        "repo_count": len(repo_inputs),
        "repos": repo_summaries,
        "scanned_files": sum(repo["scanned_files"] for repo in repo_summaries),
        "candidate_chunks": len(candidate_chunks),
        "candidate_paths": sorted({chunk.display_path for chunk in candidate_chunks}),
        "candidate_context_tokens": candidate_corpus_tokens,
        "selected_context_tokens": selected_tokens,
        "local_tokens_omitted_vs_candidate_context": savings,
        "local_reduction_percent_vs_candidate_context": round(
            savings / candidate_corpus_tokens * 100, 2
        ),
        "prompt_token_accounting": {
            "measurement_method": "additive_independently_tokenized_blocks",
            "original_user_prompt_tokens": user_prompt_tokens,
            "additive_eligible_corpus_tokens": candidate_corpus_tokens,
            "tokens_omitted_vs_additive_eligible_corpus": savings,
            "additive_reduction_percent": round(
                savings / candidate_corpus_tokens * 100, 2
            )
            if candidate_corpus_tokens
            else 0.0,
            # Compatibility aliases retained for existing consumers.
            "full_scan_prompt_tokens": candidate_corpus_tokens,
            "packed_prompt_tokens": selected_tokens,
            "prompt_tokens_saved_vs_full_scan": savings,
            "prompt_reduction_percent_vs_full_scan": round(
                savings / candidate_corpus_tokens * 100, 2
            )
            if candidate_corpus_tokens
            else 0.0,
            "full_scan_context_plus_instructions_tokens": full_scan_context_plus_instructions_tokens,
            "packed_context_plus_instructions_tokens": packed_context_plus_instructions_tokens,
            "context_plus_instructions_tokens_saved": (
                full_scan_context_plus_instructions_tokens
                - packed_context_plus_instructions_tokens
            ),
        },
        "query_terms": sorted({label for label, _ in query_concepts}),
        "covered_query_terms": covered_terms,
        "uncovered_query_terms": uncovered_terms,
        "selected_repo_labels": sorted(selected_repos),
        "selected_chunks": [chunk.to_dict() for chunk in selected],
        "excluded_ranked_chunks": excluded,
        "safety": {
            "secret_redaction_enabled": True,
            "candidate_secret_redaction_count": candidate_redaction_count,
            "selected_secret_redaction_count": selected_redaction_count,
            "selected_secret_redaction_types": selected_redaction_types,
        },
        "change_surface": change_surface,
        "line_pruning": line_pruning_manifest,
        "validation_requirements": validation_requirements,
        # Keep the original key for consumers that adopted the first packet.
        "validation": validation_requirements,
        "context_plan": task_plan.to_dict() if workflow == "feature" else None,
        "exploration_grounding": (
            explored_plan.grounding.to_dict() if workflow == "feature" else None
        ),
        "context_sufficiency": context_sufficiency,
        "repository_maps": [
            {
                "repo_label": item["repo_label"],
                "included_in_prompt": use_repository_map,
                **item["manifest"],
            }
            for item in repository_maps
        ],
        "graph": graph_manifest,
        "index": index_totals,
        "chunk_cache": chunk_cache_totals,
        "provenance": {
            "enabled": include_diff or include_log,
            "include_diff": include_diff,
            "include_log": include_log,
            "section_count": len(provenance_sections),
            "sections": [section.to_dict() for section in provenance_sections],
            "selected_provenance_tokens": sum(section.tokens for section in provenance_sections),
        },
        "selection_strategy": (
            "adaptive feature exploration with task evidence slots, structural role ranking, optional bounded Graphify expansion, repository map fallback, and category-aware reserved paths"
            if workflow == "feature"
            else (
                "term-ranked chunks with file-level symbol and import boosts plus a moderate per-file diversity penalty"
                if not multiple_repos
                else "term-ranked chunks with file-level symbol and import boosts, moderate per-file, and light per-repository diversity penalties"
            )
        ),
        "measurement_notes": [
            "This compares local packaged context with the scanned eligible source corpus.",
            "Prompt token accounting treats the task text as the user-authored prompt and compares the rendered packed prompt with a full-scan prompt containing every eligible chunk.",
            "It does not measure provider API tokens, cache hits, response quality, or cost savings.",
            "Tracked files plus untracked, non-ignored worktree files are scanned when the target is a Git repository.",
            "Optional git provenance sections are counted inside the packaged context budget when enabled.",
            "Secret-looking values inside otherwise eligible files are redacted before ranking and rendering.",
        ],
    }
    if include_full_scan_prompt:
        full_rendered = prefix + "".join(f"{chunk.render()}\n\n" for chunk in candidate_chunks)
        report["full_scan_prompt_markdown"] = full_rendered.rstrip() + "\n"
    if len(repo_inputs) == 1:
        report["repo"] = str(repo_inputs[0].root)
    return rendered.rstrip() + "\n", report


def build_context_package(
    repo: Path,
    task: str,
    budget: int,
    counter: TokenCounter,
    top_k: int = 20,
    instructions: str = _DEFAULT_INSTRUCTIONS,
    include_diff: bool = False,
    include_log: bool = False,
    include_globs: tuple[str, ...] = (),
    exclude_globs: tuple[str, ...] = (),
    workflow: str = "generic",
    include_full_scan_prompt: bool = False,
    graph_backend: str = "off",
    graph_json: Path | None = None,
    graph_hops: int = 2,
    graph_timeout: float = 120.0,
    graphify_executable: str = "graphify",
) -> tuple[str, dict[str, Any]]:
    return build_multi_repo_context_package(
        [repo],
        task,
        budget,
        counter,
        top_k=top_k,
        instructions=instructions,
        include_diff=include_diff,
        include_log=include_log,
        include_globs=include_globs,
        exclude_globs=exclude_globs,
        workflow=workflow,
        include_full_scan_prompt=include_full_scan_prompt,
        graph_backend=graph_backend,
        graph_json=graph_json,
        graph_hops=graph_hops,
        graph_timeout=graph_timeout,
        graphify_executable=graphify_executable,
    )


def write_package_outputs(
    markdown: str,
    report: dict[str, Any],
    output: Path | None,
    manifest: Path | None,
) -> None:
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(markdown, encoding="utf-8")
    if manifest is not None:
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
