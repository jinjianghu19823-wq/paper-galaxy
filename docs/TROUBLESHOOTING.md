# Troubleshooting

[English](TROUBLESHOOTING.md) | [简体中文](TROUBLESHOOTING.zh-CN.md)

## Installation Failures

Create a clean virtual environment and install the app extras:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,ml,pdf,app]"
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
