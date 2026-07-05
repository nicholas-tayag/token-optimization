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
        artifact_root=tmp_path / "images",
    )

    assert report["summary"]["case_count"] == 12
    assert report["summary"]["scope"] == (
        "local_pxpipe_inspired_artifact_pipeline_not_live_provider_usage"
    )
    assert "full_scan_tradeoffs" in report["summary"]
    assert "packed_tradeoffs" in report["summary"]
    assert "total_artifact_image_count" in report["summary"]
    assert "total_artifact_factsheets" in report["summary"]
    assert "artifact_manifest_verified_case_rate" in report["summary"]
    assert report["summary"]["artifact_manifest_verification_error_count"] == 0
    assert "artifact_mixed_estimated_tokens" in report["cases"][0]
    assert "artifact_manifest_verified" in report["cases"][0]
    assert "artifact_manifest_verification_status" in report["cases"][0]
    assert output_json.exists()
    assert output_md.exists()
    assert json.loads(output_json.read_text(encoding="utf-8"))["cases"]
