from __future__ import annotations

import json
import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

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
    "this",
    "use",
    "with",
    "where",
    "when",
    "what",
}
_MIN_RELEVANCE_SCORE = 9.0
_TERM_EXPANSIONS = {
    "expiration": ("expire", "ttl"),
    "expiry": ("expire", "ttl"),
    "limit": ("limiter", "limiting"),
    "limiter": ("limit", "limiting"),
    "limiting": ("limit", "limiter"),
}
_DEFAULT_INSTRUCTIONS = (
    "You are helping with a software engineering task. Base conclusions on the "
    "provided repository excerpts, cite source chunk identifiers when explaining "
    "behavior, state when context is insufficient, and avoid inventing files, "
    "tests, or runtime results."
)


@dataclass(frozen=True)
class CodeChunk:
    chunk_id: str
    relative_path: str
    start_line: int
    end_line: int
    text: str
    tokens: int
    score: float = 0.0
    matched_terms: tuple[str, ...] = ()

    def render(self) -> str:
        return (
            f"[SOURCE:{self.chunk_id}]\n"
            f"```{_language_for_path(self.relative_path)}\n{self.text.rstrip()}\n```"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.chunk_id,
            "path": self.relative_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "tokens": self.tokens,
            "score": round(self.score, 3),
            "matched_terms": list(self.matched_terms),
        }


def _language_for_path(relative_path: str) -> str:
    suffix = Path(relative_path).suffix.lower().lstrip(".")
    return {"tsx": "tsx", "ts": "typescript", "py": "python", "md": "markdown"}.get(
        suffix, suffix or "text"
    )


def _terms(value: str) -> list[str]:
    split_camel = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value).replace("_", " ")
    terms: list[str] = []
    for raw_term in re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,}", split_camel):
        term = raw_term.lower()
        candidates = [term, *(piece for piece in term.split("-") if piece != term)]
        candidates.extend(_TERM_EXPANSIONS.get(term, ()))
        terms.extend(
            candidate
            for candidate in candidates
            if len(candidate) > 1 and candidate not in _STOP_WORDS
        )
    return terms


def _query_concepts(value: str) -> tuple[tuple[str, set[str]], ...]:
    split_camel = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value).replace("_", " ")
    concepts: list[tuple[str, set[str]]] = []
    for raw_term in re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,}", split_camel):
        label = raw_term.lower()
        if len(label) <= 1 or label in _STOP_WORDS:
            continue
        variants = {label, *(piece for piece in label.split("-") if piece != label)}
        variants.update(_TERM_EXPANSIONS.get(label, ()))
        concepts.append((label, {term for term in variants if term not in _STOP_WORDS}))
    return tuple(concepts)


def _eligible_file(path: Path, repo: Path) -> bool:
    relative = path.relative_to(repo)
    lowered_parts = {part.lower() for part in relative.parts}
    if lowered_parts & _IGNORED_PARTS:
        return False
    if path.name.lower().startswith(".env"):
        return False
    return path.suffix.lower() in _SUPPORTED_SUFFIXES or path.name.lower() in _SUPPORTED_NAMES


def source_files(repo: Path, max_file_bytes: int = 300_000) -> tuple[Path, ...]:
    repo = repo.resolve()
    if not repo.is_dir():
        raise ValueError(f"Repository path does not exist: {repo}")

    tracked = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z"],
        capture_output=True,
        check=False,
        text=True,
    )
    if tracked.returncode == 0 and tracked.stdout:
        candidates = (repo / item for item in tracked.stdout.split("\0") if item)
    else:
        candidates = (path for path in repo.rglob("*") if path.is_file())

    files = []
    for path in candidates:
        if (
            path.is_file()
            and _eligible_file(path, repo)
            and path.stat().st_size <= max_file_bytes
        ):
            files.append(path)
    return tuple(sorted(files))


def chunks_for_repo(
    repo: Path,
    counter: TokenCounter,
    chunk_lines: int = 50,
    overlap_lines: int = 8,
) -> tuple[CodeChunk, ...]:
    if chunk_lines <= 0 or overlap_lines < 0 or overlap_lines >= chunk_lines:
        raise ValueError("Chunk settings require chunk_lines > overlap_lines >= 0.")

    chunks: list[CodeChunk] = []
    stride = chunk_lines - overlap_lines
    for path in source_files(repo):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        relative = path.relative_to(repo.resolve()).as_posix()
        for start in range(0, len(lines), stride):
            content_lines = lines[start : start + chunk_lines]
            if not any(line.strip() for line in content_lines):
                continue
            end_line = start + len(content_lines)
            text = "\n".join(content_lines)
            chunk_id = f"{relative}#L{start + 1}-L{end_line}"
            rendered = (
                f"[SOURCE:{chunk_id}]\n"
                f"```{_language_for_path(relative)}\n{text.rstrip()}\n```"
            )
            chunks.append(
                CodeChunk(chunk_id, relative, start + 1, end_line, text, counter.count(rendered))
            )
            if end_line == len(lines):
                break
    return tuple(chunks)


