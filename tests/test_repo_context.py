from pathlib import Path
import subprocess

from agenvantage.repo_context import (
    CodeChunk,
    build_context_package,
    build_multi_repo_context_package,
    rank_chunks,
    source_files,
)
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


def test_source_files_exclude_env_and_dependency_directories(tmp_path: Path) -> None:
    create_sample_repo(tmp_path)
    files = {path.relative_to(tmp_path).as_posix() for path in source_files(tmp_path)}
    assert "src/rateLimiter.ts" in files
    assert ".env" not in files
    assert "node_modules/library.ts" not in files


def test_source_files_include_shell_scripts(tmp_path: Path) -> None:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "deploy.sh").write_text(
        "#!/usr/bin/env bash\nnpm run build\n", encoding="utf-8"
    )

    files = {path.relative_to(tmp_path).as_posix() for path in source_files(tmp_path)}

    assert "scripts/deploy.sh" in files


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

    _, second_report = build_context_package(
        tmp_path,
        "Explain rate limiter Redis fail open behavior",
        budget=220,
        counter=TokenCounter(),
    )
    second_index = second_report["repos"][0]["index"]
    assert second_index["reused_files"] == second_index["indexed_files"]
    assert second_index["rebuilt_files"] == 0


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
    assert "redis" in report["covered_query_terms"]


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
