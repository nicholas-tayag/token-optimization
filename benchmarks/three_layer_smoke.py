from __future__ import annotations

import json
import tempfile
from pathlib import Path

from agenvantage.mcp_server import expand_context
from agenvantage.orchestrator import build_development_plan, load_development_partition
from agenvantage.presets import instructions_for_preset
from agenvantage.repo_context import build_context_package
from agenvantage.tokenizer import TokenCounter


def run_smoke() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="agenvantage-three-layer-") as directory:
        repo = Path(directory)
        (repo / "src").mkdir()
        (repo / "tests").mkdir()
        (repo / "src" / "service.py").write_text(
            "def create_order(value):\n    if value < 0:\n        raise ValueError('value')\n    return value\n",
            encoding="utf-8",
        )
        (repo / "tests" / "test_service.py").write_text(
            "from src.service import create_order\n\ndef test_rejects_negative():\n    assert create_order(1) == 1\n",
            encoding="utf-8",
        )
        markdown, report = build_context_package(
            repo,
            "Add create_order validation and regression tests",
            3000,
            TokenCounter(),
            instructions=instructions_for_preset("feature", "full"),
            workflow="feature",
        )
        report["implementation_discipline"] = "full"
        manifest = repo / "manifest.json"
        manifest.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        expansion = expand_context(
            {
                "manifest_path": str(manifest),
                "reason": "Need one more implementation neighbor",
                "expand_budget": 500,
            }
        )
        plan = build_development_plan(load_development_partition(), repo=repo)
        result = {
            "discipline_present": "IMPLEMENTATION LADDER" in markdown,
            "packed_tokens": report["prompt_token_accounting"]["packed_prompt_tokens"],
            "expanded_tokens": expansion["expanded_tokens"],
            "expansion_within_budget": expansion["expanded_tokens"] <= 500,
            "orchestration_wave_count": len(plan["waves"]),
        }
        result["passed"] = all(
            (
                result["discipline_present"],
                result["expansion_within_budget"],
                result["orchestration_wave_count"] > 0,
            )
        )
        return result


def main() -> None:
    result = run_smoke()
    print(json.dumps(result, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
