# Release Checklist

1. Update `CHANGELOG.md`.
2. Verify `pyproject.toml` and `src/paper_galaxy/__init__.py` versions match.
3. Run checks:

```bash
python -m ruff check .
python -m ruff format . --check
python -m mypy src
python -m pytest
python -m build
```

4. Run a local example validation:

```bash
paper-galaxy init . --force
paper-galaxy index examples/tiny_corpus --project-dir . --min-chars 40
paper-galaxy validate-project --project-dir .
paper-galaxy build-map-run --project-dir . --name "Release smoke"
paper-galaxy export-project --project-dir . --out paper-galaxy-backup.zip --yes
```

5. Remove generated artifacts before committing.

Phase 7 releases are Python package releases only. They do not include desktop
packaging, cloud services, account systems, or remote plugin loading.
