from pathlib import Path

from agenvantage.prompt_optimizer import append_optimization_log, optimize_prompt
from agenvantage.tokenizer import TokenCounter


def test_optimizer_structures_coding_prompt_and_reports_local_savings() -> None:
    result = optimize_prompt(
        "Please actually fix the bug in the repo. Please fix the bug in the repo. Run tests.",
        mode="auto",
        counter=TokenCounter(),
    )

    assert result.mode == "codex"
    assert "please" not in result.optimized_prompt.lower()
    assert "fix the bug" in result.optimized_prompt.lower()
    assert result.original_tokens > 0
    assert result.optimized_tokens > 0
    assert result.optimized_tokens <= result.original_tokens
    assert result.estimated_savings_tokens >= 0


def test_optimizer_flags_missing_coding_validation() -> None:
    result = optimize_prompt("Refactor the authentication code in the repo", mode="codex")

    assert "validation expectation" in result.missing_info
    assert result.to_dict()["claim_boundary"].startswith("local tokenizer")


def test_optimizer_log_is_machine_readable(tmp_path: Path) -> None:
    result = optimize_prompt("Summarize this context for me", mode="general")
    path = tmp_path / ".agenvantage" / "prompt-optimizations.jsonl"

    append_optimization_log(result, path)

    assert path.is_file()
    assert '"mode": "general"' in path.read_text(encoding="utf-8")
