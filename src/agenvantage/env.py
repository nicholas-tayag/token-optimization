from __future__ import annotations

import os
from pathlib import Path


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[len("export ") :].strip()
    if "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    if not key:
        return None
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    elif " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    return key, value


def find_dotenv(start: Path | None = None, filename: str = ".env") -> Path | None:
    current = (start or Path.cwd()).resolve()
    if current.is_file():
        current = current.parent
    for candidate_dir in (current, *current.parents):
        candidate = candidate_dir / filename
        if candidate.is_file():
            return candidate
    return None


def load_dotenv(start: Path | None = None, filename: str = ".env") -> Path | None:
    dotenv_path = find_dotenv(start, filename)
    if dotenv_path is None:
        return None

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_env_line(raw_line)
        if parsed is None:
            continue
        key, value = parsed
        os.environ.setdefault(key, value)
    return dotenv_path
