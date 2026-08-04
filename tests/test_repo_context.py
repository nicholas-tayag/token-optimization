import json
from pathlib import Path
import subprocess

from agenvantage.repo_context import (
    CodeChunk,
    build_context_package,
    build_multi_repo_context_package,
    chunks_for_repo,
    rank_chunks,
    source_files,
    write_package_outputs,
)
from agenvantage.repo_index import build_repository_index
from agenvantage.tokenizer import TokenCounter


def create_sample_repo(root: Path) -> None:
    (root / "src").mkdir()
    (root / "src" / "rateLimiter.ts").write_text(
        "export function rateLimiter(redis) {\n"
        "  // fail open when Redis cannot be reached\n"
        "  return redis.consume('ratelimit');\n"
        "}\n",
        encoding="utf-8",
    )
    (root / "src" / "mapView.ts").write_text(
        "export function renderMap() { return 'map'; }\n",
        encoding="utf-8",
    )
    (root / ".env").write_text("API_SECRET=not-for-context\n", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "library.ts").write_text(
        "rateLimiter secret dependency noise\n", encoding="utf-8"
    )
    (root / "artifacts").mkdir()
    (root / "artifacts" / "feature-work-validation.json").write_text(
        '{"generated": "benchmark output should not become context"}\n',
        encoding="utf-8",
    )


def test_source_files_exclude_env_and_dependency_directories(tmp_path: Path) -> None:
    create_sample_repo(tmp_path)
    files = {path.relative_to(tmp_path).as_posix() for path in source_files(tmp_path)}
    assert "src/rateLimiter.ts" in files
    assert ".env" not in files
    assert "node_modules/library.ts" not in files
    assert "artifacts/feature-work-validation.json" not in files


def test_source_files_include_safe_env_examples(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SECRET=live\n", encoding="utf-8")
    (tmp_path / ".env.local").write_text("SECRET=local\n", encoding="utf-8")
    (tmp_path / ".env.example").write_text("API_BASE=https://example.test\n", encoding="utf-8")
    (tmp_path / ".env.sample").write_text("FEATURE_FLAG=false\n", encoding="utf-8")
    (tmp_path / ".env.template").write_text("TOKEN=\n", encoding="utf-8")

    files = {path.relative_to(tmp_path).as_posix() for path in source_files(tmp_path)}

    assert ".env" not in files
    assert ".env.local" not in files
    assert ".env.example" in files
    assert ".env.sample" in files
    assert ".env.template" in files


def test_source_files_include_shell_scripts(tmp_path: Path) -> None:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "deploy.sh").write_text(
        "#!/usr/bin/env bash\nnpm run build\n", encoding="utf-8"
    )

    files = {path.relative_to(tmp_path).as_posix() for path in source_files(tmp_path)}

    assert "scripts/deploy.sh" in files


def test_source_files_respect_include_globs(tmp_path: Path) -> None:
    create_sample_repo(tmp_path)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "deploy.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    files = {
        path.relative_to(tmp_path).as_posix()
        for path in source_files(tmp_path, include_globs=("scripts/*.sh",))
    }

    assert files == {"scripts/deploy.sh"}


def test_source_files_respect_exclude_globs(tmp_path: Path) -> None:
    create_sample_repo(tmp_path)

    files = {
        path.relative_to(tmp_path).as_posix()
        for path in source_files(tmp_path, exclude_globs=("src/mapView.ts",))
    }

    assert "src/rateLimiter.ts" in files
    assert "src/mapView.ts" not in files
def test_source_files_include_readme_and_container_build_variants(tmp_path: Path) -> None:
    (tmp_path / "README").write_text("Project overview.\n", encoding="utf-8")
    (tmp_path / "Dockerfile.prod").write_text("FROM node:20-alpine\n", encoding="utf-8")
    (tmp_path / "Containerfile.dev").write_text("FROM python:3.12-slim\n", encoding="utf-8")

    files = {path.relative_to(tmp_path).as_posix() for path in source_files(tmp_path)}

    assert "README" in files
    assert "Dockerfile.prod" in files
    assert "Containerfile.dev" in files


