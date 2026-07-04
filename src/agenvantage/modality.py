from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


DEFAULT_IMAGE_PAGE_CHAR_CAPACITY = 92_000
DEFAULT_IMAGE_PAGE_TOKENS = 4_761
DEFAULT_MIN_TEXT_TOKEN_SAVINGS_PERCENT = 20.0
DEFAULT_MIN_IMAGE_CANDIDATE_CHARS = 6_000
DEFAULT_ARTIFACT_MIN_SAVINGS_PERCENT = 5.0

_HEX_RE = re.compile(r"\b[0-9a-fA-F]{8,}\b")
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_REDACTION_RE = re.compile(r"\[REDACTED_[A-Z0-9_]+\]")
_LINE_REF_RE = re.compile(
    r"(^|\s)[\w./-]+\.(py|ts|tsx|js|jsx|go|rs|java|md)(?::\d+\b|#L\d+)"
)


@dataclass(frozen=True)
class ModalityProviderProfile:
    profile_id: str
    label: str
    page_width: int
    page_height: int
    token_formula: str
    supported_model_prefixes: tuple[str, ...]

    def estimate_page_tokens(self, width: int | None = None, height: int | None = None) -> int:
        width = width or self.page_width
        height = height or self.page_height
        if self.token_formula == "anthropic_patches_28":
            return math.ceil(width / 28) * math.ceil(height / 28)
        if self.token_formula == "anthropic_high_res_patches_28":
            return math.ceil(math.ceil(width / 28) * math.ceil(height / 28) * 1.5)
        if self.token_formula == "openai_conservative_tiles":
            return 85 + (170 * math.ceil(width / 512) * math.ceil(height / 512))
        raise ValueError(f"Unknown modality token formula: {self.token_formula}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "label": self.label,
            "page_width": self.page_width,
            "page_height": self.page_height,
            "token_formula": self.token_formula,
            "supported_model_prefixes": list(self.supported_model_prefixes),
            "estimated_page_tokens": self.estimate_page_tokens(),
        }


PROVIDER_PROFILES: dict[str, ModalityProviderProfile] = {
    "anthropic_standard": ModalityProviderProfile(
        profile_id="anthropic_standard",
        label="Claude standard vision estimate",
        page_width=1568,
        page_height=728,
        token_formula="anthropic_patches_28",
        supported_model_prefixes=("claude-",),
    ),
    "anthropic_high_res": ModalityProviderProfile(
        profile_id="anthropic_high_res",
        label="Claude high-resolution conservative estimate",
        page_width=1568,
        page_height=728,
        token_formula="anthropic_high_res_patches_28",
        supported_model_prefixes=("claude-",),
    ),
    "openai_estimate": ModalityProviderProfile(
        profile_id="openai_estimate",
        label="OpenAI conservative image-token estimate",
        page_width=1536,
        page_height=768,
        token_formula="openai_conservative_tiles",
        supported_model_prefixes=("gpt-", "o"),
    ),
}

