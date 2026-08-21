from __future__ import annotations

import os
from pathlib import Path

from agenvantage.env import find_dotenv, load_dotenv


def test_find_dotenv_walks_upward(tmp_path: Path) -> None:
    root = tmp_path / "project"
    nested = root / "a" / "b"
    nested.mkdir(parents=True)
    dotenv = root / ".env"
    dotenv.write_text("OPENAI_API_KEY=test-key\n", encoding="utf-8")

    assert find_dotenv(nested) == dotenv


def test_load_dotenv_sets_missing_values_without_overriding_existing(
    tmp_path: Path, monkeypatch
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "\n".join(
            [
                "# comment",
                "OPENAI_API_KEY=dotenv-openai",
                "export OPENAI_ADMIN_KEY='dotenv-admin'",
                'CUSTOM_VALUE="quoted-value"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "existing-admin")
    monkeypatch.delenv("CUSTOM_VALUE", raising=False)

    loaded = load_dotenv(tmp_path)

    assert loaded == dotenv
    assert os.environ["OPENAI_API_KEY"] == "dotenv-openai"
    assert os.environ["OPENAI_ADMIN_KEY"] == "existing-admin"
    assert os.environ["CUSTOM_VALUE"] == "quoted-value"


def test_load_dotenv_ignores_unquoted_inline_comments(tmp_path: Path, monkeypatch) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "\n".join(
            [
                "PLAIN=value # trailing comment",
                'QUOTED="value # kept"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("PLAIN", raising=False)
    monkeypatch.delenv("QUOTED", raising=False)

    load_dotenv(tmp_path)

    assert os.environ["PLAIN"] == "value"
    assert os.environ["QUOTED"] == "value # kept"
