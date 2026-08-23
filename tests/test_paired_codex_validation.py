from __future__ import annotations

import json
from pathlib import Path

import pytest

from agenvantage.paired_codex_validation import (
    build_adhoc_paired_case,
    compute_treatment_delta,
    load_paired_codex_dataset,
    parse_codex_jsonl_usage,
    run_paired_codex_validation,
)


def test_parse_codex_jsonl_usage_sums_turn_completed_events() -> None:
    payload = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "abc"}),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 1000,
                        "cached_input_tokens": 800,
                        "output_tokens": 120,
                        "reasoning_output_tokens": 40,
                    },
                }
            ),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 500,
                        "cached_input_tokens": 400,
                        "output_tokens": 80,
                        "reasoning_output_tokens": 20,
                    },
                }
            ),
        ]
    )

    usage = parse_codex_jsonl_usage(payload)

    assert usage["turn_count"] == 2
    assert usage["input_tokens"] == 1500
    assert usage["cached_input_tokens"] == 1200
    assert usage["uncached_input_tokens"] == 300
    assert usage["output_tokens"] == 200
    assert usage["reasoning_output_tokens"] == 60


def test_parse_codex_jsonl_usage_ignores_invalid_token_values() -> None:
    payload = json.dumps(
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": "not-a-number",
                "cached_input_tokens": -5,
                "output_tokens": True,
                "reasoning_output_tokens": "7",
            },
        }
    )

    usage = parse_codex_jsonl_usage(payload)

    assert usage == {
        "turn_count": 1,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "uncached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 7,
    }


def test_parse_codex_jsonl_usage_ignores_fractional_token_counts() -> None:
    payload = json.dumps(
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 2.5, "cached_input_tokens": 3.0},
        }
    )

    usage = parse_codex_jsonl_usage(payload)

    assert usage["input_tokens"] == 0
    assert usage["cached_input_tokens"] == 3


def test_compute_treatment_delta_reports_percent_change() -> None:
    delta = compute_treatment_delta(
        {"input_tokens": 1000, "wall_time_seconds": 200},
        {"input_tokens": 600, "wall_time_seconds": 150},
    )

    assert delta["input_tokens"] == -40.0
    assert delta["wall_time_seconds"] == -25.0


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "dataset must be a JSON object"),
        ({"default_runner": []}, "default_runner must be a JSON object"),
        ({"cases": {}}, "cases must be a JSON array"),
        ({"cases": ["not-a-case"]}, "each paired Codex case must be a JSON object"),
    ],
)
def test_load_paired_codex_dataset_rejects_invalid_structures(
    tmp_path: Path, payload: object, message: str
) -> None:
    fixture = tmp_path / "cases.json"
    fixture.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_paired_codex_dataset(fixture)


