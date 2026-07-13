"""Deterministic, compact structural repository maps built from the local index."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from agenvantage.repo_index import (
    RepositoryIndexBuildResult,
    RepositoryFileIndexEntry,
    build_repository_index,
)
from agenvantage.tokenizer import TokenCounter

_MAP_FORMAT_VERSION = 1
_MIN_MAP_BUDGET = 500
_MAX_MAP_BUDGET = 1_500
_IGNORED_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    "artifacts",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "vendor",
}
_TEXT_SUFFIXES = {
    ".c",
    ".cpp",
    ".css",
    ".go",
    ".graphql",
    ".html",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".mdx",
    ".mjs",
    ".py",
    ".rs",
    ".scss",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}
_STOP_WORDS = {
    "a",
    "an",
    "and",
    "for",
    "from",
    "in",
    "is",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}
_ROLE_PRIORITY = {
    "source": 5,
    "style": 4,
    "test": 3,
    "config": 2,
    "docs": 1,
    "generated": 0,
    "vendor": -1,
}


def _discover_files(repo: Path) -> tuple[Path, ...]:
    paths: list[Path] = []
    for path in repo.rglob("*"):
        if not path.is_file() or any(part in _IGNORED_PARTS for part in path.parts):
            continue
        if path.suffix.casefold() in _TEXT_SUFFIXES or path.name.casefold() in {
            "dockerfile",
            "makefile",
            "package.json",
            "package-lock.json",
            "pnpm-lock.yaml",
            "yarn.lock",
        }:
            paths.append(path)
    return tuple(sorted(paths, key=lambda item: item.relative_to(repo).as_posix()))


def _terms(value: str) -> set[str]:
    return {
        term.casefold()
        for term in re.findall(r"[A-Za-z][A-Za-z0-9_]{1,}", value)
        if term.casefold() not in _STOP_WORDS
    }


def _relevance(entry: RepositoryFileIndexEntry, task_terms: set[str]) -> tuple[float, tuple[str, ...]]:
    if not task_terms:
        return 0.0, ()
    path_terms = _terms(entry.relative_path)
    symbol_terms = set().union(*(_terms(symbol) for symbol in entry.symbols)) if entry.symbols else set()
    import_terms = set().union(*(_terms(value) for value in entry.imports)) if entry.imports else set()
    matched = sorted(task_terms & (path_terms | symbol_terms | import_terms))
    score = (3.0 * len(task_terms & path_terms)) + (2.5 * len(task_terms & symbol_terms))
    score += 1.25 * len(task_terms & import_terms)
    if entry.role == "test" and task_terms & {"test", "tests", "spec", "verify"}:
        score += 2.0
    return score, tuple(matched)


def _node_record(
    entry: RepositoryFileIndexEntry,
    relevance: float,
    matched_terms: tuple[str, ...],
) -> dict[str, Any]:
    centrality = len(entry.imported_by_paths) + (0.5 * len(entry.local_import_paths))
    return {
        "path": entry.relative_path,
        "role": entry.role,
        "language": entry.language,
        "symbols": list(entry.symbols[:12]),
        "imports": list(entry.local_import_paths[:12]),
        "imported_by": list(entry.imported_by_paths[:12]),
        "landmarks": list(entry.landmarks),
        "centrality": round(centrality, 3),
        "relevance": round(relevance, 3),
        "matched_terms": list(matched_terms),
    }


def _render_node(node: dict[str, Any]) -> str:
    labels = [node["role"], node["language"]]
    labels.extend(node["landmarks"])
    line = f"- {node['path']} [{', '.join(labels)}]"
    if node["symbols"]:
        line += f" symbols: {', '.join(node['symbols'])}"
    if node["imports"]:
        line += f" -> {', '.join(node['imports'])}"
    line += f"; centrality={node['centrality']}; relevance={node['relevance']}"
    return line


def _render_map(
    repo: Path,
    index: RepositoryIndexBuildResult,
    selected_nodes: list[dict[str, Any]],
    budget: int,
    counter: TokenCounter,
) -> tuple[str, int]:
    stats = index.stats
    landmarks = list(stats.get("landmarks", []))
    test_layout = stats.get("test_layout", {})
    role_counts = stats.get("role_counts", {})
    directories = list(test_layout.get("directories", []))
    test_files = list(test_layout.get("files", []))

    def render() -> tuple[str, int]:
        lines = [
            "# Repository Map",
            f"Repository: {repo.name or repo}",
            f"Revision: {stats.get('revision', 'unknown')}",
            "Roles: " + ", ".join(f"{key}={value}" for key, value in sorted(role_counts.items())),
            "",
            "## Landmarks",
        ]
        if landmarks:
            lines.extend(f"- {item['kind']}: {item['path']}" for item in landmarks)
        else:
            lines.append("- none discovered")
        lines.extend(["", "## Test Layout"])
        lines.append("Directories: " + (", ".join(directories) if directories else "none"))
        lines.append("Files: " + (", ".join(test_files) if test_files else "none"))
        lines.extend(["", "## Selected Nodes"])
        lines.extend(_render_node(node) for node in selected_nodes)
        rendered = "\n".join(lines) + "\n"
        return rendered, counter.count(rendered)

    rendered, token_count = render()
    # Remove lowest-ranked nodes and then lowest-signal list items if a large
    # repository's structural header itself would exceed the requested budget.
    while selected_nodes and token_count > budget:
        selected_nodes.pop()
        rendered, token_count = render()
    while landmarks and token_count > budget:
        landmarks.pop()
        rendered, token_count = render()
    while test_files and token_count > budget:
        test_files.pop()
        rendered, token_count = render()
    while directories and token_count > budget:
        directories.pop()
        rendered, token_count = render()
    return rendered, token_count


def build_repository_map(
    repo: Path,
    task: str = "",
    budget: int = 1_000,
    *,
    files: Iterable[Path] | None = None,
    index_result: RepositoryIndexBuildResult | None = None,
    counter: TokenCounter | None = None,
) -> tuple[str, dict[str, Any]]:
    """Build a stable structural map and its machine-readable manifest.

    The requested budget is constrained to the product contract's 500-1500
    token range. Exact source retrieval remains the responsibility of callers.
    """
    if budget <= 0:
        raise ValueError("Map token budget must be positive.")
    repo = Path(repo).resolve()
    effective_budget = max(_MIN_MAP_BUDGET, min(_MAX_MAP_BUDGET, budget))
    if index_result is None:
        index_result = build_repository_index(repo, files or _discover_files(repo))
    counter = counter or TokenCounter()
    task_terms = _terms(task)
    entries = index_result.entries
    scored: list[tuple[float, float, int, int, str, dict[str, Any]]] = []
    for relative_path, entry in sorted(entries.items()):
        relevance, matched_terms = _relevance(entry, task_terms)
        centrality = len(entry.imported_by_paths) + (0.5 * len(entry.local_import_paths))
        landmark_boost = 1 if entry.landmarks else 0
        scored.append(
            (
                relevance,
                centrality,
                landmark_boost,
                _ROLE_PRIORITY.get(entry.role, 0),
                relative_path,
                _node_record(entry, relevance, matched_terms),
            )
        )
    scored.sort(key=lambda item: (-item[0], -item[1], -item[2], -item[3], item[4]))

    # Keep navigation-critical role and framework coverage even when task terms
    # favor one file. The manifest still reports every discovered landmark.
    selected: list[dict[str, Any]] = []
    selected_paths: set[str] = set()
    for required_role in ("source", "style", "test", "config", "docs"):
        candidates = [item for item in scored if item[5]["role"] == required_role]
        if candidates:
            node = candidates[0][5]
            selected.append(node)
            selected_paths.add(node["path"])
    for landmark in (
        "entrypoint",
        "react-entrypoint",
        "react-root",
        "app-shell",
        "style",
        "state",
        "types",
        "test-layout",
    ):
        candidates = [item for item in scored if landmark in item[5]["landmarks"]]
        if candidates and candidates[0][5]["path"] not in selected_paths:
            node = candidates[0][5]
            selected.append(node)
            selected_paths.add(node["path"])
    for item in scored:
        node = item[5]
        if node["path"] not in selected_paths:
            selected.append(node)
            selected_paths.add(node["path"])
    selected.sort(
        key=lambda node: (
            -float(node["relevance"]),
            -float(node["centrality"]),
            -int(bool(node["landmarks"])),
            -_ROLE_PRIORITY.get(node["role"], 0),
            node["path"],
        )
    )
    rendered, token_count = _render_map(repo, index_result, selected, effective_budget, counter)
    selected_paths = {node["path"] for node in selected if f"- {node['path']} [" in rendered}
    selected_nodes = [node for node in selected if node["path"] in selected_paths]
    manifest = {
        "format_version": _MAP_FORMAT_VERSION,
        "repo": str(repo),
        "revision": index_result.stats.get("revision"),
        "freshness": index_result.stats.get("freshness", {}),
        "budget": effective_budget,
        "token_count": token_count,
        "landmarks": list(index_result.stats.get("landmarks", [])),
        "test_layout": dict(index_result.stats.get("test_layout", {})),
        "role_counts": dict(index_result.stats.get("role_counts", {})),
        "selected_nodes": selected_nodes,
        "selected_node_count": len(selected_nodes),
        "index": index_result.stats,
    }
    return rendered, manifest


def build_repo_map(*args: Any, **kwargs: Any) -> tuple[str, dict[str, Any]]:
    """Compatibility alias for callers that use the shorter tool name."""
    return build_repository_map(*args, **kwargs)


__all__ = ["build_repository_map", "build_repo_map"]
