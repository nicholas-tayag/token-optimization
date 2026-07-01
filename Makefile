.PHONY: setup demo test run view help

help:
	@echo "Targets:"
	@echo "  make setup   Install the project into .venv"
	@echo "  make demo    Run the built-in on-call demo"
	@echo "  make run     Run the default experiment with a summary"
	@echo "  make view    Open the policy explorer dashboard"
	@echo "  make test    Run the test suite"

setup:
	bash scripts/setup.sh

demo:
	@test -x .venv/bin/agenvantage || (echo "Run 'make setup' first." && exit 1)
	.venv/bin/agenvantage demo --no-browser

run:
	@test -x .venv/bin/agenvantage || (echo "Run 'make setup' first." && exit 1)
	.venv/bin/agenvantage run --summary

view:
	@test -x .venv/bin/agenvantage || (echo "Run 'make setup' first." && exit 1)
	.venv/bin/agenvantage view --report artifacts/oncall-report.json --no-browser

test:
	@test -x .venv/bin/pytest || (echo "Run 'make setup' first." && exit 1)
	.venv/bin/pytest