def rank_chunks(chunks: Iterable[CodeChunk], task: str) -> tuple[CodeChunk, ...]:
    query_counts = Counter(_terms(task))
    ranked: list[CodeChunk] = []
    for chunk in chunks:
        path_counts = Counter(_terms(chunk.relative_path))
        text_counts = Counter(_terms(chunk.text))
        matches = tuple(
            term
            for term in query_counts
            if path_counts.get(term, 0) or text_counts.get(term, 0)
        )
        path_score = sum(query_counts[term] * min(path_counts[term], 3) * 5 for term in matches)
        text_score = sum(query_counts[term] * min(text_counts[term], 5) for term in matches)
        coverage_bonus = 2 * len(matches)
        score = float(path_score + text_score + coverage_bonus)
        ranked.append(
            CodeChunk(
                chunk.chunk_id,
                chunk.relative_path,
                chunk.start_line,
                chunk.end_line,
                chunk.text,
                chunk.tokens,
                score,
                matches,
            )
        )
    return tuple(
        sorted(ranked, key=lambda chunk: (chunk.score, -chunk.tokens, chunk.chunk_id), reverse=True)
    )


def build_context_package(
    repo: Path,
    task: str,
    budget: int,
    counter: TokenCounter,
    top_k: int = 20,
    instructions: str = _DEFAULT_INSTRUCTIONS,
) -> tuple[str, dict[str, Any]]:
    if budget <= 0:
        raise ValueError("Token budget must be positive.")
    if top_k <= 0:
        raise ValueError("top_k must be positive.")
    task = task.strip()
    if not task:
        raise ValueError("Task must not be empty.")

    candidate_chunks = chunks_for_repo(repo.resolve(), counter)
    if not candidate_chunks:
        raise ValueError("No eligible source files were found in the repository.")
    ranked = rank_chunks(candidate_chunks, task)
    prefix = (
        "# AgenVantage Context Package\n\n"
        "## Instructions\n\n"
        f"{instructions}\n\n"
        "## Task\n\n"
        f"{task}\n\n"
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
    candidate_pool = list(ranked[:top_k])
    selected_paths: Counter[str] = Counter()
    while candidate_pool:
        chunk = max(
            candidate_pool,
            key=lambda candidate: (
                candidate.score / (1 + (0.5 * selected_paths[candidate.relative_path])),
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
            selected_paths[chunk.relative_path] += 1
            rendered += addition
        else:
            excluded.append({"id": chunk.chunk_id, "reason": "exceeds token budget"})

    selected_tokens = counter.count(rendered)
    full_rendered = prefix + "\n\n".join(chunk.render() for chunk in candidate_chunks)
    candidate_corpus_tokens = counter.count(full_rendered)
    savings = candidate_corpus_tokens - selected_tokens
    query_concepts = _query_concepts(task)
    matched_selected_terms = {term for chunk in selected for term in chunk.matched_terms}
    covered_terms = sorted(
        label for label, variants in query_concepts if variants & matched_selected_terms
    )
    uncovered_terms = sorted(
        label for label, variants in query_concepts if not variants & matched_selected_terms
    )
    report = {
        "project": "AgenVantage",
        "workflow": "repository_context_package",
        "repo": str(repo.resolve()),
        "task": task,
        "tokenizer": {"model": counter.model, "encoding": counter.encoding_name},
        "budget": budget,
        "scanned_files": len(source_files(repo.resolve())),
        "candidate_chunks": len(candidate_chunks),
        "candidate_context_tokens": candidate_corpus_tokens,
        "selected_context_tokens": selected_tokens,
        "local_tokens_omitted_vs_candidate_context": savings,
        "local_reduction_percent_vs_candidate_context": round(
            savings / candidate_corpus_tokens * 100, 2
        ),
        "query_terms": sorted({label for label, _ in query_concepts}),
        "covered_query_terms": covered_terms,
        "uncovered_query_terms": uncovered_terms,
        "selected_chunks": [chunk.to_dict() for chunk in selected],
        "excluded_ranked_chunks": excluded,
        "selection_strategy": "term-ranked chunks with a moderate per-file diversity penalty",
        "measurement_notes": [
            "This compares local packaged context with the scanned eligible source corpus.",
            "It does not measure provider API tokens, cache hits, response quality, or cost savings.",
            "Only tracked source/configuration/documentation files are scanned when the target is a Git repository.",
        ],
    }
    return rendered.rstrip() + "\n", report


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
