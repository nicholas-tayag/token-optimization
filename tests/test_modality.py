from agenvantage.modality import (
    classify_verbatim_risk,
    estimate_image_tokens,
    estimate_modality_tradeoff,
    summarize_modality_tradeoffs,
)


def test_estimate_image_tokens_uses_fixed_page_cost() -> None:
    assert estimate_image_tokens(1) == 4761
    assert estimate_image_tokens(92_000) == 4761
    assert estimate_image_tokens(92_001) == 9522


def test_dense_bulk_context_can_be_image_candidate() -> None:
    text = "\n".join(f"const field_{index} = {index};" for index in range(2000))
    tradeoff = estimate_modality_tradeoff(text, text_tokens=20_000)

    assert tradeoff["should_image"] is True
    assert tradeoff["decision_reason"] == "image_candidate_token_dense_bulk"
    assert tradeoff["estimated_image_tokens"] < tradeoff["text_tokens"]


def test_short_sparse_context_stays_text() -> None:
    tradeoff = estimate_modality_tradeoff("A short implementation note.", text_tokens=8)

    assert tradeoff["should_image"] is False
    assert tradeoff["decision_reason"] == "below_minimum_bulk_threshold"


def test_verbatim_risky_context_stays_text() -> None:
    text = "Keep exact value abcdef1234567890 and [REDACTED_SECRET]."
    risk = classify_verbatim_risk(text)
    tradeoff = estimate_modality_tradeoff(text * 1000, text_tokens=50_000)

    assert "hex_or_hash_like_identifier" in risk["risk_labels"]
    assert "redacted_secret_marker" in risk["risk_labels"]
    assert tradeoff["should_image"] is False
    assert tradeoff["decision_reason"] == "verbatim_risk_keep_text"


def test_summarize_modality_tradeoffs_counts_candidates_and_risks() -> None:
    rows = [
        estimate_modality_tradeoff("x" * 10_000, text_tokens=10_000),
        estimate_modality_tradeoff("abcdef1234567890 " * 1000, text_tokens=10_000),
    ]
    summary = summarize_modality_tradeoffs(rows)

    assert summary["item_count"] == 2
    assert summary["image_candidate_rate"] == 0.5
    assert summary["risk_keep_text_rate"] == 0.5