_SOURCE_BLOCK_RE = re.compile(
    r"(?P<rendered>\[SOURCE:(?P<id>[^\]]+)\]\n```(?P<language>[^\n`]*)\n"
    r"(?P<text>.*?)\n```)",
    re.DOTALL,
)
_URL_RE = re.compile(r"\bhttps?://[^\s)\"'<>]+")
_PATH_RE = re.compile(r"(?:[\w@~+.-]+)?(?:/[\w.@+.-]+)+\.[A-Za-z]\w{0,8}\b")
_DIR_PATH_RE = re.compile(r"/[\w.@+.-]+(?:/[\w.@+.-]+)+/?")
_VERSION_RE = re.compile(r"\bv?\d+\.\d+(?:\.\d+)?(?:[-+][\w.]+)?\b")
_FLAG_RE = re.compile(r"(?:^|[^\w-])(--?[A-Za-z][\w-]+)")
_LARGE_NUMBER_RE = re.compile(r"\b\d[\d,_]{3,}\b")
_DECIMAL_RE = re.compile(r"\b\d+\.\d+\b")
_CONST_RE = re.compile(r"\b[A-Z][A-Z0-9]{2,}(?:_[A-Z0-9]+)+\b")
_TICKET_RE = re.compile(r"\b(?=[A-Z0-9-]{0,119}\d)[A-Z][A-Z0-9]+(?:-[A-Z0-9]+)+\b")
_ROUTE_RE = re.compile(r"\b(?:GET|POST|PUT|PATCH|DELETE)\s+(/[A-Za-z0-9_./:{}-]+)\b")
_TEST_NAME_RE = re.compile(r"\b(?:test|it|describe)_[A-Za-z0-9_]{3,}\b|\btest[A-Z][A-Za-z0-9_]+\b")
_LINE_TOKEN_RE = re.compile(r"\b[\w./-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|md)#L\d+(?:-L\d+)?\b")
_FACT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("url", _URL_RE),
    ("uuid", _UUID_RE),
    ("line_ref", _LINE_TOKEN_RE),
    ("route", _ROUTE_RE),
    ("path", _PATH_RE),
    ("dir_path", _DIR_PATH_RE),
    ("hex", _HEX_RE),
    ("version", _VERSION_RE),
    ("flag", _FLAG_RE),
    ("large_number", _LARGE_NUMBER_RE),
    ("decimal", _DECIMAL_RE),
    ("const", _CONST_RE),
    ("ticket", _TICKET_RE),
    ("test_name", _TEST_NAME_RE),
)
_MAX_FACTSHEET_SCAN = 262_144
_MAX_FACTSHEET_ENTRIES = 64


@dataclass(frozen=True)
class SourceBlock:
    block_id: str
    rendered: str
    text: str
    language: str
    path: str
    start_line: int | None
    end_line: int | None
    tokens: int
    redaction_count: int
    role: str


@dataclass(frozen=True)
class RenderPage:
    path: Path
    width: int
    height: int
    estimated_image_tokens: int
    char_start: int
    char_end: int


_FONT_5X7: dict[str, tuple[str, ...]] = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01111", "10000", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "J": ("00111", "00010", "00010", "00010", "10010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "10101", "01010"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "10000", "11110", "00001", "00001", "11110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    "_": ("00000", "00000", "00000", "00000", "00000", "00000", "11111"),
    ".": ("00000", "00000", "00000", "00000", "00000", "01100", "01100"),
    ",": ("00000", "00000", "00000", "00000", "01100", "01100", "01000"),
    ":": ("00000", "01100", "01100", "00000", "01100", "01100", "00000"),
    ";": ("00000", "01100", "01100", "00000", "01100", "01100", "01000"),
    "/": ("00001", "00010", "00010", "00100", "01000", "01000", "10000"),
    "\\": ("10000", "01000", "01000", "00100", "00010", "00010", "00001"),
    "#": ("01010", "01010", "11111", "01010", "11111", "01010", "01010"),
    "=": ("00000", "11111", "00000", "00000", "11111", "00000", "00000"),
    "+": ("00000", "00100", "00100", "11111", "00100", "00100", "00000"),
    "*": ("00000", "10101", "01110", "11111", "01110", "10101", "00000"),
    "'": ("01100", "01100", "00100", "00000", "00000", "00000", "00000"),
    "\"": ("01010", "01010", "01010", "00000", "00000", "00000", "00000"),
    "`": ("01000", "00100", "00010", "00000", "00000", "00000", "00000"),
    "(": ("00010", "00100", "01000", "01000", "01000", "00100", "00010"),
    ")": ("01000", "00100", "00010", "00010", "00010", "00100", "01000"),
    "[": ("01110", "01000", "01000", "01000", "01000", "01000", "01110"),
    "]": ("01110", "00010", "00010", "00010", "00010", "00010", "01110"),
    "{": ("00010", "00100", "00100", "01000", "00100", "00100", "00010"),
    "}": ("01000", "00100", "00100", "00010", "00100", "00100", "01000"),
    "<": ("00010", "00100", "01000", "10000", "01000", "00100", "00010"),
    ">": ("01000", "00100", "00010", "00001", "00010", "00100", "01000"),
    "|": ("00100", "00100", "00100", "00100", "00100", "00100", "00100"),
    "!": ("00100", "00100", "00100", "00100", "00100", "00000", "00100"),
    "?": ("01110", "10001", "00001", "00010", "00100", "00000", "00100"),
    "@": ("01110", "10001", "10111", "10101", "10111", "10000", "01110"),
    "$": ("00100", "01111", "10100", "01110", "00101", "11110", "00100"),
    "%": ("11001", "11010", "00010", "00100", "01000", "01011", "10011"),
    "&": ("01100", "10010", "10100", "01000", "10101", "10010", "01101"),
    "~": ("00000", "00000", "01000", "10101", "00010", "00000", "00000"),
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
    "?fallback": ("11111", "10001", "00010", "00100", "00100", "00000", "00100"),
}


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


