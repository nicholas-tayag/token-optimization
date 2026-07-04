import json
from pathlib import Path

from benchmarks.modality_tradeoff_validation import run_modality_tradeoff_validation


def test_modality_tradeoff_validation_writes_report(tmp_path: Path) -> None:
    fixture = Path("examples/feature_work_validation_cases.json")
    repos_root = Path("..")
    if not all((repos_root / name).is_dir() for name in ("mesh", "signalfoundry")):
        return

    output_json = tmp_path / "modality.json"
    output_md = tmp_path / "modality.md"
    report = run_modality_tradeoff_validation(
        fixture=fixture,
        repos_root=repos_root,
        output_json=output_json,
        output_md=output_md,
    )

    assert report["summary"]["case_count"] == 12
    assert report["summary"]["scope"] == (
        "theoretical_pxpipe_inspired_modality_gate_not_live_image_transport"
    )
    assert "full_scan_tradeoffs" in report["summary"]
    assert "packed_tradeoffs" in report["summary"]
    assert output_json.exists()
    assert output_md.exists()
    assert json.loads(output_json.read_text(encoding="utf-8"))["cases"]
