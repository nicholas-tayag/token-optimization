from __future__ import annotations

from typing import Any

import pytest

from benchmarks.warm_pack_validation import measure_warm_pack


def _report(
    paths: list[str],
    tokens: int,
    reused: int = 0,
    *,
    chunk_hits: int = 0,
) -> dict[str, Any]:
    return {
        "prompt_token_accounting": {"packed_prompt_tokens": tokens},
        "selected_chunks": [{"relative_path": path} for path in paths],
        "index": {
            "reused_files": reused,
            "metadata_fast_path_reused_files": reused,
            "hashes_avoided": reused,
            "content_hashes_validated": 0,
        },
        "chunk_cache": {"hits": chunk_hits, "misses": 0, "writes": 0},
    }


def test_measure_warm_pack_reports_speedup_and_preserves_output() -> None:
    reports = iter(
        [
            (_report(["src/service.py", "tests/test_service.py"], 420), "cold"),
            (
                _report(
                    ["tests/test_service.py", "src/service.py"],
                    420,
                    4,
                    chunk_hits=4,
                ),
                "warm",
            ),
        ]
    )

    result = measure_warm_pack(lambda: next(reports)[0])

    assert result["speedup"] > 0
    assert result["warm"]["hashes_avoided"] == 4
    assert result["chunk_cache"]["warm_hits"] == 4
    assert result["acceptance"] == {
        "selected_paths_equal": True,
        "packed_tokens_equal": True,
        "warm_output_preserved": True,
    }


def test_measure_warm_pack_accepts_real_pack_shape() -> None:
    result = measure_warm_pack(
        lambda: ("markdown", _report(["src/main.py"], 123, 2))
    )

    assert result["cold"]["packed_tokens"] == 123
    assert result["warm"]["selected_paths"] == ["src/main.py"]
    assert result["index_reuse"]["warm_reused_files"] == 2


def test_measure_warm_pack_rejects_missing_report() -> None:
    with pytest.raises(TypeError, match="pack callable"):
        measure_warm_pack(lambda: "not a report")


def test_measure_warm_pack_detects_changed_token_count() -> None:
    reports = iter([_report(["src/main.py"], 100), _report(["src/main.py"], 101)])

    result = measure_warm_pack(lambda: next(reports))

    assert result["acceptance"]["selected_paths_equal"] is True
    assert result["acceptance"]["packed_tokens_equal"] is False
    assert result["acceptance"]["warm_output_preserved"] is False
