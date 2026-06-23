.PHONY: install-dev install-embeddings test lint format typecheck check doctor build check-build clean-artifacts validate-example

install-dev:
	python -m pip install -e ".[dev,ml,pdf,app]"

install-embeddings:
	python -m pip install -e ".[dev,ml,pdf,app,embeddings]"

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

build:
	python -m build

check-build: build
	python -m pip install --force-reinstall dist/*.whl
	paper-galaxy doctor

clean-artifacts:
	rm -rf .paper-galaxy galaxy.html galaxy.json extraction-report.json validation.json map-run*.json paper-galaxy-backup*.zip dist build *.egg-info src/*.egg-info
	find . -name "*.sqlite3" -delete

validate-example:
	paper-galaxy init . --force
	paper-galaxy index examples/tiny_corpus --project-dir . --min-chars 40
	paper-galaxy validate-project --project-dir .
