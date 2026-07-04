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
        return tuple(str(item) for item in value)
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

    if budget is not None and not isinstance(budget, int):
        raise ValueError(f"pack.budget in {config_path} must be an integer.")
    if top_k is not None and not isinstance(top_k, int):
        raise ValueError(f"pack.top_k in {config_path} must be an integer.")
    if model is not None and not isinstance(model, str):
        raise ValueError(f"pack.model in {config_path} must be a string.")
    if preset is not None and not isinstance(preset, str):
        raise ValueError(f"pack.preset in {config_path} must be a string.")

    return PackConfig(
        budget=budget,
        model=model,
        preset=preset,
        top_k=top_k,
        include_glob=_as_str_tuple(pack.get("include_glob")),
        exclude_glob=_as_str_tuple(pack.get("exclude_glob")),
        source=config_path,
    )
