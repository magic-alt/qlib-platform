.PHONY: install lint type test coverage contract audit ci

install:
	pip install -e ".[dev]"

lint:
	ruff check src tests

type:
	mypy src

test:
	pytest

coverage:
	pytest --cov=src/tushare_qlib --cov-report=term-missing --cov-fail-under=60

contract:
	tq --config configs/pipeline.yaml validate-qrun-contract

audit:
	tq project-audit --root . --output docs/project_audit.json

ci: lint type contract coverage audit
