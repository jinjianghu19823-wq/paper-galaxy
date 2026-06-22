# Paper Galaxy

Paper Galaxy is a local-first research cartography tool for turning a personal
research corpus into an interactive map of documents, clusters, and conceptual
neighborhoods.

Current status: Phase 3 local interactive web app. This repository can scan a
local sample corpus, export a self-contained offline `galaxy.html`, index
documents and chunks into local SQLite, rerun indexing incrementally, search
indexed text with SQLite FTS5, and serve a local browser app with an
Obsidian-inspired dynamic document graph for browsing the indexed corpus.

Eventually, Paper Galaxy will let a user point the app at folders or a Zotero
library, represent each document as a point in a 2D map, place similar documents
near each other, label clusters, and explain why documents are neighbors. The
planned pipeline is:

```text
files -> extraction -> cleaning -> records -> vectors -> graph -> map -> clusters -> UI
```

Intentionally not implemented yet: OCR, full LaTeX parsing, dense embeddings,
UMAP as a required path, React, Node build tooling, Zotero integration, desktop
packaging, cloud sync, accounts, telemetry, and LLM chat.

Paper Galaxy is local-first by default. There is no account, no telemetry, no
automatic upload, and no cloud dependency. Generated HTML is local and offline.

## Development Setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,ml,pdf,app]"
paper-galaxy doctor
python -m pytest
```

If `uv` is available, it can be used as a faster local environment helper:

```bash
uv venv
source .venv/bin/activate
uv pip install -e ".[dev,ml,pdf,app]"
paper-galaxy doctor
python -m pytest
```

## CLI

```bash
paper-galaxy --help
paper-galaxy doctor
paper-galaxy init
paper-galaxy init /path/to/project
paper-galaxy scan examples/tiny_corpus --out galaxy.html --force
paper-galaxy scan examples/tiny_corpus --out galaxy.html --json-out galaxy.json --force
paper-galaxy index examples/tiny_corpus --project-dir . --min-chars 40
paper-galaxy search "neural operator" --project-dir .
paper-galaxy db-stats --project-dir .
paper-galaxy serve --project-dir .
```

`paper-galaxy init` creates `.paper-galaxy/project.toml` only. It does not scan
documents or copy corpus files.

`paper-galaxy scan` recursively scans a local folder and writes a static HTML
map. Supported Phase 1 formats are:

- `.txt`
- `.md`
- `.markdown`
- `.tex` with conservative command-stripping heuristics
- `.pdf` when optional `pypdf` is installed

PDF support is basic and optional. If `pypdf` is unavailable, PDFs are skipped
with a clear reason. OCR is not implemented in Phase 1.

Nearest neighbors are computed from high-dimensional TF-IDF cosine similarity,
not from visual distance in the 2D map. The 2D layout is only a view.

`paper-galaxy index` persists extracted text and deterministic chunks in a local
SQLite database under `.paper-galaxy/paper_galaxy.sqlite3` by default. It uses
SHA-256 hashes to skip unchanged files, preserves document IDs when a file keeps
the same corpus-relative path, and marks removed files as `missing` rather than
deleting their rows. If an existing file is present but cannot currently be
indexed, for example because extraction fails or the extracted text is shorter
than `--min-chars`, it is marked `unindexed` instead of being left active with
stale search content.

`paper-galaxy search` uses local SQLite FTS5 over document titles, relative
paths, and extracted text. Search returns active documents by default.
`--include-missing` includes documents whose source files disappeared, while
`unindexed` documents remain hidden until a later successful indexing run makes
them active again. `paper-galaxy db-stats` reports local database counts.
Database files live under `.paper-galaxy/` and are gitignored.

`paper-galaxy serve` starts the Phase 3 local web app on `127.0.0.1` by default.
Install app dependencies with:

```bash
python -m pip install -e ".[dev,ml,pdf,app]"
```

Typical local app usage is:

```bash
paper-galaxy init .
paper-galaxy index examples/tiny_corpus --project-dir . --min-chars 40
paper-galaxy serve --project-dir .
```

The app reads the local SQLite database and serves static HTML/CSS/JavaScript
with no CDN assets. It does not upload documents, collect telemetry, or run
indexing from the browser UI. Indexing remains a CLI command.

The local app graph is dynamic in Phase 3.1. `/api/map` still provides the
canonical active documents, TF-IDF nearest neighbors, cluster labels, and
initial x/y coordinates. The browser then runs a dependency-free vanilla
JavaScript force simulation so the map can move subtly, pan and zoom, fade
unrelated nodes on hover, and keep semantic links attached while nodes move.
Users can drag nodes to pin manual positions, double-click or use the inspector
to unpin, and use reset view or reset layout controls. Manual node positions
and graph display settings are stored only in browser `localStorage`, keyed by
the local database identity and map settings; they are not written to SQLite.

## Next Phase

The next planned implementation phase is Phase 4: better extraction quality.
There is still no cloud dependency, OCR, dense embeddings, Zotero integration,
desktop packaging, account system, telemetry, or React/Node frontend in Phase 3.
