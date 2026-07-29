# Contributing

## Local setup

```bash
make setup
source .venv/bin/activate
```

Run the full checks before opening a pull request:

```bash
make test
ruff check .
mypy src/agenvantage
python -m build
```

Keep changes scoped, add regression tests for behavior changes, and do not
commit `.env`, generated artifacts, provider keys, or local repository paths.
Benchmark claims must include the task dataset, repository revisions, model
configuration, and whether measurements are local estimates or provider usage.

## Pull requests

Explain the user-visible behavior, validation commands, and any claim boundary
or reproducibility limitation. Do not report theoretical token reduction as
provider-billed savings.
