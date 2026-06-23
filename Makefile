.PHONY: install-dev install-embeddings test lint format typecheck check doctor build check-build clean-artifacts validate-example demo-site public-check live-check post-public-check release-check launch-report launch-check

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
	rm -rf .paper-galaxy galaxy.html galaxy.json extraction-report.json validation.json map-run*.json paper-galaxy-backup*.zip public-readiness.json live-site-check.json launch-report.md release-notes.generated.md site_dist dist build *.egg-info src/*.egg-info
	find . -name "*.sqlite3" -delete
	find . -name "*.faiss" -delete
	find . -name "*.index" -delete

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

release-check: clean-artifacts
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

launch-check: clean-artifacts
	python -m ruff check .
	python -m ruff format . --check
	python -m mypy src
	python -m pytest
	python -m build
	python scripts/build_demo_site.py --out site_dist
	python scripts/check_demo_site.py --dist site_dist
	python scripts/public_readiness_check.py --strict --require-site-dist
