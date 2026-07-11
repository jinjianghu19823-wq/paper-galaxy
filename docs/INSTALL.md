# Install

[English](INSTALL.md) | [简体中文](INSTALL.zh-CN.md)

## Try Before Installing

Open the static public demo:

```text
https://jinjianghu19823-wq.github.io/paper-galaxy/
```

The demo uses synthetic data only and includes English plus Simplified Chinese
pages. It does not run the local FastAPI app or read user documents.

## Local Workstation Install

Clone the repository first. All commands below are run from the checkout root.
The `full` extra combines the local web app, TF-IDF/map dependencies, and PDF
extraction. It deliberately excludes OCR, dense embeddings, model downloads,
and development tools.

### pip and venv

```bash
git clone https://github.com/jinjianghu19823-wq/paper-galaxy.git
cd paper-galaxy
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install ".[full]"
paper-galaxy doctor
```

### pipx

```bash
git clone https://github.com/jinjianghu19823-wq/paper-galaxy.git
cd paper-galaxy
pipx install ".[full]"
paper-galaxy doctor
```

### uv tool

```bash
git clone https://github.com/jinjianghu19823-wq/paper-galaxy.git
cd paper-galaxy
uv tool install ".[full]"
paper-galaxy doctor
```

`pipx` and `uv tool` each create a dedicated tool environment and expose the
`paper-galaxy` command. They are convenient for normal use; use a venv and an
editable install when contributing code.

## One-Command Launch

```bash
paper-galaxy launch \
  --project-dir ~/PaperGalaxy \
  --corpus ~/Papers \
  --no-open
```

The command safely creates or reopens the project, registers the corpus as a
source without copying or modifying it, queues indexing for a newly registered
source, and starts the local workstation. Repeating the command reuses the
project and source registration rather than overwriting either. Use `--open`
to open the browser automatically.

The web server binds to loopback (`127.0.0.1`) by default. Paper Galaxy does not
upload the corpus, collect telemetry, or automatically download OCR or
embedding models.

## Development Install

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,ml,pdf,app]"
paper-galaxy doctor
```

This editable install adds test, lint, type-check, and build tools to the local
workstation dependencies.

## Zotero Reading Graph

Zotero support is included in the same app install and uses Zotero Desktop's
local API:

```bash
paper-galaxy zotero detect
paper-galaxy zotero status
paper-galaxy zotero import --project-dir . --include-pdfs --include-notes --build-reading-map
paper-galaxy serve --project-dir .
```

Paper Galaxy does not write to Zotero, performs no upload, and does not copy
PDFs by default. Imported metadata and extracted text live in `.paper-galaxy/`.

## Optional Extras

```bash
python -m pip install -e ".[dev,ml,pdf,app,ocr]"
python -m pip install -e ".[dev,ml,pdf,app,embeddings]"
```

OCR remains disabled unless the user passes OCR flags. Embedding commands still
require an explicit local model path unless `--allow-model-download` is used.
Neither optional extra is part of `full`, and installing `full` never downloads
a model.

## Smoke Test

```bash
PROJECT_DIR="$(mktemp -d)/paper-galaxy-project"
paper-galaxy init "$PROJECT_DIR"
paper-galaxy index examples/tiny_corpus --project-dir "$PROJECT_DIR" --min-chars 40
paper-galaxy validate-project --project-dir "$PROJECT_DIR"
paper-galaxy build-map-run --project-dir "$PROJECT_DIR" --name "Tiny corpus map"
paper-galaxy serve --project-dir "$PROJECT_DIR" --no-open
```

This shell example uses a fresh temporary project. On Windows PowerShell, set
`$PROJECT_DIR` to a new directory under `$env:TEMP` and pass that value to the
same commands. The local server binds to `127.0.0.1` by default. A project
database can contain extracted text, chunks, vectors, labels, and saved map
runs, so do not commit `.paper-galaxy/`.
