"""Optional per-project configuration for AgenVantage.

A developer can drop an ``.agenvantage.toml`` file at a repository root to set
recurring ``pack`` defaults instead of repeating flags::

    [pack]
    budget = 6000
    model = "gpt-4o-mini"
    preset = "explain"
    top_k = 20
    include_glob = ["src/*"]
    exclude_glob = ["docs/*", "**/*.min.js"]
    graph_backend = "off"
    graph_json = "graphify-out/graph.json"

CLI flags always override config values, which override built-in defaults.
Parsing uses the standard-library ``tomllib`` (Python 3.11+); on older
interpreters without it, configuration is skipped rather than erroring.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - depends on interpreter version
    tomllib = None  # type: ignore[assignment]

CONFIG_FILENAME = ".agenvantage.toml"


@dataclass(frozen=True)
class PackConfig:
    budget: int | None = None
    model: str | None = None
    preset: str | None = None
    top_k: int | None = None
    include_glob: tuple[str, ...] = field(default_factory=tuple)
    exclude_glob: tuple[str, ...] = field(default_factory=tuple)
    graph_backend: str | None = None
    graph_json: Path | None = None
    graph_hops: int | None = None
    graph_timeout: float | None = None
    graphify_executable: str | None = None
    source: Path | None = None


def find_config_file(search_paths: list[Path]) -> Path | None:
    seen: set[Path] = set()
    for base in search_paths:
        base = base if base.is_dir() else base.parent
        try:
            base = base.resolve()
        except OSError:  # pragma: no cover - defensive
            continue
        if base in seen:
            continue
        seen.add(base)
        candidate = base / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return None


def _as_str_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        items: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("expected every list item to be a string")
            items.append(item)
        return tuple(items)
    raise ValueError("expected a string or list of strings")


def load_pack_config(search_paths: list[Path]) -> PackConfig:
    """Load ``[pack]`` config from the first matching file, or empty defaults."""
    if tomllib is None:
        return PackConfig()

    config_path = find_config_file(search_paths)
    if config_path is None:
        return PackConfig()

    with config_path.open("rb") as handle:
        data = tomllib.load(handle)

    pack = data.get("pack", {})
    if not isinstance(pack, dict):
        raise ValueError(f"[pack] section in {config_path} must be a table.")

    budget = pack.get("budget")
    top_k = pack.get("top_k")
    model = pack.get("model")
    preset = pack.get("preset")
    graph_backend = pack.get("graph_backend")
    graph_json = pack.get("graph_json")
    graph_hops = pack.get("graph_hops")
    graph_timeout = pack.get("graph_timeout")
    graphify_executable = pack.get("graphify_executable")

    if budget is not None and (not isinstance(budget, int) or isinstance(budget, bool)):
        raise ValueError(f"pack.budget in {config_path} must be an integer.")
    if top_k is not None and (not isinstance(top_k, int) or isinstance(top_k, bool)):
        raise ValueError(f"pack.top_k in {config_path} must be an integer.")
    if model is not None and not isinstance(model, str):
        raise ValueError(f"pack.model in {config_path} must be a string.")
    if preset is not None and not isinstance(preset, str):
        raise ValueError(f"pack.preset in {config_path} must be a string.")
    if graph_backend is not None and graph_backend not in {"auto", "off", "graphify"}:
        raise ValueError(
            f'pack.graph_backend in {config_path} must be "auto", "off", or "graphify".'
        )
    if graph_json is not None and not isinstance(graph_json, str):
        raise ValueError(f"pack.graph_json in {config_path} must be a string.")
    if graph_hops is not None and (
        not isinstance(graph_hops, int) or isinstance(graph_hops, bool) or graph_hops not in {1, 2}
    ):
        raise ValueError(f"pack.graph_hops in {config_path} must be 1 or 2.")
    if graph_timeout is not None and (
        not isinstance(graph_timeout, (int, float))
        or isinstance(graph_timeout, bool)
        or graph_timeout <= 0
    ):
        raise ValueError(f"pack.graph_timeout in {config_path} must be a positive number.")
    if graphify_executable is not None and not isinstance(graphify_executable, str):
        raise ValueError(f"pack.graphify_executable in {config_path} must be a string.")

    graph_json_path = None
    if graph_json is not None:
        graph_json_path = Path(graph_json).expanduser()
        if not graph_json_path.is_absolute():
            graph_json_path = config_path.parent / graph_json_path

    return PackConfig(
        budget=budget,
        model=model,
        preset=preset,
        top_k=top_k,
        include_glob=_as_str_tuple(pack.get("include_glob")),
        exclude_glob=_as_str_tuple(pack.get("exclude_glob")),
        graph_backend=graph_backend,
        graph_json=graph_json_path,
        graph_hops=graph_hops,
        graph_timeout=float(graph_timeout) if graph_timeout is not None else None,
        graphify_executable=graphify_executable,
        source=config_path,
    )