def get_provider_profile(profile_id: str = "anthropic_standard") -> ModalityProviderProfile:
    try:
        return PROVIDER_PROFILES[profile_id]
    except KeyError as exc:
        valid = ", ".join(sorted(PROVIDER_PROFILES))
        raise ValueError(f"Unknown modality provider profile: {profile_id}. Valid: {valid}") from exc


def model_supported_by_profile(model: str, profile: ModalityProviderProfile) -> bool:
    if not model:
        return False
    lowered = model.lower()
    return any(lowered.startswith(prefix) for prefix in profile.supported_model_prefixes)


def _fact_kind(token: str, default_kind: str) -> str:
    if _UUID_RE.fullmatch(token):
        return "uuid"
    if _HEX_RE.fullmatch(token):
        return "hex_or_hash"
    if _CONST_RE.fullmatch(token):
        return "constant_or_env"
    if _FLAG_RE.search(f" {token}"):
        return "cli_flag"
    if _URL_RE.fullmatch(token):
        return "url"
    if _PATH_RE.fullmatch(token) or _DIR_PATH_RE.fullmatch(token):
        return "path"
    if _TICKET_RE.fullmatch(token):
        return "ticket_or_advisory"
    if _ROUTE_RE.search(f"GET {token}"):
        return "route"
    if _TEST_NAME_RE.fullmatch(token):
        return "test_name"
    return default_kind


def _fact_priority(kind: str) -> int:
    if kind in {
        "uuid",
        "hex_or_hash",
        "constant_or_env",
        "cli_flag",
        "large_number",
        "decimal",
        "ticket_or_advisory",
        "line_ref",
    }:
        return 0
    if kind in {"path", "dir_path", "version", "route", "test_name"}:
        return 1
    if kind == "url":
        return 2
    return 1


def extract_factsheet_entries(
    text: str,
    *,
    max_entries: int = _MAX_FACTSHEET_ENTRIES,
) -> list[dict[str, Any]]:
    scan = text[:_MAX_FACTSHEET_SCAN]
    counts: dict[str, dict[str, Any]] = {}
    for kind, pattern in _FACT_PATTERNS:
        for match in pattern.finditer(scan):
            token = (match.group(1) if match.lastindex else match.group(0)).strip()
            token = token.rstrip(".,;:!?")
            if not (3 <= len(token) <= 160):
                continue
            actual_kind = _fact_kind(token, kind)
            entry = counts.setdefault(
                token,
                {
                    "token": token,
                    "kind": actual_kind,
                    "count": 0,
                    "priority": _fact_priority(actual_kind),
                },
            )
            entry["count"] += 1
    ordered = sorted(
        counts.values(),
        key=lambda item: (
            int(item["priority"]),
            -int(item["count"]),
            -len(str(item["token"])),
            str(item["token"]),
        ),
    )
    kept: list[dict[str, Any]] = []
    for entry in ordered:
        token = str(entry["token"])
        if any(token != str(item["token"]) and token in str(item["token"]) for item in kept):
            continue
        kept.append(
            {
                "token": token,
                "kind": entry["kind"],
                "count": entry["count"],
            }
        )
        if len(kept) >= max_entries:
            break
    return kept


