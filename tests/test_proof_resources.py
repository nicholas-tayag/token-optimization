from __future__ import annotations

import json
from pathlib import Path


def test_proof_resources_manifest_is_well_formed() -> None:
    path = Path(__file__).resolve().parents[1] / "examples" / "proof_resources.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["resource_pack_id"] == "agenvantage-proof-resources-2026-07-01"
    resources = payload["resources"]
    assert len(resources) >= 20

    ids = [item["id"] for item in resources]
    assert len(ids) == len(set(ids))

    claims = {item["claim"] for item in resources}
    assert claims == {
        "solved_agent_context_overload_end_to_end",
        "proved_real_api_cost_savings",
        "proved_latency_improvements_in_production",
        "proved_downstream_model_answer_quality_retention_across_broad_workloads",
    }
