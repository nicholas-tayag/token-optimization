from __future__ import annotations

import pytest

from agenvantage.presets import (
    DEFAULT_PRESET,
    PRESETS,
    instructions_for_preset,
    preset_names,
    get_preset,
)


def test_expected_presets_exist() -> None:
    assert set(preset_names()) == {"explain", "feature", "review", "debug", "change", "compare"}
    assert DEFAULT_PRESET in PRESETS


def test_provenance_defaults_match_intent() -> None:
    assert get_preset("explain").include_diff is False
    assert get_preset("feature").include_diff is False
    assert get_preset("feature").include_log is False
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


def test_feature_preset_includes_full_implementation_ladder() -> None:
    instructions = get_preset("feature").instructions

    assert "IMPLEMENTATION LADDER" in instructions
    assert "standard library" in instructions
    assert "guard or contract test" in instructions
    assert "call expand_context before implementing" in instructions


def test_feature_discipline_variants_are_explicit() -> None:
    assert "IMPLEMENTATION LADDER" in instructions_for_preset("feature", "full")
    assert "IMPLEMENTATION DISCIPLINE" in instructions_for_preset("feature", "lite")
    assert "IMPLEMENTATION LADDER" not in instructions_for_preset("feature", "off")
