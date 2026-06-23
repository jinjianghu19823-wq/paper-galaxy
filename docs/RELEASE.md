# Release Checklist

[English](RELEASE.md) | [简体中文](RELEASE.zh-CN.md)

1. Update `CHANGELOG.md`.
2. Verify `pyproject.toml` and `src/paper_galaxy/__init__.py` versions match.
3. Remove generated artifacts:

```bash
make clean-artifacts
```

4. Run checks:

```bash
python -m ruff check .
python -m ruff format . --check
python -m mypy src
python -m pytest
python -m build
```

5. Run a local example validation:

```bash
paper-galaxy init . --force
paper-galaxy index examples/tiny_corpus --project-dir . --min-chars 40
paper-galaxy validate-project --project-dir .
paper-galaxy build-map-run --project-dir . --name "Release smoke"
paper-galaxy export-project --project-dir . --out paper-galaxy-backup.zip --yes
```

6. Build and check the public demo:

```bash
python scripts/build_demo_site.py --out site_dist
python scripts/check_demo_site.py --dist site_dist
python scripts/public_readiness_check.py --strict
```

7. Run the public launch aggregate check:

```bash
make launch-check
```

8. Remove generated local artifacts before committing. `site/data/tiny-map.json`
is committed source data; `site_dist/` is generated and gitignored.

Phase 7 releases are Python package releases only. They do not include desktop
packaging, cloud services, account systems, or remote plugin loading.

## Public Launch Checks

For a public repository launch, also follow
[PUBLISHING_CHECKLIST.md](PUBLISHING_CHECKLIST.md). Verify GitHub Pages is set
to Source = GitHub Actions, then confirm:

- repository loads anonymously;
- Pages URL loads;
- issue templates appear;
- README demo, privacy, install, roadmap, and contribution links work;
- English and Simplified Chinese demo pages load.
