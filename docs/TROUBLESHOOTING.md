# Troubleshooting

[English](TROUBLESHOOTING.md) | [简体中文](TROUBLESHOOTING.zh-CN.md)

## Installation Failures

Create a clean virtual environment and install the app extras:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install ".[full]"
paper-galaxy doctor
```

If installation fails, include the Python version, OS, command, and the last
error block in the bug report. Do not include private paths if they reveal
sensitive names.

## Missing scikit-learn

Install the `ml` extra:

```bash
python -m pip install -e ".[dev,ml,pdf,app]"
```

Then rerun `paper-galaxy doctor`.

## Missing pypdf

PDF extraction is optional. Install the `pdf` extra:

```bash
python -m pip install -e ".[dev,ml,pdf,app]"
```

If `pypdf` is unavailable, PDFs are skipped with a clear reason rather than
crashing the scan.

## OCR Missing Tesseract

OCR is disabled by default. If you use OCR, install the Python extra and a local
Tesseract binary:

```bash
python -m pip install -e ".[dev,ml,pdf,app,ocr]"
paper-galaxy scan /path/to/corpus --include-images --ocr --out galaxy.html --force
```

Missing OCR packages or a missing binary should be reported as skips.

## Embeddings Hidden Download Rejection

`paper-galaxy embed` rejects remote model names by default. Pass a local model
path:

```bash
paper-galaxy embed --project-dir . --model /path/to/local/model
```

Only use `--allow-model-download` if you intentionally want Sentence
Transformers to resolve/download a model.

## Database Missing

If the local app says the database is missing, initialize and index a corpus:

```bash
paper-galaxy init . --force
paper-galaxy index examples/tiny_corpus --project-dir . --min-chars 40
paper-galaxy serve --project-dir .
```

For a new workstation, the equivalent one-command flow is:

```bash
paper-galaxy launch --project-dir ~/PaperGalaxy --corpus ~/Papers --open
```

## Launch Says A Worker Is Already Active

Only one background writer may serve a project. Close the other Paper Galaxy
window/process and retry. Do not delete the worker lock or edit the jobs table;
the next exclusive worker safely marks genuinely interrupted work and preserves
completed artifacts. A restore must also run while the workspace is stopped.

If launch rejects a source/project relationship, choose a project directory
outside the corpus or Zotero data directory. Paper Galaxy intentionally refuses
to create its database inside a read-only source tree.

## Zotero Local API Unavailable

`paper-galaxy zotero status` needs Zotero Desktop running with the local API
enabled. Start Zotero, then run:

```bash
paper-galaxy zotero detect
paper-galaxy zotero status
paper-galaxy zotero doctor --project-dir .
```

If the API is still unavailable, check Zotero Settings -> Advanced and confirm
the local API is enabled. The Zotero data directory shown in Zotero settings is
authoritative; `paper-galaxy zotero detect` only makes a best-effort guess.
`paper-galaxy zotero doctor --json-out zotero-doctor.json` writes a no-write
readiness report that is useful for private triage.

## Zotero Import Has Missing PDFs

Missing or linked-outside-data-dir attachments do not block the import. Paper
Galaxy can still create metadata-only documents from titles, abstracts, notes,
tags, and collections:

```bash
paper-galaxy zotero import --project-dir . --include-metadata-only --build-reading-map
```

Paper Galaxy does not write to Zotero, does not upload Zotero data, and does
not copy PDFs by default.

If you want to separate metadata issues from PDF extraction issues, run:

```bash
paper-galaxy zotero import --project-dir . --pdf-policy metadata --dry-run
paper-galaxy zotero import --project-dir . --pdf-policy skip-missing --dry-run
```

## FTS5 Unavailable

SQLite FTS5 is required for local full-text search. Run:

```bash
paper-galaxy validate-project --project-dir .
```

If FTS5 is missing, use a Python/SQLite build that includes FTS5.

## Pages Or Demo Static Site Not Loading

Build and check locally:

```bash
python scripts/build_demo_site.py --out site_dist
python scripts/check_demo_site.py --dist site_dist --serve
python scripts/check_live_site.py --allow-not-deployed
```

For GitHub Pages, confirm repository Settings -> Pages -> Source is GitHub
Actions, then run the Pages workflow.

## Graph Blank Or Empty

For the public demo, check browser console and ensure `/data/tiny-map.json`
loads. For the local app, run:

```bash
paper-galaxy validate-project --project-dir .
paper-galaxy db-stats --project-dir .
```

## No Active Indexed Documents

Re-index with a lower minimum character threshold for testing:

```bash
paper-galaxy index examples/tiny_corpus --project-dir . --min-chars 40
```

Missing files and unindexed files are intentionally excluded from the default
map.

## Permission Issues

Index a folder you can read and write project metadata to a directory you own.
Avoid committing `.paper-galaxy/`, SQLite files, or backup bundles.

## Run Validation

```bash
paper-galaxy validate-project --project-dir .
```

Validation reports counts, schema status, warnings, and errors without full
extracted text.

## Semantic Search Says No Current Vectors

Current builds reject legacy, orphaned, inactive, stale-source, malformed, or
model-fingerprint-mismatched vectors instead of returning them as valid hits.
Inspect the local-only counts without changing the project:

```bash
paper-galaxy prune-stale-vectors --project-dir .
paper-galaxy validate-project --project-dir .
```

Re-run `paper-galaxy embed` with the intended local model path to rebuild
current rows. If you have reviewed the report and want to remove only invalid
SQLite vector/index-metadata rows, use:

```bash
paper-galaxy prune-stale-vectors --project-dir . --apply --yes
```

This command never removes papers, project databases, backups, or user files.
If the local model directory changes while loading, stabilize that directory
and retry; Paper Galaxy will not bind vectors to an unverified path-only model
identity.
