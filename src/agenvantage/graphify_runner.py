"""Bounded out-of-process execution for the optional Graphify CLI."""

from __future__ import annotations

import subprocess
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agenvantage.graph_backend import (
    GRAPHIFY_EXPECTED_COMMIT,
    GRAPHIFY_EXPECTED_VERSION,
)
from agenvantage.graphify_adapter import DEFAULT_MAX_GRAPH_BYTES

DEFAULT_GRAPHIFY_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_OUTPUT_BYTES = 512 * 1024 * 1024
RunStatus = Literal["available", "unavailable", "error"]


@dataclass(frozen=True)
class GraphifyRunResult:
    status: RunStatus
    graph_path: Path | None = None
    reason: str | None = None
    version: str | None = None

    @property
    def available(self) -> bool:
        return self.status == "available"


def _bounded_output_size(root: Path, maximum: int) -> tuple[bool, int]:
    total = 0
    try:
        paths = sorted(root.rglob("*"), key=lambda path: path.as_posix())
        for path in paths:
            if path.is_symlink() or not path.is_file():
                continue
            total += path.stat().st_size
            if total > maximum:
                return False, total
    except OSError:
        return False, total
    return True, total


def run_graphify(
    repo: Path,
    output_root: Path,
    *,
    executable: str = "graphify",
    timeout_seconds: float = DEFAULT_GRAPHIFY_TIMEOUT_SECONDS,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    max_graph_bytes: int = DEFAULT_MAX_GRAPH_BYTES,
) -> GraphifyRunResult:
    """Run the pinned Graphify CLI and locate its bounded graph.json output."""

    repo = Path(repo).resolve()
    output_root = Path(output_root).resolve()
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive.")
    if max_output_bytes <= 0:
        raise ValueError("max_output_bytes must be positive.")
    if max_graph_bytes <= 0:
        raise ValueError("max_graph_bytes must be positive.")
    if not repo.is_dir():
        return GraphifyRunResult(status="unavailable", reason="repository_missing")

    try:
        version_result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            check=False,
            text=True,
            timeout=min(timeout_seconds, 10.0),
        )
    except FileNotFoundError:
        return GraphifyRunResult(status="unavailable", reason="graphify_missing")
    except subprocess.TimeoutExpired:
        return GraphifyRunResult(status="unavailable", reason="version_timeout")
    except OSError as exc:
        return GraphifyRunResult(status="error", reason=f"version_check_failed: {exc}")

    version_output = version_result.stdout.strip()
    match = re.search(r"(?:^|\s)(\d+\.\d+\.\d+)(?:\s|$)", version_output)
    version = match.group(1) if match else version_output
    if version_result.returncode != 0:
        return GraphifyRunResult(
            status="unavailable",
            reason="version_check_failed",
            version=version or None,
        )
    if version != GRAPHIFY_EXPECTED_VERSION:
        return GraphifyRunResult(
            status="unavailable",
            reason="incompatible_version",
            version=version or None,
        )

    try:
        output_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return GraphifyRunResult(
            status="error",
            reason=f"output_root_failed: {exc}",
            version=version,
        )

    command = [
        executable,
        "extract",
        ".",
        "--code-only",
        "--out",
        str(output_root),
    ]
    try:
        extraction = subprocess.run(
            command,
            capture_output=True,
            check=False,
            cwd=repo,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return GraphifyRunResult(
            status="unavailable",
            reason="extraction_timeout",
            version=version,
        )
    except OSError as exc:
        return GraphifyRunResult(
            status="error",
            reason=f"extraction_failed: {exc}",
            version=version,
        )
    if extraction.returncode != 0:
        return GraphifyRunResult(
            status="unavailable",
            reason="extraction_failed",
            version=version,
        )

    within_limit, _ = _bounded_output_size(output_root, max_output_bytes)
    if not within_limit:
        return GraphifyRunResult(
            status="unavailable",
            reason="output_too_large",
            version=version,
        )
    graph_paths = sorted(output_root.rglob("graph.json"), key=lambda path: path.as_posix())
    if len(graph_paths) != 1 or not graph_paths[0].is_file():
        reason = "graph_missing" if not graph_paths else "ambiguous_graph_output"
        return GraphifyRunResult(status="unavailable", reason=reason, version=version)
    try:
        if graph_paths[0].stat().st_size > max_graph_bytes:
            return GraphifyRunResult(
                status="unavailable",
                reason="graph_too_large",
                version=version,
            )
    except OSError as exc:
        return GraphifyRunResult(
            status="error",
            reason=f"graph_stat_failed: {exc}",
            version=version,
        )
    return GraphifyRunResult(
        status="available",
        graph_path=graph_paths[0],
        version=version,
    )


__all__ = [
    "DEFAULT_GRAPHIFY_TIMEOUT_SECONDS",
    "DEFAULT_MAX_OUTPUT_BYTES",
    "GRAPHIFY_EXPECTED_COMMIT",
    "GRAPHIFY_EXPECTED_VERSION",
    "GraphifyRunResult",
    "run_graphify",
]
