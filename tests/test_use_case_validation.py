from pathlib import Path

from benchmarks.use_case_validation import (
    RequiredObservation,
    ValidationCase,
    _evaluate_required_observations,
)


def test_required_observation_matching_aggregates_selected_chunks_by_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "server.js"
    target.write_text(
        "const maxUploadBytes = 1;\n"
        "const defaultMaxUploadBytes = 2;\n",
        encoding="utf-8",
    )

    case = ValidationCase(
        case_id="case",
        category="test",
        task="Explain upload limits",
        repos=(repo,),
        budget=100,
        expected_paths=("server.js",),
        description="test",
        required_observations=(
            RequiredObservation(
                label="upload_limit_config",
                any_of=(("max upload bytes", "default max upload bytes"),),
            ),
        ),
    )
    report = {
        "selected_chunks": [
            {
                "id": "server.js#L1-L1",
                "path": "server.js",
                "relative_path": "server.js",
                "repo_label": "repo",
                "repo_path": str(repo),
                "start_line": 1,
                "end_line": 1,
            },
            {
                "id": "server.js#L2-L2",
                "path": "server.js",
                "relative_path": "server.js",
                "repo_label": "repo",
                "repo_path": str(repo),
                "start_line": 2,
                "end_line": 2,
            },
        ]
    }

    observed, missing, citations = _evaluate_required_observations(case, report)

    assert observed == ["upload_limit_config"]
    assert missing == []
    assert citations == {"upload_limit_config": ["server.js"]}


def test_required_observation_matching_reports_missing_items(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "app.js"
    target.write_text("export const health = true;\n", encoding="utf-8")

    case = ValidationCase(
        case_id="case",
        category="test",
        task="Explain upload limits",
        repos=(repo,),
        budget=100,
        expected_paths=("app.js",),
        description="test",
        required_observations=(
            RequiredObservation(
                label="upload_limit_config",
                any_of=(("max upload bytes", "default max upload bytes"),),
            ),
        ),
    )
    report = {
        "selected_chunks": [
            {
                "id": "app.js#L1-L1",
                "path": "app.js",
                "relative_path": "app.js",
                "repo_label": "repo",
                "repo_path": str(repo),
                "start_line": 1,
                "end_line": 1,
            }
        ]
    }

    observed, missing, citations = _evaluate_required_observations(case, report)

    assert observed == []
    assert missing == ["upload_limit_config"]
    assert citations == {}
