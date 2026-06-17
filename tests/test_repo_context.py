from pathlib import Path
import subprocess

from agenvantage.repo_context import build_context_package, source_files
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
