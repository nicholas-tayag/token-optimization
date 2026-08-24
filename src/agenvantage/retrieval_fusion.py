"""Deterministic fusion of ranked retrieval results."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Callable, Generic, Hashable, Mapping, Sequence, TypeVar, cast


CandidateT = TypeVar("CandidateT")
CandidateId = Hashable
CandidateIdGetter = Callable[[CandidateT], CandidateId]


@dataclass(frozen=True)
class FusedCandidate(Generic[CandidateT]):
    """A unique candidate with its aggregate reciprocal-rank score."""

    candidate_id: CandidateId
    candidate: CandidateT
    score: float
    source_ranks: tuple[tuple[str, int], ...]


@dataclass
class _Aggregate(Generic[CandidateT]):
    candidate: CandidateT
    score: float
    source_ranks: list[tuple[str, int]]


def _default_candidate_id(candidate: CandidateT) -> CandidateId:
    """Extract common ID shapes without imposing a candidate model."""

    if isinstance(candidate, Mapping):
        mapping = cast(Mapping[str, object], candidate)
        for key in ("candidate_id", "id"):
            if key in mapping:
                return cast(CandidateId, mapping[key])
    for attribute in ("candidate_id", "id"):
        value = getattr(candidate, attribute, None)
        if value is not None:
            return value
    if isinstance(candidate, Hashable):
        return candidate
    raise TypeError(
        "Candidates must be hashable or expose an 'id'/'candidate_id' field; "
        "pass candidate_id= to provide an extractor."
    )


def _candidate_sort_key(candidate_id: CandidateId) -> tuple[str, str]:
    """Return a reproducible tie key for ordinary, stable IDs."""

    return (type(candidate_id).__qualname__, repr(candidate_id))


def weighted_reciprocal_rank_fusion(
    ranked_lists: Mapping[str, Sequence[CandidateT]],
    *,
    weights: Mapping[str, float] | None = None,
    rank_constant: float = 60.0,
    top_k: int | None = None,
    candidate_id: CandidateIdGetter[CandidateT] | None = None,
) -> list[FusedCandidate[CandidateT]]:
    """Fuse source-ranked candidates using weighted reciprocal rank fusion.

    Each source contributes ``weight / (rank_constant + rank)`` for a
    candidate's first occurrence, where ranks are one-based. Duplicate IDs in
    one source do not receive multiple contributions. The returned candidate
    object is the first object observed for that ID.

    ``ranked_lists`` maps a stable source name to candidates ordered from best
    to worst. Missing weights default to ``1.0``; unknown weights are rejected
    so misspelled source names cannot silently change ranking. Ties are sorted
    by candidate-ID type and representation, making results independent of
    source mapping insertion order for stable IDs.

    The aggregation pass is ``O(N)`` for ``N`` input candidates and the final
    sort is ``O(U log U)`` for ``U`` unique candidates. No external state or
    dependencies are used.
    """

    if (
        not isinstance(rank_constant, (int, float))
        or isinstance(rank_constant, bool)
        or not isfinite(rank_constant)
        or rank_constant <= 0
    ):
        raise ValueError("rank_constant must be a finite number greater than zero")
    if top_k is not None and (
        not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 0
    ):
        raise ValueError("top_k must be non-negative or None")

    if weights is not None and not isinstance(weights, Mapping):
        raise ValueError("weights must be a mapping or None")
    source_weights = weights or {}
    unknown_sources = set(source_weights).difference(ranked_lists)
    if unknown_sources:
        names = ", ".join(sorted(map(str, unknown_sources)))
        raise ValueError(f"weights contain unknown source(s): {names}")

    resolved_id = candidate_id or _default_candidate_id
    aggregates: dict[CandidateId, _Aggregate[CandidateT]] = {}

    for source, candidates in ranked_lists.items():
        weight = source_weights.get(source, 1.0)
        if isinstance(weight, bool):
            raise ValueError(f"weight for source {source!r} must be finite and non-negative")
        try:
            is_valid_weight = isfinite(weight) and weight >= 0
        except TypeError:
            is_valid_weight = False
        if not is_valid_weight:
            raise ValueError(f"weight for source {source!r} must be finite and non-negative")
        if weight == 0:
            continue

        seen_in_source: set[CandidateId] = set()
        for rank, candidate in enumerate(candidates, start=1):
            item_id = resolved_id(candidate)
            if item_id in seen_in_source:
                continue
            seen_in_source.add(item_id)

            item = aggregates.get(item_id)
            if item is None:
                item = _Aggregate(
                    candidate=candidate,
                    score=weight / (rank_constant + rank),
                    source_ranks=[(source, rank)],
                )
                aggregates[item_id] = item
            else:
                item.score += weight / (rank_constant + rank)
                item.source_ranks.append((source, rank))

    fused = [
        FusedCandidate(
            candidate_id=item_id,
            candidate=item.candidate,
            score=item.score,
            source_ranks=tuple(sorted(item.source_ranks)),
        )
        for item_id, item in aggregates.items()
    ]
    fused.sort(key=lambda item: (-item.score, _candidate_sort_key(item.candidate_id)))
    if top_k is not None:
        return fused[:top_k]
    return fused


__all__ = ["FusedCandidate", "weighted_reciprocal_rank_fusion"]
