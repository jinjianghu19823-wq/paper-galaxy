# Publishing Checklist

[English](PUBLISHING_CHECKLIST.md) | [简体中文](PUBLISHING_CHECKLIST.zh-CN.md)

Use this checklist for the public repository launch and post-public activation
of `jinjianghu19823-wq/paper-galaxy`.

## Local Checks

```bash
make clean-build
make check
make build
make public-check
python scripts/build_demo_site.py --out site_dist
python scripts/check_demo_site.py --dist site_dist --serve
python scripts/check_live_site.py --allow-not-deployed
```

`clean-build` (and the compatibility `clean` / `clean-artifacts` targets)
removes only explicit build outputs and tool caches. It does not delete local
projects, databases, Zotero data, backups, vector indexes, or user exports.
The default demo build writes generated data only inside `site_dist/` and must
leave tracked sources unchanged.

Also useful:

```bash
python scripts/public_readiness_check.py --strict --require-site-dist --json-out public-readiness.json
make launch-check
make post-public-check
make release-check
```

## Verify Tracked And Public Content

Treat these as version-control and public-artifact checks, not instructions to
delete ignored local data:

- No tracked `.paper-galaxy/` project state or SQLite databases.
- No tracked backup zip files, downloaded models, or vector indexes.
- No tracked generated `galaxy.html`, `galaxy.json`, extraction reports,
  validation reports, or map run exports.
- No tracked secrets, tokens, `.env` files, private keys, or API keys.
- No tracked user documents.
- No real Zotero data: no tracked `zotero.sqlite`, Zotero `storage/` folders,
  PDFs, `zotero://items/...` records, or private Zotero paths in public demo
  data.
- No external runtime assets in the public demo site.
- Demo data comes from synthetic `examples/tiny_corpus` only.
- English and Simplified Chinese static pages load.

`site/data/tiny-map.json` is a reviewed source fixture. Refresh it only for an
intentional payload change with
`python scripts/build_demo_site.py --out site_dist --refresh-source-data`, then
review its diff. Never use that flag in CI, Pages, release checks, or a default
build.

## GitHub Pages

The Pages workflow always builds and checks the static site. It only deploys
when the repository visibility is public, so private-repository pushes can
verify the site without publishing it.

1. Open repository settings.
2. Go to Pages.
3. Confirm Source is GitHub Actions.
4. Push to `main` or run the Pages workflow manually.
5. Check Actions -> Pages for a successful run.
6. Run:

```bash
gh workflow run pages.yml
gh run list --workflow=pages.yml --limit 5
python scripts/check_live_site.py --base-url https://jinjianghu19823-wq.github.io/paper-galaxy/
```

7. Verify the expected URL:
   `https://jinjianghu19823-wq.github.io/paper-galaxy/`.

## Make The Repository Public

Prefer the GitHub UI if unsure:

1. Settings.
2. General.
3. Danger Zone.
4. Change repository visibility.
5. Choose public only after public readiness passes.

If using GitHub CLI, inspect the current command first:

```bash
gh repo edit --help
```

Then use the current supported command to set visibility public. Do not use an
outdated flag blindly.

## After Public

- Verify the repository loads anonymously.
- Verify the Pages site loads.
- Verify `python scripts/check_live_site.py` passes.
- Verify the CI badge.
- Verify issue templates.
- Verify README links.
- Verify license detection.
- Verify the Pages URL.
- Verify README badges load.
- Verify no generated sensitive files are present.
- Verify release notes are published or ready.
- Verify first issue labels exist or are documented in [TRIAGE.md](TRIAGE.md).

## GitHub Release

Prepare but do not publish a release unless explicitly approved:

```bash
make clean-build
make release-check
python -m build
gh release create v0.1.0 dist/* \
  --title "Paper Galaxy v0.1.0" \
  --notes-file docs/LAUNCH_NOTES.md
```

Do not publish to PyPI unless explicitly requested.

## Suggested Topics

If `gh` is available and authenticated:

```bash
gh repo edit jinjianghu19823-wq/paper-galaxy --add-topic research --add-topic papers --add-topic local-first --add-topic sqlite --add-topic visualization --add-topic knowledge-graph --add-topic semantic-search --add-topic python --add-topic fastapi
```

If the command shape changes, use the GitHub UI:

Settings -> General -> Topics, then add:

- research
- papers
- local-first
- sqlite
- visualization
- knowledge-graph
- semantic-search
- python
- fastapi

## Optional Future Launch Work

- Custom domain.
- PyPI publish.
- Demo video or GIF.
- Project announcement.
- Academic or research community outreach.
