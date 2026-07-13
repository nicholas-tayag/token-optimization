from __future__ import annotations

import json
import os
from pathlib import Path

from agenvantage.repo_index import build_repository_index


def _materialize_react_repo(root: Path) -> list[Path]:
    files = {
        "package.json": '{"scripts":{"dev":"vite"},"dependencies":{"react":"latest"}}\n',
        "vite.config.ts": "export default {};\n",
        "README.md": "# Example React app\n",
        "src/main.tsx": (
            'import App from "./App";\n'
            "import './index.css';\n"
            "import { createRoot } from 'react-dom/client';\n"
            "createRoot(document.getElementById('root')!).render(<App />);\n"
        ),
        "src/App.tsx": (
            "import './App.css';\n"
            'import { initialState } from "./state";\n'
            "import type { AppProps } from './types';\n"
            "export function App({ title }: AppProps) { return <main>{title}{initialState}</main>; }\n"
        ),
        "src/App.css": ".app { color: tomato; }\n",
        "src/index.css": "body { margin: 0; }\n",
        "src/state.ts": "export const initialState = 'ready';\n",
        "src/types.ts": "export interface AppProps { title: string }\n",
        "tests/App.test.tsx": "test('renders the app', () => expect(true).toBe(true));\n",
    }
    for relative_path, content in files.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return [root / relative_path for relative_path in sorted(files)]


def test_react_roles_landmarks_and_quoted_css_imports(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENVANTAGE_INDEX_ROOT", str(tmp_path / "cache"))
    files = _materialize_react_repo(tmp_path)

    result = build_repository_index(tmp_path, files)
    entries = result.entries

    assert entries["src/App.tsx"].role == "source"
    assert entries["src/App.css"].role == "style"
    assert entries["src/state.ts"].landmarks == ("state",)
    assert "types" in entries["src/types.ts"].landmarks
    assert "react-entrypoint" in entries["src/main.tsx"].landmarks
    assert "app-shell" in entries["src/App.tsx"].landmarks
    assert "./App.css" in entries["src/App.tsx"].imports
    assert "src/App.css" in entries["src/App.tsx"].local_import_paths
    assert "src/App.tsx" in entries["src/main.tsx"].local_import_paths
    assert "src/main.tsx" in entries["src/App.tsx"].imported_by_paths
    assert result.stats["test_layout"]["files"] == ["tests/App.test.tsx"]
    assert result.stats["revision"]
    assert result.stats["freshness"]["content_hashes_validated"] == len(files)


def test_index_resolves_absolute_python_imports_inside_repository(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AGENVANTAGE_INDEX_ROOT", str(tmp_path / "cache"))
    cli = tmp_path / "graphify" / "cli.py"
    server = tmp_path / "graphify" / "serve.py"
    cli.parent.mkdir()
    cli.write_text("from graphify.serve import build_server\n", encoding="utf-8")
    server.write_text("def build_server():\n    return None\n", encoding="utf-8")

    result = build_repository_index(tmp_path, [cli, server])

    assert result.entries["graphify/cli.py"].local_import_paths == ("graphify/serve.py",)
    assert result.entries["graphify/serve.py"].imported_by_paths == ("graphify/cli.py",)


def test_index_ignores_import_like_string_literals_in_python(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AGENVANTAGE_INDEX_ROOT", str(tmp_path / "cache"))
    source = tmp_path / "parser.py"
    source.write_text('pattern = "from \\\"/\\\""\n', encoding="utf-8")

    result = build_repository_index(tmp_path, [source])

    assert result.entries["parser.py"].local_import_paths == ()


def test_index_reuses_warm_entries_but_invalidates_same_size_content(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AGENVANTAGE_INDEX_ROOT", str(tmp_path / "cache"))
    path = tmp_path / "src" / "value.ts"
    path.parent.mkdir()
    path.write_text("export const value = 'one';\n", encoding="utf-8")

    first = build_repository_index(tmp_path, [path])
    second = build_repository_index(tmp_path, [path])
    assert first.stats["rebuilt_files"] == 1
    assert second.stats["reused_files"] == 1
    assert second.stats["rebuilt_files"] == 0
    old_hash = second.entries["src/value.ts"].content_hash

    original_stat = path.stat()
    path.write_text("export const value = 'two';\n", encoding="utf-8")
    os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    third = build_repository_index(tmp_path, [path])

    assert third.stats["reused_files"] == 0
    assert third.stats["rebuilt_files"] == 1
    assert third.entries["src/value.ts"].content_hash != old_hash
    cache_payload = json.loads(Path(third.stats["cache_path"]).read_text(encoding="utf-8"))
    assert cache_payload["manifest"]["revision"] == third.stats["revision"]
    assert cache_payload["entries"]["src/value.ts"]["content_hash"] == third.entries[
        "src/value.ts"
    ].content_hash


def test_index_classifies_discoverable_generated_and_vendor_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENVANTAGE_INDEX_ROOT", str(tmp_path / "cache"))
    paths = []
    for relative_path in ("dist/app.js", "vendor/library.js", "src/component.tsx"):
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("export const value = true;\n", encoding="utf-8")
        paths.append(path)

    result = build_repository_index(tmp_path, paths)

    assert result.entries["dist/app.js"].role == "generated"
    assert result.entries["vendor/library.js"].role == "vendor"
    assert result.entries["src/component.tsx"].role == "source"