def factsheet_text(entries: Iterable[dict[str, Any]]) -> str:
    parts = []
    for entry in entries:
        token = str(entry["token"])
        count = int(entry.get("count", 1))
        parts.append(f"{token} x{count}" if count > 1 else token)
    if not parts:
        return ""
    return (
        "[Exact identifiers from imaged context - quote these from text, "
        "not from the image: "
        + " · ".join(parts)
        + "]"
    )


def recoverable_block_id(kind: str, text: str, source_hint: str = "") -> str:
    digest = hashlib.sha256(f"{kind}\0{source_hint}\0{text}".encode("utf-8")).hexdigest()[:12]
    return f"rec_{digest}"


def parse_source_blocks(
    markdown: str,
    report: dict[str, Any],
    counter: Any,
) -> list[SourceBlock]:
    metadata = {chunk["id"]: chunk for chunk in report.get("selected_chunks", [])}
    blocks: list[SourceBlock] = []
    for match in _SOURCE_BLOCK_RE.finditer(markdown):
        block_id = match.group("id")
        meta = metadata.get(block_id, {})
        blocks.append(
            SourceBlock(
                block_id=block_id,
                rendered=match.group("rendered"),
                text=match.group("text"),
                language=match.group("language"),
                path=str(meta.get("path") or block_id.split("#", 1)[0]),
                start_line=int(meta["start_line"]) if meta.get("start_line") else None,
                end_line=int(meta["end_line"]) if meta.get("end_line") else None,
                tokens=int(meta.get("tokens") or counter.count(match.group("rendered"))),
                redaction_count=int(meta.get("redaction_count") or 0),
                role="unclassified",
            )
        )
    return blocks


def _target_path_sets(report: dict[str, Any]) -> dict[str, set[str]]:
    change_surface = report.get("change_surface") or {}
    path_sets: dict[str, set[str]] = {}
    for category in ("edit_targets", "test_targets", "config_targets", "supporting_targets"):
        path_sets[category] = {
            str(item.get("path"))
            for item in change_surface.get(category, [])
            if isinstance(item, dict) and item.get("path")
        }
    return path_sets


def _path_suffix(path: str) -> str:
    return Path(path.split("#", 1)[0]).suffix.lower()


def _is_gist_friendly_path(path: str) -> bool:
    lowered = path.lower()
    suffix = _path_suffix(path)
    return (
        suffix in {".md", ".txt", ".log", ".json", ".yaml", ".yml"}
        or "/docs/" in lowered
        or lowered.startswith("docs/")
        or "report" in lowered
        or "log" in lowered
    )


def _block_role(block: SourceBlock, target_paths: dict[str, set[str]]) -> str:
    exact_paths = set().union(
        target_paths.get("edit_targets", set()),
        target_paths.get("test_targets", set()),
        target_paths.get("config_targets", set()),
        target_paths.get("supporting_targets", set()),
    )
    risk = classify_verbatim_risk(block.text)
    hard_risk_labels = set(risk["risk_labels"]) & {
        "redacted_secret_marker",
        "uuid_or_exact_identifier",
        "hex_or_hash_like_identifier",
    }
    if block.path in exact_paths:
        return "exact_text"
    if block.redaction_count > 0 or hard_risk_labels:
        return "exact_text"
    if _is_gist_friendly_path(block.path):
        return "gist_image_candidate"
    if block.tokens >= 750 and not risk["risk_labels"]:
        return "gist_image_candidate"
    return "exact_text"


def _visual_lines(text: str, max_cols: int) -> list[str]:
    lines: list[str] = []
    for raw_line in text.expandtabs(2).splitlines() or [""]:
        line = "".join(ch if 32 <= ord(ch) <= 126 else "?" for ch in raw_line)
        if not line:
            lines.append("")
            continue
        while len(line) > max_cols:
            lines.append(line[:max_cols])
            line = line[max_cols:]
        lines.append(line)
    return lines


