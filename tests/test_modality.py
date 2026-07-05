import json
import hashlib
from pathlib import Path

from agenvantage.modality import (
    apply_multimodal_pack,
    classify_verbatim_risk,
    count_exact_identifier_signals,
    extract_factsheet_entries,
    estimate_image_tokens,
    estimate_modality_tradeoff,
    factsheet_text,
    get_provider_profile,
    recoverable_block_id,
    resolve_provider_profile,
    summarize_modality_tradeoffs,
    verify_mixed_modality_manifest,
)
from agenvantage.tokenizer import TokenCounter


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


def test_high_density_exact_identifiers_stay_text() -> None:
    text = "\n".join(
        f"GET /api/resource/{index} uses --feature-flag-{index} "
        f"and docs/service_{index}.yaml"
        for index in range(14)
    )
    text = f"{text}\n" + ("background narrative " * 1200)
    risk = classify_verbatim_risk(text)
    tradeoff = estimate_modality_tradeoff(text, text_tokens=20_000)

    assert count_exact_identifier_signals(text) >= 14
    assert "high_density_exact_identifiers" in risk["risk_labels"]
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


def test_provider_profile_uses_deterministic_patch_estimate() -> None:
    profile = get_provider_profile("anthropic_standard")

    assert profile.estimate_page_tokens() == 1456
    assert profile.to_dict()["token_formula"] == "anthropic_patches_28"


def test_auto_provider_profile_resolves_from_model_family() -> None:
    assert resolve_provider_profile("auto", model="gpt-4o-mini").profile_id == "openai_estimate"
    assert (
        resolve_provider_profile("auto", model="claude-sonnet-4-5").profile_id
        == "anthropic_standard"
    )


def test_factsheet_extracts_exact_identifier_shapes() -> None:
    entries = extract_factsheet_entries(
        "POST /api/memory/search uses src/server.js#L10-L20 with "
        "TRACE_ID=abc123ef and --max-results v1.2.3 testMemorySearch."
    )
    text = factsheet_text(entries)

    assert "/api/memory/search" in text
    assert "src/server.js#L10-L20" in text
    assert "--max-results" in text
    assert "v1.2.3" in text


def test_recoverable_block_id_is_stable_and_content_sensitive() -> None:
    first = recoverable_block_id("source_chunk", "same text", "docs.md")
    second = recoverable_block_id("source_chunk", "same text", "docs.md")
    changed = recoverable_block_id("source_chunk", "changed text", "docs.md")

    assert first == second
    assert first.startswith("rec_")
    assert first != changed


def test_apply_multimodal_pack_writes_png_and_recoverable_artifacts(tmp_path) -> None:
    background = " ".join("[]{}():;,./#L" for _ in range(3500))
    markdown = (
        "# AgenVantage Context Package\n\n"
        "## Instructions\n\nUse context.\n\n"
        "## Task\n\nAdd diagnostics.\n\n"
        "## Selected Repository Context\n\n"
        "[SOURCE:src/server.js#L1-L3]\n"
        "```javascript\n"
        "function searchMemory() { return true; }\n"
        "```\n\n"
        "[SOURCE:docs/memory-search.md#L1-L900]\n"
        "```markdown\n"
        f"{background}\n"
        "```\n"
    )
    report = {
        "task": "Add diagnostics.",
        "selected_chunks": [
            {
                "id": "src/server.js#L1-L3",
                "path": "src/server.js",
                "start_line": 1,
                "end_line": 3,
                "tokens": 30,
                "redaction_count": 0,
            },
            {
                "id": "docs/memory-search.md#L1-L900",
                "path": "docs/memory-search.md",
                "start_line": 1,
                "end_line": 900,
                "tokens": 9000,
                "redaction_count": 0,
            },
        ],
        "change_surface": {
            "edit_targets": [{"path": "src/server.js"}],
            "test_targets": [],
            "config_targets": [],
            "supporting_targets": [],
        },
        "prompt_token_accounting": {
            "packed_prompt_tokens": TokenCounter().count(markdown),
            "full_scan_prompt_tokens": TokenCounter().count(markdown),
        },
    }

    mixed, updated = apply_multimodal_pack(
        markdown,
        report,
        TokenCounter(),
        mode="artifact",
        output_dir=tmp_path / "mixed",
    )
    plan = updated["multimodal"]

    assert plan["should_image"] is True
    assert plan["image_attachments"]
    assert plan["recoverable_blocks"]
    assert plan["factsheets"]
    assert plan["estimated_tokens_saved_vs_packed_text"] > 0
    assert "GIST_IMAGE_CONTEXT" in mixed
    image_path = Path(plan["image_attachments"][0]["path"])
    assert image_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert hashlib.sha256(image_path.read_bytes()).hexdigest() == plan["image_attachments"][0][
        "sha256"
    ]
    factsheet_path = Path(plan["factsheets"][0]["text_path"])
    assert factsheet_path.is_file()
    assert hashlib.sha256(factsheet_path.read_bytes()).hexdigest() == plan["factsheets"][0][
        "text_sha256"
    ]
    for block in plan["recoverable_blocks"]:
        persisted = Path(block["text_path"]).read_bytes()
        assert hashlib.sha256(persisted).hexdigest() == block["text_sha256"]
    assert (tmp_path / "mixed" / "manifest.json").exists()
    verification = verify_mixed_modality_manifest(Path(plan["manifest_path"]))
    assert verification["ok"] is True
    assert verification["image_attachment_count"] == len(plan["image_attachments"])
    assert verification["recoverable_block_count"] == len(plan["recoverable_blocks"])
    assert verification["factsheet_count"] == len(plan["factsheets"])


