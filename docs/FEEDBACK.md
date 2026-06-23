# Early Feedback Guide

[English](FEEDBACK.md) | [简体中文](FEEDBACK.zh-CN.md)

Paper Galaxy is in public alpha. Early feedback is most useful when it is small,
reproducible, and privacy-safe.

## Suggested First Tasks

1. Open the public demo:
   <https://jinjianghu19823-wq.github.io/paper-galaxy/>
2. Install locally:
   `python -m pip install -e ".[dev,ml,pdf,app]"`
3. Index the tiny corpus:
   `paper-galaxy index examples/tiny_corpus --project-dir . --min-chars 40`
4. Serve the local app:
   `paper-galaxy serve --project-dir .`
5. Index a small personal test folder that does not contain sensitive text.
6. Run validation:
   `paper-galaxy validate-project --project-dir .`
7. Try an explanation:
   `paper-galaxy explain-pair SOURCE TARGET --project-dir .`
8. If you use Zotero Desktop, try a local dry run first:
   `paper-galaxy zotero smoke-test --project-dir .`
9. Then import a small Zotero sample with `--limit` before importing a large
   library:
   `paper-galaxy zotero import --project-dir . --limit 20 --include-pdfs --include-notes --build-reading-map`

## Useful Feedback

- Extraction quality and confusing skipped-file reasons.
- Graph usability, label overlap, or hard-to-understand neighbor links.
- Commands that are confusing or too verbose.
- Install problems on specific Python/OS versions.
- Privacy concerns or unclear local data boundaries.
- Zotero detection, attachment path, metadata-only import, or reading graph
  filtering issues.
- Documentation gaps.

## Do Not Share

- Private paper text or sensitive extracted chunks.
- Real Zotero databases, `zotero.sqlite`, Zotero `storage/` folders, or PDFs.
- `.paper-galaxy/` directories or SQLite databases.
- API keys, tokens, secrets, or private keys.
- Local paths that reveal sensitive names or institutions.
- Backup bundles unless they are synthetic and intentionally prepared.

## Where To Report

Use the issue templates:
<https://github.com/jinjianghu19823-wq/paper-galaxy/issues/new/choose>

If a bug depends on private text, create a tiny synthetic file that reproduces
the same shape of failure.
