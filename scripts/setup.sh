#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if command -v uv >/dev/null 2>&1; then
  echo "Using uv to create a Python 3.12 virtual environment..."
  uv venv .venv --python 3.12
  # shellcheck disable=SC1091
  source .venv/bin/activate
  uv pip install -e ".[dev]"
else
  if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 not found. Install Python 3.10+ or https://docs.astral.sh/uv/." >&2
    exit 1
  fi
  echo "Using python3 to create a virtual environment..."
  python3 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  python -m pip install --upgrade pip
  python -m pip install -e ".[dev]"
fi

cat <<'EOF'

Setup complete.

Next steps:
  source .venv/bin/activate
  agenvantage demo

Other commands:
  agenvantage run --summary
  agenvantage pack --repo . --task "Explain the CLI" --budget 1800
  pytest
EOF
