from dataclasses import dataclass

import pytest

from agenvantage.retrieval_fusion import (
    FusedCandidate,
    weighted_reciprocal_rank_fusion,
)


def test_fuses_sources_and_deduplicates_by_id() -> None:
    result = weighted_reciprocal_rank_fusion(
        {
            "lexical": ["alpha", "shared", "duplicate", "duplicate"],
            "structural": ["shared", "beta"],
        },
        rank_constant=1,
    )

    assert [item.candidate_id for item in result] == ["shared", "alpha", "beta", "duplicate"]
    assert result[0].source_ranks == (("lexical", 2), ("structural", 1))
    assert result[0].score == pytest.approx(1 / 3 + 1 / 2)
    assert result[2].candidate == "beta"


def test_source_weights_change_contribution() -> None:
    result = weighted_reciprocal_rank_fusion(
        {"text": ["text-hit"], "graph": ["graph-hit"]},
        weights={"text": 1.0, "graph": 3.0},
    )

    assert [item.candidate_id for item in result] == ["graph-hit", "text-hit"]
    assert result[0].score == pytest.approx(3 / 61)
    assert result[1].score == pytest.approx(1 / 61)


def test_ties_are_deterministic_and_independent_of_source_order() -> None:
    first = weighted_reciprocal_rank_fusion({"b": ["z"], "a": ["a"]})
    second = weighted_reciprocal_rank_fusion({"a": ["a"], "b": ["z"]})

    assert [item.candidate_id for item in first] == ["a", "z"]
    assert [item.candidate_id for item in first] == [item.candidate_id for item in second]


def test_top_k_limits_after_fusion() -> None:
    result = weighted_reciprocal_rank_fusion(
        {"source": ["a", "b", "c"]},
        top_k=2,
    )

    assert [item.candidate_id for item in result] == ["a", "b"]


def test_custom_candidate_id_preserves_first_candidate_and_metadata() -> None:
    @dataclass(frozen=True)
    class Candidate:
        path: str
        line: int

    result = weighted_reciprocal_rank_fusion(
        {
            "lexical": [Candidate("src/app.py", 10)],
            "tests": [Candidate("src/app.py", 20)],
        },
        candidate_id=lambda item: item.path,
    )

    assert result == [
        FusedCandidate(
            candidate_id="src/app.py",
            candidate=Candidate("src/app.py", 10),
            score=2 / 61,
            source_ranks=(("lexical", 1), ("tests", 1)),
        )
    ]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"rank_constant": 0}, "rank_constant"),
        ({"rank_constant": float("inf")}, "rank_constant"),
        ({"top_k": -1}, "top_k"),
        ({"weights": {"missing": 1.0}}, "unknown source"),
        ({"weights": {"source": -1.0}}, "weight"),
    ],
)
def test_rejects_invalid_configuration(kwargs: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        weighted_reciprocal_rank_fusion({"source": ["a"]}, **kwargs)