def test_apply_multimodal_pack_keeps_line_referenced_docs_as_text(tmp_path) -> None:
    line_references = "\n".join(
        f"Inspect src/service_{index}.py#L{index + 1}-L{index + 3} before changing behavior."
        for index in range(120)
    )
    markdown = (
        "# AgenVantage Context Package\n\n"
        "## Instructions\n\nUse context.\n\n"
        "## Task\n\nPlan a feature.\n\n"
        "## Selected Repository Context\n\n"
        "[SOURCE:docs/line-map.md#L1-L120]\n"
        "```markdown\n"
        f"{line_references}\n"
        "```\n"
    )
    report = {
        "task": "Plan a feature.",
        "selected_chunks": [
            {
                "id": "docs/line-map.md#L1-L120",
                "path": "docs/line-map.md",
                "start_line": 1,
                "end_line": 120,
                "tokens": 1200,
                "redaction_count": 0,
            }
        ],
        "change_surface": {
            "edit_targets": [],
            "test_targets": [],
            "config_targets": [],
            "supporting_targets": [],
        },
        "prompt_token_accounting": {
            "packed_prompt_tokens": TokenCounter().count(markdown),
            "full_scan_prompt_tokens": TokenCounter().count(markdown),
        },
    }

    mixed, updated = apply_multimodal_pack(
        markdown,
        report,
        TokenCounter(),
        mode="artifact",
        output_dir=tmp_path / "mixed",
    )
    plan = updated["multimodal"]
    block_plan = plan["block_plan"][0]

    assert mixed == markdown
    assert plan["should_image"] is False
    assert plan["decision_reason"] == "no_gist_candidate_blocks"
    assert plan["image_attachments"] == []
    assert block_plan["role"] == "exact_text"
    assert "line_addressed_reference" in block_plan["risk_labels"]
    assert not (tmp_path / "mixed" / "manifest.json").exists()


def test_apply_multimodal_pack_keeps_text_for_unsupported_model_profile(tmp_path) -> None:
    markdown = (
        "# AgenVantage Context Package\n\n"
        "## Instructions\n\nUse context.\n\n"
        "## Task\n\nExplain docs.\n\n"
        "## Selected Repository Context\n\n"
        "[SOURCE:docs/notes.md#L1-L5]\n"
        "```markdown\n"
        + " ".join("[]{}():;,./#L" for _ in range(3500))
        + "\n```\n"
    )
    report = {
        "task": "Explain docs.",
        "selected_chunks": [
            {
                "id": "docs/notes.md#L1-L5",
                "path": "docs/notes.md",
                "start_line": 1,
                "end_line": 5,
                "tokens": 9000,
                "redaction_count": 0,
            }
        ],
        "prompt_token_accounting": {
            "packed_prompt_tokens": TokenCounter().count(markdown),
            "full_scan_prompt_tokens": TokenCounter().count(markdown),
        },
    }

    mixed, updated = apply_multimodal_pack(
        markdown,
        report,
        TokenCounter(),
        mode="artifact",
        output_dir=tmp_path / "mixed",
        profile_id="anthropic_standard",
        model="gpt-4o-mini",
    )

    assert mixed == markdown
    assert updated["multimodal"]["decision_reason"] == "unsupported_model_for_profile"
    assert updated["multimodal"]["image_attachments"] == []
    assert not (tmp_path / "mixed" / "manifest.json").exists()


def test_verify_mixed_modality_manifest_reports_recoverable_hash_mismatch(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "recoverable.txt"
    source_path.write_text("changed recovered text\n", encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "recoverable_blocks": [
                    {
                        "id": "rec_bad",
                        "text_path": str(source_path),
                        "text_sha256": hashlib.sha256(
                            b"original recovered text\n"
                        ).hexdigest(),
                    }
                ],
                "image_attachments": [],
            }
        ),
        encoding="utf-8",
    )

    verification = verify_mixed_modality_manifest(manifest_path)

    assert verification["ok"] is False
    assert verification["error_count"] == 1
    assert "hash mismatch" in verification["errors"][0]


def test_verify_mixed_modality_manifest_reports_factsheet_hash_mismatch(
    tmp_path: Path,
) -> None:
    factsheet_path = tmp_path / "factsheet.txt"
    factsheet_path.write_text("changed factsheet\n", encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "recoverable_blocks": [],
                "image_attachments": [],
                "factsheets": [
                    {
                        "group_id": "rec_group",
                        "text_path": str(factsheet_path),
                        "text_sha256": hashlib.sha256(b"original factsheet\n").hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    verification = verify_mixed_modality_manifest(manifest_path)

    assert verification["ok"] is False
    assert verification["factsheet_count"] == 1
    assert verification["error_count"] == 1
    assert "Factsheet hash mismatch" in verification["errors"][0]


def test_verify_mixed_modality_manifest_reports_image_hash_mismatch(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "page.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nchanged")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "recoverable_blocks": [],
                "factsheets": [],
                "image_attachments": [
                    {
                        "path": str(image_path),
                        "sha256": hashlib.sha256(b"\x89PNG\r\n\x1a\noriginal").hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    verification = verify_mixed_modality_manifest(manifest_path)

    assert verification["ok"] is False
    assert verification["image_attachment_count"] == 1
    assert verification["error_count"] == 1
    assert "Image attachment hash mismatch" in verification["errors"][0]
