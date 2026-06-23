# Publishing Checklist

Use this checklist before making `jinjianghu19823-wq/paper-galaxy` public.

## Local Checks

```bash
make clean-artifacts
make check
make build
make public-check
python scripts/build_demo_site.py --out site_dist
python scripts/check_demo_site.py --dist site_dist --serve
```

Also useful:

```bash
python scripts/public_readiness_check.py --strict --json-out public-readiness.json
make launch-check
```

## Verify Before Public

- No `.paper-galaxy/`.
- No SQLite databases.
- No backup zip files.
- No generated `galaxy.html`, `galaxy.json`, extraction reports, validation
  reports, or map run exports.
- No secrets, tokens, `.env` files, private keys, or API keys.
- No downloaded model files or vector indexes.
- No user documents.
- No external runtime assets in the public demo site.
- Demo data comes from synthetic `examples/tiny_corpus` only.
- English and Simplified Chinese static pages load.

## GitHub Pages

The Pages workflow always builds and checks the static site. It only deploys
when the repository visibility is public, so private-repository pushes can
verify the site without publishing it.

1. Make the repository public after the readiness checks pass.
2. Open repository settings.
3. Go to Pages.
4. Set Source to GitHub Actions if it is not already enabled.
5. Push to `main` or run the Pages workflow manually.
6. Verify the expected URL:
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
- Verify the CI badge.
- Verify issue templates.
- Verify README links.
- Verify license detection.
- Verify the Pages URL.

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