def _reflow_for_image(text: str) -> str:
    # Render-only compaction: original text remains available in recoverable blocks.
    reflowed = re.sub(r"[ \t\r\f\v]+", " ", text)
    reflowed = re.sub(r"\n{2,}", "\n", reflowed)
    reflowed = reflowed.replace("\n", " ")
    return reflowed.strip()


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
    )


def _write_rgb_png(path: Path, width: int, height: int, pixels: bytearray) -> None:
    raw_rows = []
    stride = width * 3
    for row in range(height):
        raw_rows.append(b"\x00" + bytes(pixels[row * stride : (row + 1) * stride]))
    payload = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(
            b"IHDR",
            struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0),
        )
        + _png_chunk(b"IDAT", zlib.compress(b"".join(raw_rows), level=9))
        + _png_chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _draw_glyph(
    pixels: bytearray,
    width: int,
    height: int,
    x: int,
    y: int,
    char: str,
    *,
    scale: int = 1,
) -> None:
    glyph = _FONT_5X7.get(char.upper(), _FONT_5X7["?fallback"])
    for row, pattern in enumerate(glyph):
        for col, value in enumerate(pattern):
            if value != "1":
                continue
            for yy in range(y + row * scale, y + (row + 1) * scale):
                if yy < 0 or yy >= height:
                    continue
                base = yy * width * 3
                for xx in range(x + col * scale, x + (col + 1) * scale):
                    if xx < 0 or xx >= width:
                        continue
                    offset = base + xx * 3
                    pixels[offset : offset + 3] = b"\x1f\x29\x33"


def _render_text_page(
    text_lines: list[str],
    output_path: Path,
    profile: ModalityProviderProfile,
    *,
    scale: int = 1,
    margin_x: int = 18,
    margin_y: int = 18,
) -> None:
    width = profile.page_width
    height = profile.page_height
    pixels = bytearray([255]) * (width * height * 3)
    cell_w = 6 * scale
    cell_h = 9 * scale
    for row, line in enumerate(text_lines):
        y = margin_y + row * cell_h
        if y + (7 * scale) >= height:
            break
        for col, char in enumerate(line):
            x = margin_x + col * cell_w
            if x + (5 * scale) >= width:
                break
            _draw_glyph(pixels, width, height, x, y, char, scale=scale)
    _write_rgb_png(output_path, width, height, pixels)


