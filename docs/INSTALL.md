# Install

[English](INSTALL.md) | [简体中文](INSTALL.zh-CN.md)

## Try Before Installing

Open the static public demo:

```text
https://jinjianghu19823-wq.github.io/paper-galaxy/
```

The demo uses synthetic data only and includes English plus Simplified Chinese
pages. It does not run the local FastAPI app or read user documents.

## Development Install

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,ml,pdf,app]"
paper-galaxy doctor
```

This install supports scanning, indexing, TF-IDF maps, the local web app,
validation, saved map runs, and backup import/export.

## Optional Extras

```bash
python -m pip install -e ".[dev,ml,pdf,app,ocr]"
python -m pip install -e ".[dev,ml,pdf,app,embeddings]"
```

OCR remains disabled unless the user passes OCR flags. Embedding commands still
require an explicit local model path unless `--allow-model-download` is used.

## Smoke Test

```bash
paper-galaxy init . --force
paper-galaxy index examples/tiny_corpus --project-dir . --min-chars 40
paper-galaxy validate-project --project-dir .
paper-galaxy build-map-run --project-dir . --name "Tiny corpus map"
paper-galaxy serve --project-dir .
```

The local server binds to `127.0.0.1` by default. The local SQLite database can
contain extracted text, chunks, vectors, labels, and saved map runs, so do not
commit `.paper-galaxy/`.
