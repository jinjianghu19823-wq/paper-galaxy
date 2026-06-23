# Install

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
