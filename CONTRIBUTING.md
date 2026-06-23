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
```

Use `make validate-example` for a small end-to-end local smoke test.

## Generated Files

Do not commit `.paper-galaxy/`, SQLite databases, generated HTML/JSON reports,
backup zip files, vector indexes, or downloaded model files.
