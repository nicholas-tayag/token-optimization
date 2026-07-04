from __future__ import annotations

import fnmatch
import json
import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from agenvantage.repo_index import (
    RepositoryFileIndexEntry,
    build_repository_index,
    extract_symbol_occurrences,
)
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
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".mjs",
    ".py",
    ".rs",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}
_SUPPORTED_NAMES = {
    "dockerfile",
    "makefile",
    "package.json",
    "pyproject.toml",
    "readme.md",
}
_IGNORED_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
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
    "verify",
    "validation",
}
_FEATURE_CONFIG_TERMS = {
    "config",
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
_CONFIG_SUFFIXES = {".json", ".toml", ".yaml", ".yml"}
_CONFIG_NAMES = {
    "package.json",
    "pyproject.toml",
    "tsconfig.json",
    "manifest.json",
    ".env.example",
}
_FEATURE_RESERVED_COUNTS = {
    "edit_targets": 2,
    "test_targets": 1,
    "config_targets": 1,
    "supporting_targets": 1,
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
            "tokens": self.tokens,
            "redaction_count": self.redaction_count,
            "redaction_types": list(self.redaction_types),
            "score": round(self.score, 3),
            "matched_terms": list(self.matched_terms),
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
    split_camel = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value).replace("_", " ")
    terms: list[str] = []
    for raw_term in re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,}", split_camel):
        term = raw_term.lower()
        terms.extend(_term_variants(term))
    return terms


def _query_concepts(value: str) -> tuple[tuple[str, set[str]], ...]:
    split_camel = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value).replace("_", " ")
    concepts: list[tuple[str, set[str]]] = []
    for raw_term in re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,}", split_camel):
        label = raw_term.lower()
        if len(label) <= 1 or label in _STOP_WORDS:
            continue
        concepts.append((label, set(_term_variants(label))))
    return tuple(concepts)


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
        if value.startswith("[REDACTED_"):
            return match.group(0)
        redaction_counts["secret_assignment"] += 1
        quote = match.group("quote")
        return f"{match.group('key')}{match.group('sep')}{quote}[REDACTED_SECRET]{quote}"

    redacted = _PRIVATE_KEY_BLOCK_PATTERN.sub(_replace_private_key, text)
    for label, pattern, replacement in _SECRET_VALUE_PATTERNS:
        redacted, count = pattern.subn(replacement, redacted)
        if count:
            redaction_counts[label] += count
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
    if path.name.lower().startswith(".env"):
        return False
    return path.suffix.lower() in _SUPPORTED_SUFFIXES or path.name.lower() in _SUPPORTED_NAMES


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
    return path.suffix.lower() in _CONFIG_SUFFIXES or path.name.lower() in _CONFIG_NAMES


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

    tracked = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z"],
        capture_output=True,
        check=False,
        text=True,
    )
    untracked = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "--others", "--exclude-standard", "-z"],
        capture_output=True,
        check=False,
        text=True,
    )
    if tracked.returncode == 0 and untracked.returncode == 0:
        git_paths = {
            repo / item
            for output in (tracked.stdout, untracked.stdout)
            for item in output.split("\0")
            if item
        }
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
            and not _matches_any_glob(relative_path, exclude_globs)
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
) -> tuple[CodeChunk, ...]:
    if chunk_lines <= 0 or overlap_lines < 0 or overlap_lines >= chunk_lines:
        raise ValueError("Chunk settings require chunk_lines > overlap_lines >= 0.")

    chunks: list[CodeChunk] = []
    stride = chunk_lines - overlap_lines
    repo = repo.resolve()
    for path in files or source_files(repo):
        try:
            raw_text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        redacted_text, _ = _redact_sensitive_text(raw_text)
        original_lines = raw_text.splitlines()
        lines = redacted_text.splitlines()
        if len(original_lines) != len(lines):
            original_lines = lines
        relative = path.relative_to(repo).as_posix()
        display_path = f"{repo_label}/{relative}" if repo_label else relative
        indexed_entry = file_index.get(relative) if file_index is not None else None
        symbol_occurrences = (
            indexed_entry.symbol_occurrences
            if indexed_entry is not None and indexed_entry.symbol_occurrences
            else extract_symbol_occurrences(path, "\n".join(lines))
        )
        for start in range(0, len(lines), stride):
            content_lines = lines[start : start + chunk_lines]
            if not any(line.strip() for line in content_lines):
                continue
            end_line = start + len(content_lines)
            text = "\n".join(content_lines)
            original_text = "\n".join(original_lines[start:end_line])
            chunk_redactions = _redact_sensitive_text(original_text)[1]
            chunk_id = f"{display_path}#L{start + 1}-L{end_line}"
            rendered = (
                f"[SOURCE:{chunk_id}]\n"
                f"```{_language_for_path(display_path)}\n{text.rstrip()}\n```"
            )
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
                    counter.count(rendered),
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
                )
            )
            if end_line == len(lines):
                break
    return tuple(chunks)