def _paginate_text(
    text: str,
    profile: ModalityProviderProfile,
    *,
    scale: int = 1,
    margin_x: int = 18,
    margin_y: int = 18,
) -> list[tuple[list[str], int, int]]:
    cell_w = 6 * scale
    cell_h = 9 * scale
    max_cols = max(20, (profile.page_width - (2 * margin_x)) // cell_w)
    max_rows = max(10, (profile.page_height - (2 * margin_y)) // cell_h)
    visual = _visual_lines(text, max_cols)
    pages: list[tuple[list[str], int, int]] = []
    char_cursor = 0
    for start in range(0, len(visual), max_rows):
        page_lines = visual[start : start + max_rows]
        page_chars = sum(len(line) + 1 for line in page_lines)
        pages.append((page_lines, char_cursor, char_cursor + page_chars))
        char_cursor += page_chars
    return pages or [([""], 0, 0)]


def render_text_to_png_pages(
    text: str,
    output_dir: Path,
    base_name: str,
    profile: ModalityProviderProfile,
) -> list[RenderPage]:
    pages = []
    output_dir.mkdir(parents=True, exist_ok=True)
    for index, (lines, char_start, char_end) in enumerate(_paginate_text(text, profile), start=1):
        path = output_dir / f"{base_name}-p{index:02d}.png"
        _render_text_page(lines, path, profile)
        pages.append(
            RenderPage(
                path=path,
                width=profile.page_width,
                height=profile.page_height,
                estimated_image_tokens=profile.estimate_page_tokens(),
                char_start=char_start,
                char_end=char_end,
            )
        )
    return pages


def _estimate_pages(text: str, profile: ModalityProviderProfile) -> list[dict[str, Any]]:
    return [
        {
            "page_index": index,
            "width": profile.page_width,
            "height": profile.page_height,
            "estimated_image_tokens": profile.estimate_page_tokens(),
            "char_start": char_start,
            "char_end": char_end,
        }
        for index, (_, char_start, char_end) in enumerate(_paginate_text(text, profile), start=1)
    ]


def _pack_id(report: dict[str, Any]) -> str:
    selected_ids = "\0".join(str(chunk.get("id", "")) for chunk in report.get("selected_chunks", []))
    digest = hashlib.sha256(
        f"{report.get('task', '')}\0{selected_ids}".encode("utf-8")
    ).hexdigest()[:12]
    return f"pack_{digest}"


def _artifact_note(
    *,
    group_id: str,
    pages: list[dict[str, Any]],
    factsheet: str,
    recoverable_ids: list[str],
) -> str:
    image_lines = "\n".join(f"- `{page['path']}`" for page in pages)
    recoverable = ", ".join(f"`{item}`" for item in recoverable_ids)
    return (
        f"[GIST_IMAGE_CONTEXT:{group_id}]\n"
        "Use the image pages for background/gist only. Exact implementation evidence "
        "remains in text. If an exact value is needed, use the factsheet below or "
        "rehydrate the listed recoverable block IDs.\n\n"
        f"Image pages:\n{image_lines}\n\n"
        f"{factsheet}\n\n"
        f"Recoverable source IDs: {recoverable}\n"
    )


def apply_multimodal_pack(
    markdown: str,
    report: dict[str, Any],
    counter: Any,
    *,
    mode: str = "off",
    output_dir: Path | None = None,
    profile_id: str = "anthropic_standard",
) -> tuple[str, dict[str, Any]]:
    if mode not in {"off", "estimate", "artifact"}:
        raise ValueError("multimodal mode must be one of: off, estimate, artifact")
    if mode == "off":
        report["multimodal"] = {"mode": "off", "enabled": False}
        return markdown, report

    profile = get_provider_profile(profile_id)
    pack_id = _pack_id(report)
    artifact_root = output_dir or (Path("artifacts") / "context-images" / pack_id)
    blocks = parse_source_blocks(markdown, report, counter)
    target_paths = _target_path_sets(report)
    classified: list[SourceBlock] = []
    for block in blocks:
        classified.append(
            SourceBlock(
                block.block_id,
                block.rendered,
                block.text,
                block.language,
                block.path,
                block.start_line,
                block.end_line,
                block.tokens,
                block.redaction_count,
                _block_role(block, target_paths),
            )
        )

    imageable = [block for block in classified if block.role == "gist_image_candidate"]
    exact = [block for block in classified if block.role == "exact_text"]
    image_source = "\n\n".join(block.rendered for block in imageable)
    image_render_source = _reflow_for_image(image_source)
    text_counterfactual_tokens = counter.count(image_source) if image_source else 0
    pages_estimate = _estimate_pages(image_render_source, profile) if image_render_source else []
    estimated_image_tokens = sum(int(page["estimated_image_tokens"]) for page in pages_estimate)
    fact_entries = extract_factsheet_entries(image_source) if image_source else []
    fact_text = factsheet_text(fact_entries)
    factsheet_tokens = counter.count(fact_text) if fact_text else 0
    overhead_tokens = counter.count(
        "Use the image pages for background/gist only. Recover exact source if needed."
    )
    candidate_mixed_tokens = estimated_image_tokens + factsheet_tokens + overhead_tokens
    token_delta = text_counterfactual_tokens - candidate_mixed_tokens
    reduction_percent = (
        round((token_delta / text_counterfactual_tokens) * 100, 2)
        if text_counterfactual_tokens
        else 0.0
    )
    should_image = (
        bool(imageable)
        and len(image_source) >= DEFAULT_MIN_IMAGE_CANDIDATE_CHARS
        and token_delta > 0
        and reduction_percent >= DEFAULT_ARTIFACT_MIN_SAVINGS_PERCENT
    )
    if not imageable:
        decision_reason = "no_gist_candidate_blocks"
    elif len(image_source) < DEFAULT_MIN_IMAGE_CANDIDATE_CHARS:
        decision_reason = "below_minimum_bulk_threshold"
    elif token_delta <= 0:
        decision_reason = "image_artifact_not_cheaper_after_factsheet"
    elif reduction_percent < DEFAULT_ARTIFACT_MIN_SAVINGS_PERCENT:
        decision_reason = "below_artifact_savings_threshold"
    else:
        decision_reason = "image_artifact_candidate"

    image_pages: list[dict[str, Any]] = []
    recoverable_blocks: list[dict[str, Any]] = []
    group_id = recoverable_block_id("gist_image_group", image_source, pack_id)
    if should_image and mode == "artifact":
        image_output_dir = artifact_root / "images"
        rendered_pages = render_text_to_png_pages(
            image_render_source,
            image_output_dir,
            group_id,
            profile,
        )
        image_pages = [
            {
                "path": str(page.path),
                "width": page.width,
                "height": page.height,
                "estimated_image_tokens": page.estimated_image_tokens,
                "char_start": page.char_start,
                "char_end": page.char_end,
            }
            for page in rendered_pages
        ]
        recoverable_dir = artifact_root / "recoverable"
        recoverable_dir.mkdir(parents=True, exist_ok=True)
        for block in imageable:
            rec_id = recoverable_block_id("source_chunk", block.rendered, block.block_id)
            rec_path = recoverable_dir / f"{rec_id}.txt"
            rec_path.write_text(block.rendered.rstrip() + "\n", encoding="utf-8")
            recoverable_blocks.append(
                {
                    "id": rec_id,
                    "source_chunk_id": block.block_id,
                    "path": block.path,
                    "start_line": block.start_line,
                    "end_line": block.end_line,
                    "text_sha256": hashlib.sha256(block.rendered.encode("utf-8")).hexdigest(),
                    "text_path": str(rec_path),
                }
            )
        manifest_path = artifact_root / "manifest.json"
    else:
        image_pages = [
            {
                **page,
                "path": str(artifact_root / "images" / f"{group_id}-p{page['page_index']:02d}.png"),
            }
            for page in pages_estimate
        ]
        recoverable_blocks = [
            {
                "id": recoverable_block_id("source_chunk", block.rendered, block.block_id),
                "source_chunk_id": block.block_id,
                "path": block.path,
                "start_line": block.start_line,
                "end_line": block.end_line,
                "text_sha256": hashlib.sha256(block.rendered.encode("utf-8")).hexdigest(),
                "text_path": str(
                    artifact_root
                    / "recoverable"
                    / f"{recoverable_block_id('source_chunk', block.rendered, block.block_id)}.txt"
                ),
            }
            for block in imageable
        ]
        manifest_path = artifact_root / "manifest.json"

    if should_image and mode == "artifact":
        page_note = _artifact_note(
            group_id=group_id,
            pages=image_pages,
            factsheet=fact_text,
            recoverable_ids=[item["id"] for item in recoverable_blocks],
        )
        prefix, context_body = markdown.split("## Selected Repository Context\n\n", 1)
        rendered_by_id = {block.block_id: block.rendered for block in classified}
        imageable_ids = {block.block_id for block in imageable}
        inserted_image_note = False
        context_parts: list[str] = []
        cursor = 0
        for match in _SOURCE_BLOCK_RE.finditer(context_body):
            between = context_body[cursor : match.start()]
            if between.strip():
                context_parts.append(between.strip())
            block_id = match.group("id")
            if block_id in imageable_ids:
                if not inserted_image_note:
                    context_parts.append(page_note)
                    inserted_image_note = True
                cursor = match.end()
                continue
            context_parts.append(rendered_by_id.get(block_id, match.group("rendered")))
            cursor = match.end()
        tail = context_body[cursor:]
        if tail.strip():
            context_parts.append(tail.strip())
        mixed_markdown = (
            prefix.rstrip()
            + "\n\n## Mixed-Modality Guidance\n\n"
            + "Exact code, tests, config, paths, and identifiers are kept in text. "
            + "Attached image pages are lossy background context. Rehydrate recoverable "
            + "IDs instead of guessing exact values from images.\n\n"
            + "## Selected Repository Context\n\n"
            + "\n\n".join(part.rstrip() for part in context_parts if part.strip()).rstrip()
            + "\n"
        )
    else:
        mixed_markdown = markdown

    original_packed_tokens = int(
        report.get("prompt_token_accounting", {}).get(
            "packed_prompt_tokens",
            report.get("selected_context_tokens", counter.count(markdown)),
        )
    )
    full_scan_tokens = int(
        report.get("prompt_token_accounting", {}).get(
            "full_scan_prompt_tokens",
            report.get("candidate_context_tokens", original_packed_tokens),
        )
    )
    mixed_text_tokens = counter.count(mixed_markdown)
    hypothetical_mixed_prompt_tokens = (
        original_packed_tokens - text_counterfactual_tokens + candidate_mixed_tokens
        if should_image
        else original_packed_tokens
    )
    if should_image and mode == "artifact":
        estimated_mixed_prompt_tokens = mixed_text_tokens + estimated_image_tokens
    elif mode == "estimate":
        estimated_mixed_prompt_tokens = hypothetical_mixed_prompt_tokens
    else:
        estimated_mixed_prompt_tokens = mixed_text_tokens
    estimated_tokens_saved_vs_packed = original_packed_tokens - estimated_mixed_prompt_tokens
    estimated_tokens_saved_vs_full = full_scan_tokens - estimated_mixed_prompt_tokens
    modality_plan = {
        "mode": mode,
        "enabled": mode != "off",
        "pack_id": pack_id,
        "profile": profile.to_dict(),
        "artifact_root": str(artifact_root),
        "manifest_path": str(manifest_path),
        "decision_reason": decision_reason,
        "should_image": bool(should_image),
        "artifacts_written": bool(should_image and mode == "artifact"),
        "candidate_block_count": len(imageable),
        "exact_text_block_count": len(exact),
        "text_counterfactual_tokens": text_counterfactual_tokens,
        "factsheet_tokens": factsheet_tokens,
        "estimated_image_tokens": estimated_image_tokens,
        "estimated_mixed_prompt_tokens": estimated_mixed_prompt_tokens,
        "mixed_prompt_text_tokens": mixed_text_tokens,
        "estimated_tokens_saved_vs_packed_text": estimated_tokens_saved_vs_packed,
        "estimated_reduction_percent_vs_packed_text": (
            round((estimated_tokens_saved_vs_packed / original_packed_tokens) * 100, 2)
            if original_packed_tokens
            else 0.0
        ),
        "estimated_tokens_saved_vs_full_scan": estimated_tokens_saved_vs_full,
        "estimated_reduction_percent_vs_full_scan": (
            round((estimated_tokens_saved_vs_full / full_scan_tokens) * 100, 2)
            if full_scan_tokens
            else 0.0
        ),
        "candidate_reduction_percent": reduction_percent,
        "render_reflow_enabled": True,
        "render_reflowed_chars": len(image_render_source),
        "estimated_image_pages": image_pages if should_image else [],
        "image_attachments": image_pages if should_image and mode == "artifact" else [],
        "factsheets": [
            {
                "group_id": group_id,
                "text": fact_text,
                "entries": fact_entries,
            }
        ]
        if fact_text
        else [],
        "recoverable_blocks": recoverable_blocks if should_image and mode == "artifact" else [],
        "block_plan": [
            {
                "id": block.block_id,
                "path": block.path,
                "role": block.role,
                "tokens": block.tokens,
                "redaction_count": block.redaction_count,
                "risk_labels": classify_verbatim_risk(block.text)["risk_labels"],
            }
            for block in classified
        ],
        "notes": [
            "Image pages are local artifacts only; no provider request was made.",
            "Exact implementation evidence stays text by default.",
            "Estimated mixed tokens add local text tokens and provider-profile image-token estimates.",
        ],
    }
    report["multimodal"] = modality_plan
    if mode == "artifact" and should_image:
        artifact_root.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(modality_plan, indent=2) + "\n", encoding="utf-8")
    return mixed_markdown, report
