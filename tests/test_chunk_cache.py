from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agenvantage.chunk_cache import CachedChunk, ChunkCache, ChunkCacheKey, JSONValue


def _key(path: str = "src/app.py", content_hash: str = "hash-1") -> ChunkCacheKey:
    return ChunkCacheKey(path, content_hash, "cl100k_base", 80, 10)


def test_persists_json_payload_and_tracks_stats(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    cache = ChunkCache(repository, tmp_path / "cache")
    key = _key()
    payload: dict[str, JSONValue] = {
        "redacted": True,
        "text": "tokenized [REDACTED]",
        "lines": [1, 2],
    }

    assert cache.save_many([(key, payload)]) == 1
    assert cache.load_many([key]) == {key: payload}
    assert cache.stats() == {"hits": 1, "misses": 0, "writes": 1}

    reopened = ChunkCache(repository, tmp_path / "cache")
    assert reopened.load_many([key]) == {key: payload}
    assert reopened.stats() == {"hits": 1, "misses": 0, "writes": 0}


def test_bulk_load_save_deduplicates_requests_and_counts_misses(tmp_path: Path) -> None:
    cache = ChunkCache(tmp_path / "repo", tmp_path / "cache")
    first = _key(content_hash="one")
    second = _key(path="tests/test_app.py", content_hash="two")

    assert cache.bulk_save(
        [CachedChunk(first, {"redacted": True, "text": "one"}), (second, {"value": 2})]
    ) == 2
    assert cache.bulk_load([first, first, second, _key(content_hash="missing")]) == {
        first: {"redacted": True, "text": "one"},
        second: {"value": 2},
    }
    assert cache.stats() == {"hits": 2, "misses": 1, "writes": 2}


def test_new_content_hash_replaces_stale_generation(tmp_path: Path) -> None:
    cache = ChunkCache(tmp_path / "repo", tmp_path / "cache")
    old_key = _key(content_hash="old")
    new_key = _key(content_hash="new")
    cache.save_many([(old_key, {"version": "old"})])
    cache.save_many([(new_key, {"version": "new"})])

    assert cache.load_many([old_key, new_key]) == {new_key: {"version": "new"}}


def test_serialization_is_deterministic_and_does_not_store_forbidden_source(tmp_path: Path) -> None:
    cache = ChunkCache(tmp_path / "repo", tmp_path / "cache")
    key = _key()
    assert cache.save_many([(key, {"z": 1, "a": [True, None]})]) == 1
    raw = cache.db_path.read_bytes()
    assert b'"a":[true,null],"z":1' in raw

    with pytest.raises(ValueError, match="unredacted source"):
        cache.save_many([(key, {"source": "private implementation"})])
    with pytest.raises(ValueError, match="redacted=true"):
        cache.save_many([(key, {"text": "private implementation"})])
    with pytest.raises(ValueError, match="redacted=true"):
        cache.save_many([(key, {"chunks": [{"text": "nested private implementation"}]})])
    assert b"private implementation" not in cache.db_path.read_bytes()


def test_key_rejects_paths_that_escape_repository(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="repository-relative"):
        ChunkCacheKey("../secret.py", "hash", "tokenizer", 20, 2)
    with pytest.raises(ValueError, match="repository-relative"):
        ChunkCacheKey("/absolute.py", "hash", "tokenizer", 20, 2)


def test_environment_root_controls_database_location(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "configured-cache"
    monkeypatch.setenv("AGENVANTAGE_INDEX_ROOT", str(root))
    cache = ChunkCache(tmp_path / "repository")
    assert cache.db_path.parent == root
    assert cache.db_path.is_file()


def test_concurrent_writes_are_transaction_safe(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    root = tmp_path / "cache"

    def write(index: int) -> int:
        cache = ChunkCache(repository, root)
        key = _key(path=f"src/file_{index}.py", content_hash=f"hash-{index}")
        return cache.save_many([(key, {"redacted": True, "text": f"chunk-{index}"})])

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert sum(executor.map(write, range(12))) == 12

    cache = ChunkCache(repository, root)
    keys = [_key(path=f"src/file_{index}.py", content_hash=f"hash-{index}") for index in range(12)]
    assert len(cache.load_many(keys)) == 12


def test_invalid_json_values_are_rejected_before_writing(tmp_path: Path) -> None:
    cache = ChunkCache(tmp_path / "repo", tmp_path / "cache")
    with pytest.raises(TypeError, match="JSON-compatible"):
        cache.save_many([(_key(), {"value": object()})])  # type: ignore[dict-item]
    with pytest.raises(TypeError, match="JSON-compatible"):
        cache.save_many([(_key(), {"value": float("nan")})])
