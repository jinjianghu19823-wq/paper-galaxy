.PHONY: install-dev test lint format typecheck check doctor

install-dev:
	python -m pip install -e ".[dev,ml,pdf]"

test:
	python -m pytest

lint:
	python -m ruff check .

format:
	python -m ruff format .

typecheck:
	python -m mypy src

check:
	python -m ruff check .
	python -m ruff format . --check
	python -m mypy src
	python -m pytest

doctor:
	paper-galaxy doctor
