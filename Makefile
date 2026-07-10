.PHONY: install-dev install-embeddings test lint format typecheck check doctor build check-build clean clean-build clean-artifacts validate-example demo-site public-check live-check post-public-check release-check launch-report launch-check zotero-smoke zotero-demo-test zotero-check

CLEAN_BUILD_ARTIFACTS = \
	dist \
	build \
	site_dist \
	*.egg-info \
	src/*.egg-info \
	.pytest_cache \
	.ruff_cache \
	.mypy_cache \
	.coverage \
	htmlcov \
	__pycache__ \
	scripts/__pycache__ \
	tests/__pycache__ \
	src/paper_galaxy/__pycache__ \
	src/paper_galaxy/*/__pycache__

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

clean: clean-build

clean-build:
	rm -rf $(CLEAN_BUILD_ARTIFACTS)

clean-artifacts: clean-build

zotero-smoke:
	paper-galaxy zotero detect
	-paper-galaxy zotero status

zotero-demo-test:
	python -m pytest tests/test_zotero*.py

zotero-check:
	python -m pytest tests/test_zotero*.py
	python scripts/public_readiness_check.py --strict

validate-example:
	paper-galaxy init . --force
	paper-galaxy index examples/tiny_corpus --project-dir . --min-chars 40
	paper-galaxy validate-project --project-dir .

demo-site:
	python scripts/build_demo_site.py --out site_dist
	python scripts/check_demo_site.py --dist site_dist

public-check:
	python -m pytest
	python -m build
	python scripts/build_demo_site.py --out site_dist
	python scripts/check_demo_site.py --dist site_dist
	python scripts/public_readiness_check.py --strict --require-site-dist

live-check:
	python scripts/check_live_site.py --allow-not-deployed

post-public-check:
	python scripts/build_demo_site.py --out site_dist
	python scripts/check_demo_site.py --dist site_dist --serve
	python scripts/public_readiness_check.py --strict --require-site-dist
	python scripts/check_live_site.py --allow-not-deployed

release-check: clean-build
	python -m ruff check .
	python -m ruff format . --check
	python -m mypy src
	python -m pytest
	python -m build
	python scripts/build_demo_site.py --out site_dist
	python scripts/check_demo_site.py --dist site_dist
	python scripts/public_readiness_check.py --strict --require-site-dist

launch-report:
	python scripts/build_demo_site.py --out site_dist
	python scripts/public_readiness_check.py --strict --require-site-dist --json-out public-readiness.json
	python scripts/launch_report.py --require-site-dist --out launch-report.md

launch-check: clean-build
	python -m ruff check .
	python -m ruff format . --check
	python -m mypy src
	python -m pytest
	python -m build
	python scripts/build_demo_site.py --out site_dist
	python scripts/check_demo_site.py --dist site_dist
	python scripts/public_readiness_check.py --strict --require-site-dist
