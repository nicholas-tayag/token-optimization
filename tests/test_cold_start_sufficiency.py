import json
from pathlib import Path

from benchmarks.cold_start_sufficiency import DEFAULT_FIXTURE, _oracle_recovery, run_cold_start_sufficiency


class _Chunk:
    def __init__(self, chunk_id: str, path: str, text: str, tokens: int = 10) -> None:
        self.chunk_id = chunk_id
        self.relative_path = path
        self.start_line = 1
        self.end_line = 20
        self.text = text
        self.addition_tokens = tokens


def _annotation(slot: str, valid: bool = True, path: str = "src/App.jsx") -> dict:
    return {"slot": slot, "path": path, "start_line": 1, "end_line": 20, "anchors": {slot}, "valid": valid}


def test_oracle_recovery_zero_annotations() -> None:
    assert _oracle_recovery([], []) == ([], [], [], 0)


def test_oracle_recovery_partial_and_invalid_annotations() -> None:
    covered, missing, ids, tokens = _oracle_recovery(
        [_annotation("render"), _annotation("state"), _annotation("invalid", valid=False)],
        [_Chunk("chunk-render", "src/App.jsx", "render")],
    )
    assert covered == ["render"]
    assert missing == ["state", "invalid"]
    assert ids == ["chunk-render"]
    assert tokens == 10


def test_oracle_recovery_deduplicates_shared_chunk() -> None:
    covered, missing, ids, tokens = _oracle_recovery(
        [_annotation("render"), _annotation("state")],
        [_Chunk("shared", "src/App.jsx", "render state", tokens=17)],
    )
    assert covered == ["render", "state"]
    assert missing == []
    assert ids == ["shared"]
    assert tokens == 17


def test_fixture_is_self_contained_and_annotated() -> None:
    payload = json.loads(DEFAULT_FIXTURE.read_text(encoding="utf-8"))
    case = payload["cases"][0]
    assert payload["version"] == 1
    assert {"src/App.jsx", "src/state/workouts.js", "src/styles.css", "src/App.test.jsx"} <= set(case["fixture"])
    assert set(case["evidence_slots"]) == {"render", "state", "style", "validation"}


def test_adaptive_pack_recovers_cold_start_evidence_from_legacy_cap(tmp_path: Path) -> None:
    result = run_cold_start_sufficiency(output_json=tmp_path / "report.json")
    case = result["cases"][0]
    assert result["summary"] == {
        "case_count": 2,
        "ready_case_count": 2,
        "cases_with_missing_evidence": 0,
        "legacy_ready_case_count": 1,
        "cases_improved": 1,
    }
    assert case["readiness"] is True
    assert case["oracle_coverage_percent"] == 100.0
    assert case["oracle_covered_evidence_slots"] == ["render", "state", "style", "validation"]
    assert case["missing_oracle_evidence_slots"] == []
    assert case["selected_coverage_percent"] == 100.0
    assert case["missing_evidence_slots"] == []
    assert case["fixture_invalid"] is False
    assert case["annotation_mismatch_count"] == 0
    assert 1800 < case["oracle_tokens"] <= 6000
    assert case["requested_tokens"] == 6000
    assert 1800 < case["effective_budget"] <= 6000
    assert case["full_scan_tokens"] >= 5400
    assert case["full_scan_tokens"] > case["packed_tokens"]
    assert case["selected_line_regions"]
    assert case["legacy"]["requested_tokens"] == 1800
    assert case["legacy"]["effective_budget"] == 1800
    assert case["legacy"]["readiness"] is False
    assert case["legacy"]["selected_coverage_percent"] < 100.0
    assert case["legacy"]["missing_evidence_slots"]
    assert any(
        "region_not_selected" in reason or "path_not_selected" in reason
        for reason in case["legacy"]["missing_slot_reasons"]
    )
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8")) == result


def test_graphify_cold_start_fixture_is_source_backed_and_bounded(
    tmp_path: Path,
) -> None:
    result = run_cold_start_sufficiency(output_json=tmp_path / "report.json")
    case = next(
        item for item in result["cases"] if item["case_id"] == "cross-file-upload-limit"
    )

    assert case["fixture_invalid"] is False
    assert case["graphify"]["readiness"] is True
    assert case["graphify"]["graph"]["used"] is True
    assert case["graphify"]["graph"]["selected_relations"] == ["calls", "tests"]
    assert case["graphify"]["packed_tokens"] <= case["requested_tokens"]
