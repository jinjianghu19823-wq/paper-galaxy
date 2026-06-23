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
make release-check
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
paper-galaxy zotero detect
paper-galaxy zotero smoke-test --project-dir . || true
paper-galaxy export-project --project-dir . --out paper-galaxy-backup.zip --yes
```

The Zotero smoke test is optional because CI and release machines may not have
Zotero Desktop running. It must fail gracefully when the local API is not
available.

6. Build and check the public demo:

```bash
python scripts/build_demo_site.py --out site_dist
python scripts/check_demo_site.py --dist site_dist
python scripts/public_readiness_check.py --strict --require-site-dist
python scripts/check_live_site.py --allow-not-deployed
```

7. Run the public launch aggregate check:

```bash
make launch-check
```

8. Remove generated local artifacts before committing. `site/data/tiny-map.json`
is committed source data; `site_dist/` is generated and gitignored.

Phase 7 releases are Python package releases only. They do not include desktop
packaging, cloud services, account systems, or remote plugin loading.

Do not publish to PyPI unless the repository owner explicitly requests it.

## v0.1.0 Public Alpha Release

The first public GitHub Release should be treated as an alpha announcement, not
a package registry launch.

1. Confirm all checks pass:

```bash
make clean-artifacts
make release-check
make post-public-check
```

2. Confirm the live site:

```bash
python scripts/check_live_site.py --base-url https://jinjianghu19823-wq.github.io/paper-galaxy/
```

3. Inspect generated distributions:

```bash
python -m build
ls dist/
```

4. Review release notes:

```bash
sed -n '1,220p' docs/LAUNCH_NOTES.md
```

5. Create the tag and GitHub Release only after explicit approval:

```bash
git tag v0.1.0
git push origin v0.1.0
gh release create v0.1.0 dist/* \
  --title "Paper Galaxy v0.1.0" \
  --notes-file docs/LAUNCH_NOTES.md
```

The tag push also triggers `.github/workflows/release.yml`, which runs checks,
builds the Python distributions, and uploads the wheel/sdist as workflow
artifacts. The workflow does not publish to PyPI and does not require secrets.

## Rollback Plan

If a release tag or GitHub Release is created incorrectly:

1. Delete or edit the GitHub Release in the GitHub UI or with `gh release`.
2. If the tag is wrong and no one should use it, delete the remote tag:

```bash
git push origin :refs/tags/v0.1.0
```

3. Fix the repository state on `main`, rerun `make release-check`, then create a
new tag only after review.

Do not rewrite published history on `main`.

## Public Launch Checks

For a public repository launch, also follow
[PUBLISHING_CHECKLIST.md](PUBLISHING_CHECKLIST.md). Verify GitHub Pages is set
to Source = GitHub Actions, then confirm:

- repository loads anonymously;
- Pages URL loads;
- live-site check passes;
- issue templates appear;
- README demo, privacy, install, roadmap, and contribution links work;
- English and Simplified Chinese demo pages load.
