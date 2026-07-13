from __future__ import annotations

from pathlib import Path

from agenvantage.repo_index import build_repository_index
from agenvantage.repo_map import build_repository_map
from agenvantage.tokenizer import TokenCounter


def _materialize_react_repo(root: Path) -> list[Path]:
    files = {
        "package.json": '{"scripts":{"dev":"vite"}}\n',
        "vite.config.ts": "export default {};\n",
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


def test_repository_map_exposes_react_landmarks_test_layout_and_selected_nodes(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AGENVANTAGE_INDEX_ROOT", str(tmp_path / "cache"))
    files = _materialize_react_repo(tmp_path)
    index_result = build_repository_index(tmp_path, files)

    output, manifest = build_repository_map(
        tmp_path,
        "trace app state and types styling through the React entrypoint",
        budget=900,
        index_result=index_result,
        counter=TokenCounter(),
    )
    selected_paths = {node["path"] for node in manifest["selected_nodes"]}
    landmark_kinds = {item["kind"] for item in manifest["landmarks"]}

    assert manifest["budget"] == 900
    assert manifest["token_count"] == TokenCounter().count(output)
    assert manifest["token_count"] <= 900
    assert {"react-entrypoint", "state", "types", "style"} <= landmark_kinds
    assert manifest["test_layout"]["directories"] == ["tests"]
    assert "tests/App.test.tsx" in manifest["test_layout"]["files"]
    assert {"src/App.tsx", "src/App.css", "src/state.ts", "src/types.ts"} <= selected_paths
    assert "tests/App.test.tsx" in selected_paths
    assert "## Landmarks" in output
    assert "## Test Layout" in output
    assert "## Selected Nodes" in output


def test_repository_map_is_deterministic_and_clamps_contract_budget(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AGENVANTAGE_INDEX_ROOT", str(tmp_path / "cache"))
    files = _materialize_react_repo(tmp_path)
    index_result = build_repository_index(tmp_path, files)

    first_output, first_manifest = build_repository_map(
        tmp_path, budget=100, index_result=index_result
    )
    second_output, second_manifest = build_repository_map(
        tmp_path, budget=100, index_result=index_result
    )

    assert first_output == second_output
    assert first_manifest["budget"] == 500
    assert first_manifest["token_count"] == second_manifest["token_count"]
    assert first_manifest["selected_nodes"] == second_manifest["selected_nodes"]
