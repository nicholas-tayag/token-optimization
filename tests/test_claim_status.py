from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from agenvantage.provider_validation import (
    PricingSnapshot,
    load_provider_validation_dataset,
    summarize_provider_validation_records,
)


def test_claim_status_script_reports_resume_claim_boundaries(tmp_path: Path) -> None:
    output_json = tmp_path / "claim-status.json"
    output_md = tmp_path / "claim-status.md"

    completed = subprocess.run(
        [
            sys.executable,
            "benchmarks/claim_status.py",
            "--output-json",
            str(output_json),
            "--output-md",
            str(output_md),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "JSON report written to" in completed.stdout
    report = json.loads(output_json.read_text(encoding="utf-8"))
    markdown = output_md.read_text(encoding="utf-8")

    assert report["narrow_supported_claim"]["supported"] is True
    assert (
        report["resume_claims"]["proved_latency_improvements_in_production"]["supported"]
        is False
    )
    assert "AgenVantage Claim Status" in markdown
    assert "solved_agent_context_overload_end_to_end" in markdown


def test_claim_status_uses_saved_provider_dataset_requirements(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    fixture = repo_root / "examples" / "provider_validation_cases.json"
    dataset = load_provider_validation_dataset(fixture)
    pricing = PricingSnapshot(
        provider="openai",
        model="gpt-test",
        captured_at="2026-07-01",
        source_url="https://developers.openai.com/api/docs/pricing",
        input_price_per_million=1.0,
        cached_input_price_per_million=0.1,
        output_price_per_million=2.0,
    )
    perfect_grade = {
        "correctness_pass": True,
        "safety_pass": True,
        "grounded_citation_pass": True,
        "overall_pass": True,
        "score": 1.0,
    }
    records = []
    for case in dataset.cases:
        records.append(
            {
                "policy_id": "full_unaligned",
                "case_id": case.case_id,
                "failure_type": case.failure_type,
                "latency_ms": 950.0,
                "input_tokens": 2200,
                "cached_input_tokens": 0,
                "output_tokens": 220,
                "request_cost_usd": 0.00264,
                "grade": perfect_grade,
            }
        )
        records.append(
            {
                "policy_id": "budgeted_cache_aligned",
                "case_id": case.case_id,
                "failure_type": case.failure_type,
                "latency_ms": 730.0,
                "input_tokens": 1700,
                "cached_input_tokens": 1200,
                "output_tokens": 220,
                "request_cost_usd": 0.00136,
                "grade": perfect_grade,
            }
        )

    provider_report = summarize_provider_validation_records(records, dataset=dataset, pricing=pricing)
    provider_path = tmp_path / "provider-validation.json"
    provider_path.write_text(json.dumps(provider_report, indent=2) + "\n", encoding="utf-8")

    output_json = tmp_path / "claim-status.json"
    output_md = tmp_path / "claim-status.md"
    subprocess.run(
        [
            sys.executable,
            "benchmarks/claim_status.py",
            "--provider-json",
            str(provider_path),
            "--output-json",
            str(output_json),
            "--output-md",
            str(output_md),
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )

    report = json.loads(output_json.read_text(encoding="utf-8"))
    assert report["resume_claims"]["proved_real_api_cost_savings"]["supported"] is True
    assert (
        report["resume_claims"]["proved_downstream_model_answer_quality_retention_across_broad_workloads"]["supported"]
        is True
    )
    assert report["resume_claims"]["proved_latency_improvements_in_production"]["supported"] is False