def rank_chunks(chunks: Iterable[CodeChunk], task: str) -> tuple[CodeChunk, ...]:
    query_counts = Counter(_terms(task))
    ranked: list[CodeChunk] = []
    for chunk in chunks:
        path_counts = Counter(_terms(chunk.relative_path))
        repo_counts = Counter(_terms(chunk.repo_label))
        text_counts = Counter(_terms(chunk.text))
        chunk_symbol_counts = Counter(_terms(" ".join(chunk.chunk_symbols)))
        symbol_counts = Counter(_terms(" ".join(chunk.file_symbols)))
        import_counts = Counter(_terms(" ".join(chunk.file_imports)))
        imported_by_counts = Counter(_terms(" ".join(chunk.file_imported_by_paths)))
        matches = tuple(
            term
            for term in query_counts
            if path_counts.get(term, 0)
            or repo_counts.get(term, 0)
            or text_counts.get(term, 0)
            or chunk_symbol_counts.get(term, 0)
            or symbol_counts.get(term, 0)
            or import_counts.get(term, 0)
            or imported_by_counts.get(term, 0)
        )
        path_score = sum(query_counts[term] * min(path_counts[term], 3) * 5 for term in matches)
        repo_score = sum(query_counts[term] * min(repo_counts[term], 2) for term in matches)
        text_score = sum(query_counts[term] * min(text_counts[term], 5) for term in matches)
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
        coverage_bonus = 2 * len(matches)
        score = float(
            path_score
            + repo_score
            + text_score
            + chunk_symbol_score
            + symbol_score
            + import_score
            + imported_by_score
            + coverage_bonus
        )
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
    return "source"


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
    return score


def _build_feature_change_surface(
    ranked: tuple[CodeChunk, ...],
    task: str,
    include_diff: bool,
    include_log: bool,
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
                "local_imports": list(chunk.file_local_import_paths),
                "imported_by": list(chunk.file_imported_by_paths),
            }

    ordered = sorted(
        file_candidates.values(),
        key=lambda item: (item["score"], item["path"]),
        reverse=True,
    )
    edit_targets = [item for item in ordered if item["kind"] == "source"][: _FEATURE_RESERVED_COUNTS["edit_targets"]]

    edit_paths = {item["relative_path"] for item in edit_targets}
    support_paths: list[str] = []
    for target in edit_targets:
        support_paths.extend(target["local_imports"])
        support_paths.extend(target["imported_by"])
    supporting_targets = []
    for path in _ordered_unique(support_paths, 12):
        for item in ordered:
            if item["relative_path"] == path and item["relative_path"] not in edit_paths:
                supporting_targets.append(item)
                break
        if len(supporting_targets) >= _FEATURE_RESERVED_COUNTS["supporting_targets"]:
            break

    test_targets: list[dict[str, Any]] = []
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
        ):
            test_targets.append(item)
        if len(test_targets) >= _FEATURE_RESERVED_COUNTS["test_targets"]:
            break

    config_targets: list[dict[str, Any]] = []
    for item in ordered:
        if item["kind"] != "config":
            continue
        if feature_mentions_config or any(term in _path_terms(item["relative_path"]) for term in task_terms):
            config_targets.append(item)
        if len(config_targets) >= _FEATURE_RESERVED_COUNTS["config_targets"]:
            break

    missing_signals: list[str] = []
    if not test_targets:
        missing_signals.append("No likely test target was identified for this feature task.")
    if feature_mentions_config and not config_targets:
        missing_signals.append("Task suggests config or schema work, but no strong config target was identified.")
    if not supporting_targets and edit_targets:
        missing_signals.append("No strong supporting implementation neighbor was identified from local imports.")
    if not edit_targets:
        missing_signals.append("No likely edit target was identified from exact path, symbol, or chunk matches.")
    if (include_diff or include_log) and not (include_diff and include_log):
        missing_signals.append("Feature work may benefit from both diff and recent log provenance for changed-behavior context.")

    return {
        "edit_targets": edit_targets,
        "test_targets": test_targets,
        "config_targets": config_targets,
        "supporting_targets": supporting_targets,
        "missing_signals": missing_signals,
    }


