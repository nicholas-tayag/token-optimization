from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from agenvantage.indexed_query import MetadataQuery, query_index, query_metadata


@dataclass(frozen=True)
class Entry:
    relative_path: str
    symbols: tuple[str, ...] = ()
    imports: tuple[str, ...] = ()
    imported_by_paths: tuple[str, ...] = ()
    role: str = "source"
    language: str = "python"
    landmarks: tuple[str, ...] = ()


def test_ranks_exact_channels_and_preserves_metadata() -> None:
    entries = {
        "src/api.py": Entry(
            "src/api.py",
            symbols=("create_user",),
            imports=("models.user",),
            role="source",
            landmarks=("api",),
        ),
        "tests/test_api.py": Entry(
            "tests/test_api.py",
            symbols=("test_create_user",),
            imported_by_paths=("src/api.py",),
            role="test",
            landmarks=("test-layout",),
        ),
    }

    result = query_index(
        entries,
        MetadataQuery(paths=("src/api.py",), symbols=("create_user",), top_k=2),
    )

    assert result[0].path == "src/api.py"
    assert result[0].matched_paths == ("src/api.py",)
    assert result[0].matched_symbols == ("create_user",)
    assert result[0].matched_channels == ("paths", "symbols")
    assert "paths:exact:src/api.py" in result[0].reasons
    assert "symbols:exact:create_user" in result[0].reasons


def test_accepts_json_mappings_and_protocol_like_objects() -> None:
    entries: list[Any] = [
        {
            "relative_path": "src/worker.ts",
            "symbols": ["runWorker"],
            "imports": ["queue"],
            "imported_by_paths": ["src/index.ts"],
            "file_role": "source",
            "language": "typescript",
            "landmarks": ["worker"],
        },
        Entry("src/other.py", language="python"),
    ]

    result = query_metadata(
        entries,
        {
            "symbols": ["runWorker"],
            "languages": ["typescript"],
            "top_k": 5,
        },
    )

    assert result[0].path == "src/worker.ts"
    assert result[0].matched_symbols == ("runWorker",)
    assert "languages" in result[0].matched_channels


def test_import_reverse_import_role_language_and_landmark_channels() -> None:
    entries = [
        Entry(
            "src/handler.py",
            imports=("db.models",),
            imported_by_paths=("src/routes.py",),
            role="source",
            language="python",
            landmarks=("handler",),
        )
    ]

    result = query_index(
        entries,
        {
            "imports": ["db.models"],
            "reverse_imports": ["src/routes.py"],
            "roles": ["source"],
            "languages": ["python"],
            "landmarks": ["handler"],
        },
    )

    assert result[0].matched_channels == (
        "imports",
        "reverse_imports",
        "roles",
        "languages",
        "landmarks",
    )
    assert len(result[0].reasons) == 5


def test_ties_are_stable_and_top_k_is_overridable() -> None:
    entries = {
        "z.py": Entry("z.py", role="source"),
        "a.py": Entry("a.py", role="source"),
        "m.py": Entry("m.py", role="source"),
    }
    query = {"roles": ["source"], "top_k": 1}

    assert [item.path for item in query_index(entries, query)] == ["a.py"]
    assert [item.path for item in query_index(entries, query, top_k=2)] == ["a.py", "m.py"]


def test_duplicate_paths_from_iterable_are_returned_once() -> None:
    entries = [Entry("src/value.py", symbols=("value",)), Entry("src/value.py")]

    result = query_index(entries, {"symbols": ["value"]})

    assert [item.path for item in result] == ["src/value.py"]


def test_empty_and_invalid_limits_are_explicit() -> None:
    assert query_index([], MetadataQuery(top_k=0)) == []
    with pytest.raises(ValueError, match="top_k"):
        query_index([], MetadataQuery(top_k=-1))


def test_query_does_not_read_or_require_source_content(tmp_path: Any) -> None:
    class MetadataOnly:
        relative_path = "src/locked.py"
        symbols = ("locked",)

        @property
        def content(self) -> str:
            raise AssertionError("source content must not be accessed")

    result = query_index([MetadataOnly()], {"symbols": ["locked"]})
    assert result[0].path == "src/locked.py"
