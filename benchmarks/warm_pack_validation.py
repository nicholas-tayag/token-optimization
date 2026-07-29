"""Measure cold versus warm context packing without provider calls."""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

PackCallable = Callable[[], Any]


def _as_report(result: Any) -> Mapping[str, Any]:
    """Accept a report directly or the ``(markdown, report)`` pack result."""
    if isinstance(result, Mapping):
        return result
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], Mapping):
        return result[1]
    raise TypeError("pack callable must return a report or a (payload, report) tuple")


def _selected_paths(report: Mapping[str, Any]) -> tuple[str, ...]:
    chunks = report.get("selected_chunks", ())
    if not isinstance(chunks, (list, tuple)):
        return ()
    return tuple(sorted({str(chunk["relative_path"]) for chunk in chunks if "relative_path" in chunk}))


def _packed_tokens(report: Mapping[str, Any]) -> int:
    accounting = report.get("prompt_token_accounting", {})
    if isinstance(accounting, Mapping):
        return int(accounting.get("packed_prompt_tokens", report.get("selected_context_tokens", 0)))
    return int(report.get("selected_context_tokens", 0))


def _index_stats(report: Mapping[str, Any]) -> Mapping[str, Any]:
    stats = report.get("index", {})
    return stats if isinstance(stats, Mapping) else {}


def _chunk_cache_stats(report: Mapping[str, Any]) -> Mapping[str, Any]:
    stats = report.get("chunk_cache", {})
    return stats if isinstance(stats, Mapping) else {}


def _measure_run(pack: PackCallable) -> dict[str, Any]:
    started = time.perf_counter()
    report = _as_report(pack())
    elapsed_ms = max(0.001, round((time.perf_counter() - started) * 1_000, 3))
    index = _index_stats(report)
    chunk_cache = _chunk_cache_stats(report)
    return {
        "elapsed_ms": elapsed_ms,
        "packed_tokens": _packed_tokens(report),
        "selected_paths": list(_selected_paths(report)),
        "index_reused_files": int(index.get("reused_files", 0)),
        "metadata_fast_path_reused_files": int(
            index.get("metadata_fast_path_reused_files", 0)
        ),
        "hashes_avoided": int(index.get("hashes_avoided", 0)),
        "content_hashes_validated": int(index.get("content_hashes_validated", 0)),
        "chunk_cache_hits": int(chunk_cache.get("hits", 0)),
        "chunk_cache_misses": int(chunk_cache.get("misses", 0)),
        "chunk_cache_writes": int(chunk_cache.get("writes", 0)),
        "report": report,
    }


def measure_warm_pack(pack: PackCallable) -> dict[str, Any]:
    """Run ``pack`` cold and warm and return reproducible comparison metrics.

    The function does not know how a repository is indexed or which provider is
    used. The caller controls the callable's inputs and can therefore use this
    with a fake packer, a local packer, or another deterministic implementation.
    """
    cold = _measure_run(pack)
    warm = _measure_run(pack)
    cold_ms = float(cold["elapsed_ms"])
    warm_ms = float(warm["elapsed_ms"])
    speedup = cold_ms / warm_ms if warm_ms > 0 else 0.0
    acceptance = {
        "selected_paths_equal": cold["selected_paths"] == warm["selected_paths"],
        "packed_tokens_equal": cold["packed_tokens"] == warm["packed_tokens"],
    }
    acceptance["warm_output_preserved"] = all(acceptance.values())
    return {
        "cold": {key: value for key, value in cold.items() if key != "report"},
        "warm": {key: value for key, value in warm.items() if key != "report"},
        "elapsed_ms": {"cold": cold_ms, "warm": warm_ms},
        "speedup": round(speedup, 3),
        "index_reuse": {
            "cold_reused_files": cold["index_reused_files"],
            "warm_reused_files": warm["index_reused_files"],
            "warm_metadata_fast_path_reused_files": warm[
                "metadata_fast_path_reused_files"
            ],
            "warm_hashes_avoided": warm["hashes_avoided"],
        },
        "chunk_cache": {
            "cold_hits": cold["chunk_cache_hits"],
            "cold_misses": cold["chunk_cache_misses"],
            "cold_writes": cold["chunk_cache_writes"],
            "warm_hits": warm["chunk_cache_hits"],
            "warm_misses": warm["chunk_cache_misses"],
            "warm_writes": warm["chunk_cache_writes"],
        },
        "acceptance": acceptance,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure cold versus warm context packing.")
    parser.add_argument("repo", type=Path)
    parser.add_argument("--task", required=True)
    parser.add_argument("--budget", type=int, default=6_000)
    parser.add_argument("--top-k", type=int, default=28)
    args = parser.parse_args()

    from agenvantage.repo_context import build_context_package
    from agenvantage.tokenizer import TokenCounter

    counter = TokenCounter()

    def pack() -> tuple[str, dict[str, Any]]:
        return build_context_package(
            args.repo,
            args.task,
            args.budget,
            counter,
            top_k=args.top_k,
            workflow="feature",
        )

    result = measure_warm_pack(pack)
    print(json.dumps(result, indent=2))
    if not result["acceptance"]["warm_output_preserved"]:
        raise SystemExit("warm output did not preserve selected paths and token counts")


if __name__ == "__main__":
    main()