def _select_feature_reserved_chunks(
    ranked: tuple[CodeChunk, ...],
    change_surface: dict[str, Any],
) -> list[CodeChunk]:
    reserved: list[CodeChunk] = []
    seen_paths: set[str] = set()
    for category in ("edit_targets", "test_targets", "config_targets", "supporting_targets"):
        for target in change_surface.get(category, []):
            path = target["path"]
            if path in seen_paths:
                continue
            chunk = _best_chunk_for_path(ranked, path)
            if chunk is None:
                continue
            reserved.append(chunk)
            seen_paths.add(path)
    return reserved


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
) -> tuple[str, dict[str, Any]]:
    if budget <= 0:
        raise ValueError("Token budget must be positive.")
    if top_k <= 0:
        raise ValueError("top_k must be positive.")
    task = task.strip()
    if not task:
        raise ValueError("Task must not be empty.")

    repo_inputs = _repo_inputs(repos)
    repo_summaries: list[dict[str, Any]] = []
    candidate_chunks: list[CodeChunk] = []
    provenance_sections: list[ProvenanceSection] = []
    index_totals = {
        "indexed_files": 0,
        "reused_files": 0,
        "rebuilt_files": 0,
        "skipped_files": 0,
        "symbol_count": 0,
        "import_count": 0,
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
        chunks = chunks_for_repo(
            repo_input.root,
            counter,
            files=files,
            repo_label=repo_input.label if len(repo_inputs) > 1 else None,
            file_index=index_result.entries,
        )
        repo_summaries.append(
            {
                "label": repo_input.label,
                "path": str(repo_input.root),
                "scanned_files": len(files),
                "candidate_chunks": len(chunks),
                "index": index_result.stats,
                "provenance_sections": [section.to_dict() for section in repo_provenance],
            }
        )
        for key in index_totals:
            index_totals[key] += int(index_result.stats[key])
        candidate_chunks.extend(chunks)
        provenance_sections.extend(repo_provenance)

    if not candidate_chunks:
        raise ValueError("No eligible source files were found in the provided repositories.")

    ranked = rank_chunks(candidate_chunks, task)
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
    prefix = (
        "# AgenVantage Context Package\n\n"
        "## Instructions\n\n"
        f"{instructions}\n\n"
        "## Task\n\n"
        f"{task}\n\n"
        f"{repo_section}"
        f"{provenance_section}"
        "## Selected Repository Context\n\n"
    )
    required_tokens = counter.count(prefix)
    if required_tokens > budget:
        raise ValueError(
            f"Instructions and task use {required_tokens} tokens, exceeding budget {budget}."
        )

    selected: list[CodeChunk] = []
    excluded: list[dict[str, Any]] = []
    rendered = prefix
    candidate_pool = _build_candidate_pool(ranked, top_k)
    change_surface = (
        _build_feature_change_surface(ranked, task, include_diff, include_log)
        if workflow == "feature"
        else None
    )
    selected_paths: Counter[str] = Counter()
    selected_repos: Counter[str] = Counter()
    selected_terms: set[str] = set()
    selected_dependency_targets: Counter[tuple[str, str]] = Counter()
    multiple_repos = len(repo_inputs) > 1
    if change_surface is not None:
        for chunk in _select_feature_reserved_chunks(ranked, change_surface):
            addition = chunk.render() + "\n\n"
            if counter.count(rendered + addition) > budget:
                excluded.append({"id": chunk.chunk_id, "reason": "exceeds token budget"})
                continue
            selected.append(chunk)
            rendered += addition
            selected_paths[chunk.display_path] += 1
            selected_repos[chunk.repo_label] += 1
            selected_terms.update(chunk.matched_terms)
            for local_import_path in chunk.file_local_import_paths:
                selected_dependency_targets[(chunk.repo_label, local_import_path)] += 1
            candidate_pool = [
                candidate for candidate in candidate_pool if candidate.display_path != chunk.display_path
            ]
    while candidate_pool:
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
                            "config_targets",
                            "supporting_targets",
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
                ),
                candidate.score,
                -candidate.tokens,
            ),
        )
        candidate_pool.remove(chunk)
        if chunk.score < _MIN_RELEVANCE_SCORE:
            excluded.append({"id": chunk.chunk_id, "reason": "below relevance threshold"})
            continue
        addition = chunk.render() + "\n\n"
        if counter.count(rendered + addition) <= budget:
            selected.append(chunk)
            selected_paths[chunk.display_path] += 1
            selected_repos[chunk.repo_label] += 1
            selected_terms.update(chunk.matched_terms)
            for local_import_path in chunk.file_local_import_paths:
                selected_dependency_targets[(chunk.repo_label, local_import_path)] += 1
            rendered += addition
        else:
            excluded.append({"id": chunk.chunk_id, "reason": "exceeds token budget"})

    selected_tokens = counter.count(rendered)
    full_rendered = prefix + "".join(f"{chunk.render()}\n\n" for chunk in candidate_chunks)
    candidate_corpus_tokens = counter.count(full_rendered)
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
    report = {
        "project": "AgenVantage",
        "workflow": "repository_context_package",
        "pack_workflow": workflow,
        "task": task,
        "tokenizer": {"model": counter.model, "encoding": counter.encoding_name},
        "budget": budget,
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
            "original_user_prompt_tokens": user_prompt_tokens,
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
        "index": index_totals,
        "provenance": {
            "enabled": include_diff or include_log,
            "include_diff": include_diff,
            "include_log": include_log,
            "section_count": len(provenance_sections),
            "sections": [section.to_dict() for section in provenance_sections],
            "selected_provenance_tokens": sum(section.tokens for section in provenance_sections),
        },
        "selection_strategy": (
            "feature-work exploration plus deterministic chunk ranking with category-aware reserved paths"
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
