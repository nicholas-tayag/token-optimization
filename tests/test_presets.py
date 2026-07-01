from __future__ import annotations

import pytest

from agenvantage.presets import DEFAULT_PRESET, PRESETS, get_preset, preset_names


def test_expected_presets_exist() -> None:
    assert set(preset_names()) == {"explain", "review", "debug", "change", "compare"}
    assert DEFAULT_PRESET in PRESETS


def test_provenance_defaults_match_intent() -> None:
    assert get_preset("explain").include_diff is False
    assert get_preset("review").include_diff is True
    assert get_preset("debug").include_diff is True
    assert get_preset("debug").include_log is True
    assert get_preset("change").include_log is True


def test_get_preset_rejects_unknown() -> None:
    with pytest.raises(KeyError):
        get_preset("nonsense")


def test_every_preset_has_instructions() -> None:
    for name in preset_names():
        assert get_preset(name).instructions.strip()
