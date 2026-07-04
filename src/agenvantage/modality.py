from __future__ import annotations

import math
import re
import statistics
from typing import Any, Iterable


DEFAULT_IMAGE_PAGE_CHAR_CAPACITY = 92_000
DEFAULT_IMAGE_PAGE_TOKENS = 4_761
DEFAULT_MIN_TEXT_TOKEN_SAVINGS_PERCENT = 20.0
DEFAULT_MIN_IMAGE_CANDIDATE_CHARS = 6_000

_HEX_RE = re.compile(r"\b[0-9a-fA-F]{8,}\b")
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_REDACTION_RE = re.compile(r"\[REDACTED_[A-Z0-9_]+\]")
_LINE_REF_RE = re.compile(r"(^|\s)[\w./-]+\.(py|ts|tsx|js|jsx|go|rs|java|md):\d+\b")


def estimate_image_tokens(
    char_count: int,
    *,
    page_char_capacity: int = DEFAULT_IMAGE_PAGE_CHAR_CAPACITY,
    image_page_tokens: int = DEFAULT_IMAGE_PAGE_TOKENS,
) -> int:
    """Estimate vision tokens for pxpipe-style text-to-image packing."""
    if char_count <= 0:
        return 0
    if page_char_capacity <= 0 or image_page_tokens <= 0:
        raise ValueError("Image token estimation requires positive page capacity and token cost.")
    return math.ceil(char_count / page_char_capacity) * image_page_tokens


def classify_verbatim_risk(text: str, path: str = "") -> dict[str, Any]:
    risk_labels: list[str] = []
    if _REDACTION_RE.search(text):
        risk_labels.append("redacted_secret_marker")
    if _UUID_RE.search(text):
        risk_labels.append("uuid_or_exact_identifier")
    if _HEX_RE.search(text):
        risk_labels.append("hex_or_hash_like_identifier")
    if _LINE_REF_RE.search(text) or re.search(r":\d+(-\d+)?$", path):
        risk_labels.append("line_addressed_reference")
    risk_labels = sorted(set(risk_labels))
    return {
        "path": path,
        "risk_labels": risk_labels,
        "requires_verbatim_text": bool(risk_labels),
    }


def estimate_modality_tradeoff(
    text: str,
    text_tokens: int,
    *,
    path: str = "",
    kind: str = "context",
    page_char_capacity: int = DEFAULT_IMAGE_PAGE_CHAR_CAPACITY,
    image_page_tokens: int = DEFAULT_IMAGE_PAGE_TOKENS,
    min_text_token_savings_percent: float = DEFAULT_MIN_TEXT_TOKEN_SAVINGS_PERCENT,
    min_image_candidate_chars: int = DEFAULT_MIN_IMAGE_CANDIDATE_CHARS,
) -> dict[str, Any]:
    char_count = len(text)
    image_tokens = estimate_image_tokens(
        char_count,
        page_char_capacity=page_char_capacity,
        image_page_tokens=image_page_tokens,
    )
    token_delta = text_tokens - image_tokens
    reduction_percent = round((token_delta / text_tokens) * 100, 2) if text_tokens > 0 else 0.0
    risk = classify_verbatim_risk(text, path)
    chars_per_text_token = round(char_count / text_tokens, 4) if text_tokens > 0 else 0.0

    if text_tokens <= 0 or char_count <= 0:
        reason = "empty_context"
    elif risk["requires_verbatim_text"]:
        reason = "verbatim_risk_keep_text"
    elif char_count < min_image_candidate_chars:
        reason = "below_minimum_bulk_threshold"
    elif image_tokens >= text_tokens:
        reason = "image_token_estimate_not_lower"
    elif reduction_percent < min_text_token_savings_percent:
        reason = "below_savings_threshold"
    else:
        reason = "image_candidate_token_dense_bulk"

    should_image = reason == "image_candidate_token_dense_bulk"
    return {
        "kind": kind,
        "path": path,
        "char_count": char_count,
        "text_tokens": text_tokens,
        "chars_per_text_token": chars_per_text_token,
        "estimated_image_tokens": image_tokens,
        "estimated_token_delta": token_delta,
        "estimated_reduction_percent": reduction_percent,
        "risk_labels": risk["risk_labels"],
        "should_keep_text": not should_image,
        "should_image": should_image,
        "decision_reason": reason,
        "estimator": {
            "source": "pxpipe_inspired_theoretical_modality_gate",
            "page_char_capacity": page_char_capacity,
            "image_page_tokens": image_page_tokens,
            "min_text_token_savings_percent": min_text_token_savings_percent,
            "min_image_candidate_chars": min_image_candidate_chars,
        },
    }


def summarize_modality_tradeoffs(items: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(items)
    if not rows:
        return {
            "item_count": 0,
            "image_candidate_rate": 0.0,
            "risk_keep_text_rate": 0.0,
            "median_text_tokens": 0.0,
            "median_estimated_image_tokens": 0.0,
            "median_estimated_reduction_percent": 0.0,
            "total_text_tokens": 0,
            "total_estimated_image_tokens": 0,
            "total_estimated_tokens_saved": 0,
            "total_estimated_reduction_percent": 0.0,
        }

    text_tokens = [int(row["text_tokens"]) for row in rows]
    image_tokens = [int(row["estimated_image_tokens"]) for row in rows]
    reductions = [float(row["estimated_reduction_percent"]) for row in rows]
    total_text = sum(text_tokens)
    total_image = sum(image_tokens)
    return {
        "item_count": len(rows),
        "image_candidate_rate": round(
            sum(1 for row in rows if row["should_image"]) / len(rows),
            4,
        ),
        "risk_keep_text_rate": round(
            sum(1 for row in rows if row["risk_labels"]) / len(rows),
            4,
        ),
        "median_text_tokens": round(statistics.median(text_tokens), 2),
        "median_estimated_image_tokens": round(statistics.median(image_tokens), 2),
        "median_estimated_reduction_percent": round(statistics.median(reductions), 2),
        "total_text_tokens": total_text,
        "total_estimated_image_tokens": total_image,
        "total_estimated_tokens_saved": total_text - total_image,
        "total_estimated_reduction_percent": (
            round(((total_text - total_image) / total_text) * 100, 2) if total_text else 0.0
        ),
    }