def test_source_files_include_common_dependency_manifests(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("httpx==0.28.0\n", encoding="utf-8")
    (tmp_path / "constraints.txt").write_text("urllib3<3\n", encoding="utf-8")
    (tmp_path / "Pipfile").write_text("[packages]\nhttpx='*'\n", encoding="utf-8")
    (tmp_path / "Pipfile.lock").write_text("{\"_meta\":{}}\n", encoding="utf-8")
    (tmp_path / "poetry.lock").write_text("[[package]]\nname='httpx'\n", encoding="utf-8")
    (tmp_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")

    files = {path.relative_to(tmp_path).as_posix() for path in source_files(tmp_path)}

    assert "requirements.txt" in files
    assert "constraints.txt" in files
    assert "Pipfile" in files
    assert "Pipfile.lock" in files
    assert "poetry.lock" in files
    assert "uv.lock" in files


def test_source_files_include_plain_config_suffixes(tmp_path: Path) -> None:
    (tmp_path / "gunicorn.conf").write_text("workers=3\n", encoding="utf-8")
    (tmp_path / "alembic.ini").write_text("[alembic]\nscript_location = migrations\n", encoding="utf-8")
    (tmp_path / "mypy.cfg").write_text("[mypy]\npython_version = 3.12\n", encoding="utf-8")
    (tmp_path / ".editorconfig").write_text("root = true\n[*]\ncharset = utf-8\n", encoding="utf-8")

    files = {path.relative_to(tmp_path).as_posix() for path in source_files(tmp_path)}

    assert "gunicorn.conf" in files
    assert "alembic.ini" in files
    assert "mypy.cfg" in files
    assert ".editorconfig" in files


def test_source_files_include_untracked_non_ignored_git_files(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    (tmp_path / ".gitignore").write_text("ignored.ts\n", encoding="utf-8")
    (tmp_path / "tracked.ts").write_text("export const tracked = true;\n", encoding="utf-8")
    (tmp_path / "draft.ts").write_text("export const draft = true;\n", encoding="utf-8")
    (tmp_path / "ignored.ts").write_text("export const ignored = true;\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", ".gitignore", "tracked.ts"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    files = {path.relative_to(tmp_path).as_posix() for path in source_files(tmp_path)}

    assert "tracked.ts" in files
    assert "draft.ts" in files
    assert "ignored.ts" not in files


def test_source_files_exclude_common_tool_caches(tmp_path: Path) -> None:
    (tmp_path / ".pytest_cache").mkdir()
    (tmp_path / ".pytest_cache" / "state.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".ruff_cache").mkdir()
    (tmp_path / ".ruff_cache" / "lint.py").write_text("print('cache')\n", encoding="utf-8")
    (tmp_path / ".mypy_cache").mkdir()
    (tmp_path / ".mypy_cache" / "types.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".tox").mkdir()
    (tmp_path / ".tox" / "pyproject.toml").write_text("[testenv]\n", encoding="utf-8")
    (tmp_path / ".nox").mkdir()
    (tmp_path / ".nox" / "run.py").write_text("print('nox')\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('real')\n", encoding="utf-8")

    files = {path.relative_to(tmp_path).as_posix() for path in source_files(tmp_path)}

    assert "src/app.py" in files
    assert ".pytest_cache/state.json" not in files
    assert ".ruff_cache/lint.py" not in files
    assert ".mypy_cache/types.json" not in files
    assert ".tox/pyproject.toml" not in files
    assert ".nox/run.py" not in files


def test_context_package_can_include_git_provenance(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    (tmp_path / "app.js").write_text(
        "export const uploadLimit = 10;\n", encoding="utf-8"
    )
    subprocess.run(
        ["git", "add", "app.js"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add upload limit"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    (tmp_path / "app.js").write_text(
        "export const uploadLimit = 25;\n", encoding="utf-8"
    )

    markdown, report = build_context_package(
        tmp_path,
        "Identify changed behavior around upload limit configuration",
        budget=700,
        counter=TokenCounter(),
        include_diff=True,
        include_log=True,
    )

    provenance = report["provenance"]
    assert provenance["enabled"] is True
    assert provenance["section_count"] >= 1
    assert provenance["selected_provenance_tokens"] > 0
    assert "## Repository Provenance" in markdown
    assert "Working tree changes" in markdown or "Add upload limit" in markdown


def test_repository_index_resolves_local_import_targets(tmp_path: Path) -> None:
    (tmp_path / "lib").mkdir()
    (tmp_path / "server.mjs").write_text(
        'import { buildAutofillPlan } from "./lib/form-autofill.mjs";\n',
        encoding="utf-8",
    )
    (tmp_path / "lib" / "form-autofill.mjs").write_text(
        "export function buildAutofillPlan() { return []; }\n",
        encoding="utf-8",
    )

    files = source_files(tmp_path)
    index_result = build_repository_index(tmp_path, files)

    assert (
        "lib/form-autofill.mjs"
        in index_result.entries["server.mjs"].local_import_paths
    )
    assert "server.mjs" in index_result.entries["lib/form-autofill.mjs"].imported_by_paths
    assert any(
        item.name == "buildAutofillPlan" and item.line_number == 1
        for item in index_result.entries["lib/form-autofill.mjs"].symbol_occurrences
    )


def test_context_package_graph_expands_to_imported_helpers(
    tmp_path: Path, monkeypatch
) -> None:
    cache_root = tmp_path.parent / "agenvantage-cache-graph"
    monkeypatch.setenv("AGENVANTAGE_INDEX_ROOT", str(cache_root))
    (tmp_path / "lib").mkdir(parents=True)
    (tmp_path / "server.mjs").write_text(
        'import { buildAutofillPlan } from "./lib/form-autofill.mjs";\n'
        'import { analyzeResume } from "./lib/resume-agent.mjs";\n'
        "export async function handleResumeUpload() {\n"
        "  return buildAutofillPlan(await analyzeResume());\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "app.js").write_text(
        "export async function submitResumeUpload() { return fetch('/api/resume'); }\n",
        encoding="utf-8",
    )
    (tmp_path / "lib" / "form-autofill.mjs").write_text(
        "export function buildAutofillPlan(profile) { return profile.fields ?? []; }\n",
        encoding="utf-8",
    )
    (tmp_path / "lib" / "resume-agent.mjs").write_text(
        "export async function analyzeResume() { return { fields: [] }; }\n",
        encoding="utf-8",
    )

    _, report = build_context_package(
        tmp_path,
        "Compare resume upload and autofill planning flow",
        budget=700,
        counter=TokenCounter(),
    )

    selected_paths = {chunk["path"] for chunk in report["selected_chunks"]}
    assert "server.mjs" in selected_paths
    assert "lib/form-autofill.mjs" in selected_paths
    assert "lib/resume-agent.mjs" in selected_paths


def test_feature_context_package_reports_change_surface_with_tests(
    tmp_path: Path, monkeypatch
) -> None:
    cache_root = tmp_path.parent / "agenvantage-cache-feature"
    monkeypatch.setenv("AGENVANTAGE_INDEX_ROOT", str(cache_root))
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "rateLimiter.ts").write_text(
        "export function configureRateLimiter(redis) {\n"
        "  return redis.consume('ratelimit');\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "rateLimiter.test.ts").write_text(
        "test('rate limiter fails open when redis is unavailable', () => {\n"
        "  expect(true).toBe(true);\n"
        "});\n",
        encoding="utf-8",
    )

    _, report = build_context_package(
        tmp_path,
        "Add feature tests for rate limiter Redis fail open behavior",
        budget=900,
        counter=TokenCounter(),
        workflow="feature",
    )

    change_surface = report["change_surface"]
    selected_paths = {chunk["path"] for chunk in report["selected_chunks"]}
    assert report["pack_workflow"] == "feature"
    assert "src/rateLimiter.ts" in {
        item["path"] for item in change_surface["edit_targets"]
    }
    assert "tests/rateLimiter.test.ts" in {
        item["path"] for item in change_surface["test_targets"]
    }
    assert "src/rateLimiter.ts" in selected_paths
    assert "tests/rateLimiter.test.ts" in selected_paths


def test_feature_cli_pack_reserves_repository_import_guard(tmp_path: Path, monkeypatch) -> None:
    cache_root = tmp_path.parent / "agenvantage-cache-cli-guard"
    monkeypatch.setenv("AGENVANTAGE_INDEX_ROOT", str(cache_root))
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\naddopts = '-q'\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "cli.py").write_text(
        "def help_json():\n    return {'options': []}\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_cli.py").write_text(
        "def test_help_json_shape():\n    assert {'options': []}\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_imports.py").write_text(
        "import sys\n\n"
        "def test_help_json_uses_lightweight_imports():\n"
        "    assert 'click' not in sys.modules\n",
        encoding="utf-8",
    )

    _, report = build_context_package(
        tmp_path,
        "Add --help-json to the CLI without eager imports",
        budget=4_000,
        counter=TokenCounter(),
        workflow="feature",
    )

    guard = report["change_surface"]["guard_test_targets"]
    validation = report["validation_requirements"]
    assert len(guard) == 1
    assert guard[0]["path"] == "tests/test_imports.py"
    assert {"test_imports", "sys_modules", "lightweight_imports"} <= set(
        guard[0]["signals"]
    )
    assert validation["full_suite_required"] is True
    assert validation["full_suite_command"] == "pytest"
    assert validation["guard_test"]["selected"] is True
    assert report["validation"] == validation
    selected_paths = {chunk["path"] for chunk in report["selected_chunks"]}
    assert {"src/cli.py", "tests/test_cli.py", "tests/test_imports.py"} <= selected_paths
    assert any(
        chunk["path"] == "tests/test_imports.py"
        and "sys.modules" in chunk["text"]
        for chunk in report["selected_chunks"]
    )
    assert report["selected_context_tokens"] <= report["effective_budget"]

    manifest_path = tmp_path / "manifest.json"
    write_package_outputs("", report, None, manifest_path)
    serialized = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert serialized["validation_requirements"] == {
        "full_suite_required": True,
        "full_suite_command": "pytest",
        "ready": True,
        "missing_mandatory_evidence": [],
        "required_minimum_budget": validation["required_minimum_budget"],
        "guard_test": {
            "path": "tests/test_imports.py",
            "chunk_id": validation["guard_test"]["chunk_id"],
            "selected": True,
        },
    }


def test_feature_pack_fails_closed_when_mandatory_guard_cannot_fit(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv(
        "AGENVANTAGE_INDEX_ROOT", str(tmp_path.parent / "agenvantage-cache-tight-guard")
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\naddopts = '-q'\n", encoding="utf-8"
    )
    (tmp_path / "src" / "cli.py").write_text(
        "def help_json():\n    return {'options': []}\n" * 80,
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_cli.py").write_text(
        "def test_help_json_shape():\n    assert {'options': []}\n" * 80,
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_imports.py").write_text(
        "import sys\n\n"
        + (
            "def test_help_json_uses_lightweight_imports():\n"
            "    assert 'click' not in sys.modules\n"
        )
        * 80,
        encoding="utf-8",
    )

    _, report = build_context_package(
        tmp_path,
        "Add --help-json to the CLI without eager imports",
        budget=350,
        counter=TokenCounter(),
        workflow="feature",
    )

    validation = report["validation_requirements"]
    assert report["selected_context_tokens"] <= report["effective_budget"]
    assert validation["full_suite_required"] is True
    assert validation["ready"] is False
    assert validation["missing_mandatory_evidence"]
    assert validation["required_minimum_budget"] > report["budget"]
    assert report["handoff_ready"] is False
    assert report["required_minimum_budget"] == validation["required_minimum_budget"]
    assert report["context_sufficiency"]["status"] == "insufficient_budget"
    assert report["context_sufficiency"]["missing_mandatory_evidence"]


def test_context_package_builds_and_reuses_repository_index(
    tmp_path: Path, monkeypatch
) -> None:
    create_sample_repo(tmp_path)
    cache_root = tmp_path.parent / "agenvantage-cache"
    monkeypatch.setenv("AGENVANTAGE_INDEX_ROOT", str(cache_root))

    _, first_report = build_context_package(
        tmp_path,
        "Explain rate limiter Redis fail open behavior",
        budget=220,
        counter=TokenCounter(),
    )
    first_index = first_report["repos"][0]["index"]
    assert Path(first_index["cache_path"]).is_file()
    assert first_index["rebuilt_files"] == first_index["indexed_files"]
    assert first_index["reused_files"] == 0
    assert first_report["chunk_cache"]["misses"] == first_index["indexed_files"]
    assert first_report["chunk_cache"]["writes"] == first_index["indexed_files"]

    _, second_report = build_context_package(
        tmp_path,
        "Explain rate limiter Redis fail open behavior",
        budget=220,
        counter=TokenCounter(),
    )
    second_index = second_report["repos"][0]["index"]
    assert second_index["reused_files"] == second_index["indexed_files"]
    assert second_index["rebuilt_files"] == 0
    assert second_report["chunk_cache"]["hits"] == second_index["indexed_files"]
    assert second_report["chunk_cache"]["misses"] == 0


def test_rank_chunks_uses_file_level_symbol_metadata() -> None:
    task = "Explain upload limit configuration"
    generic_text = "const maxBytes = 10_000;\nreturn maxBytes;\n"
    chunks = (
        CodeChunk(
            chunk_id="src/runtime.ts#L1-L2",
            relative_path="src/runtime.ts",
            display_path="src/runtime.ts",
            repo_label="repo",
            repo_path="/tmp/repo",
            start_line=1,
            end_line=2,
            text=generic_text,
            tokens=20,
            file_symbols=("configureUploadLimit",),
            file_imports=(),
        ),
        CodeChunk(
            chunk_id="src/noise.ts#L1-L2",
            relative_path="src/noise.ts",
            display_path="src/noise.ts",
            repo_label="repo",
            repo_path="/tmp/repo",
            start_line=1,
            end_line=2,
            text="const notes = 'configuration';\n",
            tokens=18,
            file_symbols=(),
            file_imports=(),
        ),
    )

    ranked = rank_chunks(chunks, task)

    assert ranked[0].chunk_id == "src/runtime.ts#L1-L2"
    assert "upload" in ranked[0].matched_terms
    assert "limit" in ranked[0].matched_terms


def test_rank_chunks_uses_chunk_local_symbol_metadata() -> None:
    task = "Compare resume upload autofill planning"
    chunks = (
        CodeChunk(
            chunk_id="lib/form-autofill.mjs#L1-L20",
            relative_path="lib/form-autofill.mjs",
            display_path="lib/form-autofill.mjs",
            repo_label="repo",
            repo_path="/tmp/repo",
            start_line=1,
            end_line=20,
            text="const SAFE = true;\n",
            tokens=18,
            chunk_symbols=("buildAutofillPlan",),
            file_symbols=("classifyFormField", "buildAutofillPlan"),
            file_imports=(),
        ),
        CodeChunk(
            chunk_id="lib/form-autofill.mjs#L21-L40",
            relative_path="lib/form-autofill.mjs",
            display_path="lib/form-autofill.mjs",
            repo_label="repo",
            repo_path="/tmp/repo",
            start_line=21,
            end_line=40,
            text="const SAFE = true;\n",
            tokens=18,
            chunk_symbols=(),
            file_symbols=("classifyFormField", "buildAutofillPlan"),
            file_imports=(),
        ),
    )

    ranked = rank_chunks(chunks, task)

    assert ranked[0].chunk_id == "lib/form-autofill.mjs#L1-L20"
    assert "autofill" in ranked[0].matched_terms


def test_rank_chunks_preserves_repo_command_anchor_for_stop_word() -> None:
    shared = {
        "relative_path": "graphify/cli.py",
        "display_path": "graphify/cli.py",
        "repo_label": "graphify",
        "repo_path": "/tmp/graphify",
        "tokens": 20,
        "file_symbols": ("dispatch_command", "explain"),
    }
    chunks = (
        CodeChunk(
            chunk_id="graphify/cli.py#L1-L20",
            start_line=1,
            end_line=20,
            text="def dispatch_command():\n    return build_graph()\n",
            **shared,
        ),
        CodeChunk(
            chunk_id="graphify/cli.py#L21-L40",
            start_line=21,
            end_line=40,
            text='elif command == "explain":\n    print("explain node")\n',
            **shared,
        ),
    )

    ranked = rank_chunks(chunks, "Add graphify explain confidence filtering")

    assert ranked[0].chunk_id == "graphify/cli.py#L21-L40"


def test_rank_chunks_boosts_command_anchor_in_test_path() -> None:
    chunks = tuple(
        CodeChunk(
            chunk_id=f"{path}#L1-L10",
            relative_path=path,
            display_path=path,
            repo_label="graphify",
            repo_path="/tmp/graphify",
            start_line=1,
            end_line=10,
            text=text,
            tokens=20,
        )
        for path, text in (
            ("tests/test_confidence.py", "def test_confidence_score(): pass\n"),
            ("tests/test_explain_cli.py", "def test_default_output(): pass\n"),
        )
    )

    ranked = rank_chunks(chunks, "Add graphify explain --confidence regression tests")

    assert ranked[0].relative_path == "tests/test_explain_cli.py"


def test_chunks_for_repo_carries_nearby_symbol_anchors_across_large_function_bodies(
    tmp_path: Path,
) -> None:
    content = (
        "export function handleUpload(file) {\n"
        + "".join(f"  const step{index} = file && {index};\n" for index in range(95))
        + "  const cleanStatus = 'complete';\n"
        + "  return cleanStatus;\n"
        + "}\n"
    )
    (tmp_path / "app.js").write_text(content, encoding="utf-8")

    files = source_files(tmp_path)
    index_result = build_repository_index(tmp_path, files)
    chunks = chunks_for_repo(tmp_path, TokenCounter(), files=files, file_index=index_result.entries)

    later_chunk = next(chunk for chunk in chunks if chunk.start_line >= 85)

    assert "handleUpload" in later_chunk.chunk_symbols


def test_chunks_for_repo_reuses_persistent_redacted_chunk_cache(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AGENVANTAGE_INDEX_ROOT", str(tmp_path / "cache"))
    source = tmp_path / "app.py"
    source.write_text("API_KEY = 'secret-value'\ndef run():\n    return API_KEY\n", encoding="utf-8")
    files = (source,)
    index_result = build_repository_index(tmp_path, files)
    cold_stats: dict[str, object] = {}
    cold = chunks_for_repo(
        tmp_path,
        TokenCounter(),
        files=files,
        file_index=index_result.entries,
        chunk_cache_stats=cold_stats,
    )

    original_read_text = Path.read_text

    def fail_source_read(path: Path, *args, **kwargs):
        if path == source:
            raise AssertionError("warm chunk cache should not reopen source")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_source_read)
    warm_stats: dict[str, object] = {}
    warm = chunks_for_repo(
        tmp_path,
        TokenCounter(),
        files=files,
        file_index=index_result.entries,
        chunk_cache_stats=warm_stats,
    )

    assert warm == cold
    assert cold_stats == {"hits": 0, "misses": 1, "writes": 1, "enabled": True}
    assert warm_stats == {"hits": 1, "misses": 0, "writes": 0, "enabled": True}
    assert "secret-value" not in warm[0].text


def test_context_package_selects_task_relevant_source(tmp_path: Path) -> None:
    create_sample_repo(tmp_path)
    markdown, report = build_context_package(
        tmp_path,
        "Explain rate limiter Redis fail open behavior",
        budget=220,
        counter=TokenCounter(),
    )
    selected = {chunk["path"] for chunk in report["selected_chunks"]}
    assert "src/rateLimiter.ts" in selected
    assert "src/mapView.ts" not in selected
    assert "rateLimiter.ts" in markdown
    assert report["selected_context_tokens"] <= report["budget"]
    assert report["local_tokens_omitted_vs_candidate_context"] > 0
    prompt_accounting = report["prompt_token_accounting"]
    assert prompt_accounting["original_user_prompt_tokens"] > 0
    assert prompt_accounting["full_scan_prompt_tokens"] == report["candidate_context_tokens"]
    assert prompt_accounting["packed_prompt_tokens"] == report["selected_context_tokens"]
    assert prompt_accounting["prompt_tokens_saved_vs_full_scan"] == report[
        "local_tokens_omitted_vs_candidate_context"
    ]
    assert "redis" in report["covered_query_terms"]


def test_full_scan_token_accounting_matches_rendered_prompt(tmp_path: Path) -> None:
    create_sample_repo(tmp_path)
    counter = TokenCounter()

    _, report = build_context_package(
        tmp_path,
        "Explain rate limiter Redis fail open behavior",
        budget=220,
        counter=counter,
        include_full_scan_prompt=True,
    )

    assert (
        counter.count(report["full_scan_prompt_markdown"])
        == report["prompt_token_accounting"]["full_scan_prompt_tokens"]
    )


def test_context_package_redacts_secret_values_before_rendering(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    raw_secret = "sk-prod1234567890abcdef"
    raw_bearer = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    (tmp_path / "src" / "payment.ts").write_text(
        "export const paymentConfig = {\n"
        f"  OPENAI_API_KEY: \"{raw_secret}\",\n"
        f"  Authorization: \"Bearer {raw_bearer}\",\n"
        "  endpoint: \"https://api.example.test/payments\"\n"
        "};\n",
        encoding="utf-8",
    )

    markdown, report = build_context_package(
        tmp_path,
        "Explain payment config api key handling",
        budget=700,
        counter=TokenCounter(),
        include_full_scan_prompt=True,
    )

    selected_chunk = report["selected_chunks"][0]
    assert raw_secret not in markdown
    assert raw_bearer not in markdown
    assert raw_secret not in report["full_scan_prompt_markdown"]
    assert raw_bearer not in report["full_scan_prompt_markdown"]
    assert "OPENAI_API_KEY" in markdown
    assert "[REDACTED_OPENAI_KEY]" in markdown
    assert "Bearer [REDACTED_BEARER_TOKEN]" in markdown
    assert selected_chunk["redaction_count"] == 2
    assert selected_chunk["redaction_types"] == ["bearer_token", "openai_api_key"]
    assert report["safety"]["selected_secret_redaction_count"] == 2
    assert report["safety"]["candidate_secret_redaction_count"] == 2


def test_context_package_excludes_weak_single_term_noise(tmp_path: Path) -> None:
    create_sample_repo(tmp_path)
    (tmp_path / "src" / "dummy.test.ts").write_text(
        "test('tests exist', () => expect(true));\n", encoding="utf-8"
    )
    _, report = build_context_package(
        tmp_path,
        "Explain Redis rate limiter tests",
        budget=500,
        counter=TokenCounter(),
    )
    selected_paths = {chunk["path"] for chunk in report["selected_chunks"]}
    assert "src/dummy.test.ts" not in selected_paths


def test_context_package_prefers_complementary_files_before_repeated_chunks(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    repeated_test = "\n".join(
        f"test('rate limiter redis retry headers {line}', () => expect(true));"
        for line in range(90)
    )
    (tmp_path / "src" / "rateLimiter.test.ts").write_text(repeated_test, encoding="utf-8")
    (tmp_path / "src" / "rateLimiter.ts").write_text(
        "export const rateLimiter = async () => {\n"
        "  // Redis failure must fail open for requests.\n"
        "  return 'retry headers';\n"
        "};\n",
        encoding="utf-8",
    )
    _, report = build_context_package(
        tmp_path,
        "Explain Redis rate limiter fail open retry headers tests",
        budget=1300,
        counter=TokenCounter(),
    )
    selected_paths = {chunk["path"] for chunk in report["selected_chunks"]}
    assert "src/rateLimiter.test.ts" in selected_paths
    assert "src/rateLimiter.ts" in selected_paths


def test_context_package_matches_natural_language_to_code_variants(tmp_path: Path) -> None:
    (tmp_path / "limiter.ts").write_text(
        "const RATE_LIMIT_TTL_SECONDS = 60;\n"
        "redis.expire(key, RATE_LIMIT_TTL_SECONDS);\n",
        encoding="utf-8",
    )
    _, report = build_context_package(
        tmp_path,
        "Explain rate limit expiration cleanup",
        budget=300,
        counter=TokenCounter(),
    )
    assert report["selected_chunks"][0]["path"] == "limiter.ts"


def test_context_package_matches_size_limit_language_to_byte_caps(tmp_path: Path) -> None:
    (tmp_path / "upload.js").write_text(
        "const defaultMaxUploadBytes = 750 * 1024;\n"
        "const maxUploadBytes = defaultMaxUploadBytes;\n",
        encoding="utf-8",
    )

    _, report = build_context_package(
        tmp_path,
        "Explain configurable upload size limits",
        budget=260,
        counter=TokenCounter(),
    )

    assert report["selected_chunks"][0]["path"] == "upload.js"


def test_context_package_selects_shell_scripts_for_ci_tasks(tmp_path: Path) -> None:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "deploy.sh").write_text(
        "#!/usr/bin/env bash\nnpm test\nnpm run release\n", encoding="utf-8"
    )
    (tmp_path / "notes.md").write_text("General project notes.\n", encoding="utf-8")

    _, report = build_context_package(
        tmp_path,
        "Explain the deploy script and CI release steps",
        budget=280,
        counter=TokenCounter(),
    )

    assert report["selected_chunks"][0]["path"] == "scripts/deploy.sh"


def test_context_package_records_active_path_filters(tmp_path: Path) -> None:
    create_sample_repo(tmp_path)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "deploy.sh").write_text(
        "#!/usr/bin/env bash\nnpm run build\n", encoding="utf-8"
    )

    _, report = build_context_package(
        tmp_path,
        "Explain deploy script",
        budget=260,
        counter=TokenCounter(),
        include_globs=("scripts/*.sh",),
        exclude_globs=("src/*",),
    )

    assert report["path_filters"] == {
        "include_globs": ["scripts/*.sh"],
        "exclude_globs": ["src/*"],
    }
    assert report["selected_chunks"][0]["path"] == "scripts/deploy.sh"


def test_context_package_matches_plural_path_terms_for_concise_tasks(tmp_path: Path) -> None:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "deploy.sh").write_text(
        "#!/usr/bin/env bash\nnpm run release\n", encoding="utf-8"
    )

    _, report = build_context_package(
        tmp_path,
        "Explain deploy script",
        budget=260,
        counter=TokenCounter(),
    )

    assert report["selected_chunks"][0]["path"] == "scripts/deploy.sh"
    assert "script" in report["covered_query_terms"]


def test_context_package_retains_short_registration_chunk(tmp_path: Path) -> None:
    (tmp_path / "index.ts").write_text(
        "if (SERVER_ENV.RATE_LIMIT_ENABLED) app.use('*', rateLimiter);\n",
        encoding="utf-8",
    )
    _, report = build_context_package(
        tmp_path,
        "Find where rate limiting is enabled",
        budget=250,
        counter=TokenCounter(),
    )
    assert report["selected_chunks"][0]["path"] == "index.ts"


def test_multi_repo_context_package_selects_relevant_chunks_across_repositories(
    tmp_path: Path,
) -> None:
    api_repo = tmp_path / "api-service"
    ops_repo = tmp_path / "ops-service"
    ui_repo = tmp_path / "ui-service"
    (api_repo / "src").mkdir(parents=True)
    (ops_repo / "scripts").mkdir(parents=True)
    (ui_repo / "src").mkdir(parents=True)

    (api_repo / "src" / "rateLimiter.ts").write_text(
        "export async function rateLimiter(redis) {\n"
        "  // fail open when Redis is unavailable\n"
        "  return redis.consume('requests');\n"
        "}\n",
        encoding="utf-8",
    )
    (ops_repo / "scripts" / "deploy.sh").write_text(
        "#!/usr/bin/env bash\n"
        "kubectl rollout status deploy/api\n"
        "kubectl rollout undo deploy/api\n",
        encoding="utf-8",
    )
    (ui_repo / "src" / "dashboard.ts").write_text(
        "export function renderDashboard() { return 'ok'; }\n",
        encoding="utf-8",
    )

    markdown, report = build_multi_repo_context_package(
        [api_repo, ops_repo, ui_repo],
        "Explain the Redis rate limiter fail open behavior and deploy rollback flow",
        budget=420,
        counter=TokenCounter(),
    )

    selected_paths = {chunk["path"] for chunk in report["selected_chunks"]}
    assert "api-service/src/rateLimiter.ts" in selected_paths
    assert "ops-service/scripts/deploy.sh" in selected_paths
    assert report["repo_count"] == 3
    assert report["selected_repo_labels"] == ["api-service", "ops-service"]
    assert "api-service/src/rateLimiter.ts" in markdown
    assert "ops-service/scripts/deploy.sh" in markdown


def test_multi_repo_context_package_disambiguates_same_relative_paths(tmp_path: Path) -> None:
    frontend_repo = tmp_path / "frontend"
    backend_repo = tmp_path / "backend"
    (frontend_repo / "src").mkdir(parents=True)
    (backend_repo / "src").mkdir(parents=True)

    (frontend_repo / "src" / "index.ts").write_text(
        "export const signupFlow = () => 'signup';\n", encoding="utf-8"
    )
    (backend_repo / "src" / "index.ts").write_text(
        "export const webhookRetry = () => 'retry';\n", encoding="utf-8"
    )

    _, report = build_multi_repo_context_package(
        [frontend_repo, backend_repo],
        "Explain the signup flow and webhook retry flow",
        budget=320,
        counter=TokenCounter(),
    )

    selected_paths = {chunk["path"] for chunk in report["selected_chunks"]}
    assert "frontend/src/index.ts" in selected_paths
    assert "backend/src/index.ts" in selected_paths


def test_context_package_records_line_pruning_in_manifest(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "rateLimiter.ts").write_text(
        "\n".join(
            [
                "export function rateLimiter(redis) {",
                "  // fail open when Redis cannot be reached",
                "  return redis.consume('ratelimit');",
                "}",
                "",
                "const fillerOne = 1;",
                "const fillerTwo = 2;",
                "# unrelated noise line alpha",
                "# unrelated noise line beta",
                "# unrelated noise line gamma",
                "# unrelated noise line delta",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    markdown, report = build_context_package(
        tmp_path,
        "Explain rate limiter Redis fail open behavior",
        budget=400,
        counter=TokenCounter(),
    )

    pruning = report["line_pruning"]
    assert pruning["enabled"] is True
    assert pruning["goal_hint_count"] > 0
    assert pruning["total_dropped_lines"] >= 1
    assert pruning["selected_block_tokens_saved"] >= 0
    assert "unrelated noise line delta" not in markdown
    assert "rateLimiter" in markdown