def test_run_paired_codex_validation_local_only(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "def run():\n    return 1\n\ndef helper():\n    return run()\n",
        encoding="utf-8",
    )
    (repo / "tests").mkdir()
    (repo / "tests" / "test_app.py").write_text(
        "from app import run\n\ndef test_run():\n    assert run() == 1\n",
        encoding="utf-8",
    )
    fixture = tmp_path / "cases.json"
    fixture.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "default_runner": {"provider": "codex", "model": "gpt-5.4-mini"},
                "cases": [
                    {
                        "case_id": "tiny-feature",
                        "repository": "local",
                        "repo_path": "repo",
                        "commit": "local",
                        "feature": "Explain run() and add a helper test.",
                        "expected_edit_paths": ["app.py"],
                        "expected_test_paths": ["tests/test_app.py"],
                        "acceptance_command": [],
                        "repository_test_command": [],
                        "budget": 1200,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "artifacts"

    report = run_paired_codex_validation(
        fixture=fixture,
        repos_root=tmp_path,
        output_dir=output_dir,
        mode="local_only",
    )

    assert report["mode"] == "local_only"
    assert len(report["cases"]) == 1
    case = report["cases"][0]
    assert case["local_prompt"]["agenvantage_handoff_tokens"] > 0
    assert case["local_prompt"]["agenvantage_full_scan_counterfactual_tokens"] >= case[
        "local_prompt"
    ]["agenvantage_handoff_tokens"]
    assert (output_dir / "paired-codex-validation.json").is_file()
    assert (output_dir / "paired-codex-validation.md").is_file()
    assert (output_dir / "tiny-feature" / "treatment-handoff.md").is_file()


def test_build_adhoc_paired_case_uses_absolute_repo(tmp_path: Path) -> None:
    repo = tmp_path / "demo-repo"
    repo.mkdir()
    case = build_adhoc_paired_case(repo, "Explain the API")

    assert case.repo_path.is_absolute()
    assert case.feature == "Explain the API"
    assert case.case_id == "demo-repo-compare"


def test_compare_cli_on_any_repo(tmp_path: Path) -> None:
    import subprocess
    import sys

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agenvantage",
            "compare",
            "--repo",
            str(repo),
            "--task",
            "Explain main()",
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )

    assert "AgenVantage compare" in completed.stdout
    assert "First-request context:" in completed.stdout
    assert (repo / ".agenvantage" / "compare" / "repo-compare" / "treatment-handoff.md").is_file()


def test_codex_jsonl_to_provider_records_maps_trajectory_usage() -> None:
    from agenvantage.paired_codex_validation import codex_jsonl_to_provider_records

    payload = "\n".join(
        [
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 1000,
                        "cached_input_tokens": 800,
                        "output_tokens": 120,
                        "reasoning_output_tokens": 40,
                    },
                }
            )
        ]
    )
    records = codex_jsonl_to_provider_records(payload, trace_id="trace-1")
    assert len(records) == 1
    assert records[0]["input_tokens"] == 1000
    assert records[0]["reasoning_output_tokens"] == 40


def test_provider_import_accepts_codex_jsonl(tmp_path: Path) -> None:
    import subprocess
    import sys

    from agenvantage.observability import init_observability_store, record_pack_trace

    repo = tmp_path / "repo"
    repo.mkdir()
    db_path = repo / ".agenvantage" / "observability.db"
    init_observability_store(db_path)
    trace = record_pack_trace(
        db_path,
        markdown="# test\n",
        report={"task": "demo", "selected_chunks": []},
        repo_path=repo,
        workflow="test",
    )
    jsonl = tmp_path / "control.jsonl"
    jsonl.write_text(
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 500,
                    "cached_input_tokens": 400,
                    "output_tokens": 50,
                    "reasoning_output_tokens": 10,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agenvantage",
            "provider",
            "import",
            "--records",
            str(jsonl),
            "--trace-id",
            trace.trace_id,
            "--repo",
            str(repo),
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    summary = json.loads(completed.stdout)
    assert summary["imported_count"] == 1
    assert summary["total_input_tokens"] == 500


def test_observability_dashboard_shows_reasoning_tokens(tmp_path: Path) -> None:
    from agenvantage.observability import import_provider_usage_records, write_observability_dashboard

    db_path = tmp_path / ".agenvantage" / "observability.db"
    from agenvantage.observability import init_observability_store, record_pack_trace

    init_observability_store(db_path)
    trace = record_pack_trace(
        db_path,
        markdown="# test\n",
        report={"task": "demo", "selected_chunks": []},
        repo_path=tmp_path,
        workflow="test",
    )
    import_provider_usage_records(
        db_path,
        [
            {
                "trace_id": trace.trace_id,
                "provider": "codex",
                "policy_id": "trajectory",
                "input_tokens": 900,
                "cached_input_tokens": 700,
                "output_tokens": 100,
                "reasoning_output_tokens": 55,
                "source": "codex_jsonl",
            }
        ],
    )
    html = write_observability_dashboard(db_path, tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert "reasoning" in html
    assert "900" in html


def test_paired_codex_validation_cli_summary(tmp_path: Path) -> None:
    import subprocess
    import sys

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")
    fixture = tmp_path / "cases.json"
    fixture.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": "cli-case",
                        "repository": "local",
                        "repo_path": "repo",
                        "commit": "local",
                        "feature": "Explain main()",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "out"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agenvantage",
            "validate-paired-codex",
            "--fixture",
            str(fixture),
            "--repos-root",
            str(tmp_path),
            "--output-dir",
            str(output_dir),
            "--summary",
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    payload = json.loads(completed.stdout)
    assert payload["mode"] == "local_only"
    assert payload["cases"][0]["case_id"] == "cli-case"
