# Contributing

Paper Galaxy is local-first. Contributions should preserve the default privacy
boundary: no telemetry, no cloud calls, no accounts, no remote assets, and no
hidden model downloads.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,ml,pdf,app]"
```

## Checks

```bash
python -m ruff check .
python -m ruff format . --check
python -m mypy src
python -m pytest
python -m build
python scripts/build_demo_site.py --out site_dist
python scripts/check_demo_site.py --dist site_dist
python scripts/public_readiness_check.py --strict
```

Use `make validate-example` for a small end-to-end local smoke test.
Use `make launch-check` before public-launch work.

## Generated Files

Do not commit `.paper-galaxy/`, SQLite databases, generated HTML/JSON reports,
backup zip files, vector indexes, or downloaded model files.

Do not paste private document text, API keys, local paths containing sensitive
names, or project SQLite contents into public issues.
