from __future__ import annotations

from pathlib import Path

import pytest

from agenvantage.config import CONFIG_FILENAME, PackConfig, find_config_file, load_pack_config

tomllib = pytest.importorskip("tomllib")


def _write_config(directory: Path, body: str) -> Path:
    path = directory / CONFIG_FILENAME
    path.write_text(body, encoding="utf-8")
    return path


def test_load_pack_config_reads_pack_section(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        """
        [pack]
        budget = 4200
        model = "gpt-4o"
        preset = "review"
        top_k = 12
        include_glob = ["src/*"]
        exclude_glob = ["docs/*", "**/*.min.js"]
        graph_backend = "graphify"
        graph_json = "graphs/repo.json"
        graph_hops = 2
        graph_timeout = 15.5
        graphify_executable = "/opt/graphify"
        """,
    )

    config = load_pack_config([tmp_path])

    assert config.budget == 4200
    assert config.model == "gpt-4o"
    assert config.preset == "review"
    assert config.top_k == 12
    assert config.include_glob == ("src/*",)
    assert config.exclude_glob == ("docs/*", "**/*.min.js")
    assert config.graph_backend == "graphify"
    assert config.graph_json == tmp_path / "graphs/repo.json"
    assert config.graph_hops == 2
    assert config.graph_timeout == 15.5
    assert config.graphify_executable == "/opt/graphify"
    assert config.source is not None


def test_load_pack_config_defaults_when_missing(tmp_path: Path) -> None:
    config = load_pack_config([tmp_path])

    assert config == PackConfig()


def test_load_pack_config_accepts_auto_graph_policy(tmp_path: Path) -> None:
    _write_config(tmp_path, '[pack]\ngraph_backend = "auto"\n')

    assert load_pack_config([tmp_path]).graph_backend == "auto"


def test_find_config_file_prefers_first_search_path(tmp_path: Path) -> None:
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()
    _write_config(second, "[pack]\nbudget = 10\n")

    assert find_config_file([first, second]) == second / CONFIG_FILENAME


def test_load_pack_config_rejects_bad_types(tmp_path: Path) -> None:
    _write_config(tmp_path, "[pack]\nbudget = \"lots\"\n")

    with pytest.raises(ValueError):
        load_pack_config([tmp_path])


@pytest.mark.parametrize(
    "body",
    [
        '[pack]\ngraph_backend = "unknown"\n',
        "[pack]\ngraph_hops = 3\n",
        '[pack]\ngraph_timeout = "slow"\n',
        "[pack]\ngraph_json = 42\n",
    ],
)
def test_load_pack_config_rejects_invalid_graph_settings(
    tmp_path: Path, body: str
) -> None:
    _write_config(tmp_path, body)

    with pytest.raises(ValueError):
        load_pack_config([tmp_path])
