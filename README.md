# Paper Galaxy

Paper Galaxy is a local-first research cartography tool for turning a personal
research corpus into an interactive map of documents, clusters, and conceptual
neighborhoods.

Current status: Phase 0 scaffold only. This repository contains the Python
package structure, a minimal CLI, documentation, tests, and CI configuration.

Eventually, Paper Galaxy will let a user point the app at folders or a Zotero
library, represent each document as a point in a 2D map, place similar documents
near each other, label clusters, and explain why documents are neighbors. The
planned pipeline is:

```text
files -> extraction -> cleaning -> records -> vectors -> graph -> map -> clusters -> UI
```

Intentionally not implemented yet: PDF parsing, OCR, LaTeX parsing, TF-IDF,
embeddings, UMAP, k-means, nearest-neighbor search, SQLite storage, FastAPI,
React, Zotero integration, desktop packaging, cloud sync, and LLM chat.

Paper Galaxy is local-first by default. There is no account, no telemetry, no
automatic upload, and no cloud dependency in this scaffold.

## Development Setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
paper-galaxy doctor
python -m pytest
```

If `uv` is available, it can be used as a faster local environment helper:

```bash
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
paper-galaxy doctor
python -m pytest
```

## CLI

```bash
paper-galaxy --help
paper-galaxy doctor
paper-galaxy init
paper-galaxy init /path/to/project
```

`paper-galaxy init` creates `.paper-galaxy/project.toml` only. It does not scan
documents or copy corpus files.

## Next Phase

The next planned implementation phase is Phase 1: a static CLI MVP that scans a
folder, extracts text from simple local formats, computes a TF-IDF baseline,
reduces documents to 2D, and writes a static `galaxy.html`.
