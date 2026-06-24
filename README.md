# Paper Galaxy

[English](README.md) | [简体中文](README.zh-CN.md)

[![CI](https://img.shields.io/github/actions/workflow/status/jinjianghu19823-wq/paper-galaxy/ci.yml?branch=main&label=CI)](https://github.com/jinjianghu19823-wq/paper-galaxy/actions)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-86b7ff)
![License](https://img.shields.io/badge/license-MIT-f3bd5c)
![Local-first](https://img.shields.io/badge/local--first-yes-68d8c8)

Paper Galaxy is a local-first research cartography tool for turning a personal
research corpus into an interactive map of documents, clusters, and conceptual
neighborhoods.

Current status: public alpha / Zotero Reading Graph. This repository can
scan a local sample corpus, export a self-contained offline `galaxy.html`,
index documents and chunks into local SQLite, rerun indexing incrementally,
search indexed text with SQLite FTS5, and serve a local English/Simplified
Chinese browser app with an Obsidian-inspired dynamic document graph for
browsing the indexed corpus. It
also records extraction quality reports, improves Markdown/LaTeX/PDF parsing,
supports opt-in local OCR for image files, and can optionally store local dense
document/chunk embeddings for semantic search and neighbor comparison. Cluster
labels are generated from inspectable local terms, can be manually renamed in
SQLite, and document-neighbor explanations can show shared terms and matching
chunk excerpts. It also validates local projects, persists stable map runs,
exports/imports local backup bundles, lists built-in plugin boundaries, builds
standard Python distributions, and imports Zotero Desktop libraries through the
read-only local API to build a personal Zotero Reading Graph.

Eventually, Paper Galaxy will let a user point the app at folders or later
integrations, represent each document as a point in a 2D map, place similar
documents near each other, label clusters, and explain why documents are
neighbors. The planned pipeline is:

```text
files -> extraction -> cleaning -> records -> vectors -> graph -> map -> clusters -> UI
```

Intentionally not implemented yet: UMAP as a required path, full TeX parsing,
cloud OCR, mandatory OCR system services, React, Node build tooling, desktop
packaging, cloud sync, accounts, telemetry, LLM chat, Zotero write-back,
Zotero online API sync, and mandatory LLM labeling.

Paper Galaxy is local-first by default. There is no account, no telemetry, no
automatic upload, and no cloud dependency. Generated HTML is local and offline.
The Zotero connector uses the Zotero Desktop local API, does not write to
Zotero, performs no upload, and references Zotero PDFs in place; PDFs are not
copied by default.

## Live Demo

Try the public static demo at
[jinjianghu19823-wq.github.io/paper-galaxy](https://jinjianghu19823-wq.github.io/paper-galaxy/).
The demo uses synthetic data from `examples/tiny_corpus` only. It has English
and Simplified Chinese pages, including a static graph demo that runs without a
backend.

Launch notes for the first public alpha are in
[docs/LAUNCH_NOTES.md](docs/LAUNCH_NOTES.md).

## Screenshot

The public site includes a screenshot-style preview and a live static graph
demo. Build it locally with:

```bash
python scripts/build_demo_site.py --out site_dist
python scripts/check_demo_site.py --dist site_dist
```

## Why Paper Galaxy?

- Local-first: documents, extracted text, vectors, labels, and map runs stay in
  your project by default.
- Zotero Reading Graph: import metadata, tags, collections, notes, and local PDF
  text from Zotero Desktop into a local Paper Galaxy project.
- Visual graph: browse clusters and nearest-neighbor links in a lightweight
  browser UI.
- Explainability: inspect TF-IDF terms and short chunk evidence for nearby
  documents.
- No cloud required: the app works without accounts, telemetry, upload, or a
  hosted backend.

## Quickstart

Paper Galaxy is useful in two current modes:

1. Import a Zotero Desktop library or collection into a local reading graph.
2. Index your own local folder of papers, notes, and drafts without Zotero.

In both modes, Paper Galaxy writes local project state to `.paper-galaxy/` and
opens a browser app from your own machine. It does not upload documents or run a
hosted backend.

## Install From Source

```bash
git clone https://github.com/jinjianghu19823-wq/paper-galaxy.git
cd paper-galaxy
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,ml,pdf,app]"
paper-galaxy doctor
python -m pytest
```

Optional image OCR support can be installed separately:

```bash
python -m pip install -e ".[dev,ml,pdf,app,ocr]"
```

OCR remains local and opt-in. The `ocr` extra installs Python wrappers only; a
user-installed local Tesseract binary may still be required for OCR to run.

Optional semantic embedding support can be installed separately:

```bash
python -m pip install -e ".[dev,ml,pdf,app,embeddings]"
```

Embedding commands remain local-first and explicit. `paper-galaxy embed` refuses
remote model names by default to avoid hidden downloads; pass a local Sentence
Transformer model path, or explicitly opt in with `--allow-model-download`.

If `uv` is available, it can be used as a faster local environment helper:

```bash
uv venv
source .venv/bin/activate
uv pip install -e ".[dev,ml,pdf,app]"
paper-galaxy doctor
python -m pytest
```

## Build Your First Galaxy

### Option A: Import From Zotero Desktop

Open Zotero Desktop first so its local API is available. Paper Galaxy uses the
Zotero local API at `http://localhost:23119/api/` and treats Zotero as
read-only.

```bash
paper-galaxy init .
paper-galaxy zotero detect
paper-galaxy zotero status
paper-galaxy zotero collections
paper-galaxy zotero import --project-dir . --include-pdfs --include-notes --build-reading-map
paper-galaxy serve --project-dir . --open
```

To import one collection, pass a Zotero collection name, path, or key:

```bash
paper-galaxy zotero import \
  --project-dir . \
  --collection "Dissertation" \
  --include-pdfs \
  --include-notes \
  --build-reading-map
```

Useful Zotero import options:

- `--collection "Name"` imports a specific collection.
- `--tag "tag name"` imports items with a tag.
- `--item-type journalArticle` narrows by Zotero item type.
- `--include-status read|reading|to_read|unknown|all` filters by reading status
  tags.
- `--pdf-policy extract` extracts local PDF text when possible.
- `--pdf-policy metadata` imports metadata quickly without extracting PDFs.
- `--pdf-policy skip-missing` skips items whose expected local PDFs cannot be
  read.

If a PDF is missing or unsupported, the Zotero item can still appear as a
metadata-only document with title, creators, abstract, tags, collections, notes,
and a `zotero://items/<key>` link. Imported Zotero data and extracted text live
inside `.paper-galaxy/`, so do not commit that directory.

### Option B: Import A Local Folder Without Zotero

Yes: Paper Galaxy can already index papers and notes directly from a local
folder. This is the best path if your papers are in a directory rather than a
Zotero library.

```bash
paper-galaxy init .
paper-galaxy index /path/to/your/papers --project-dir . --min-chars 40
paper-galaxy db-stats --project-dir .
paper-galaxy search "neural operator" --project-dir .
paper-galaxy serve --project-dir . --open
```

The source folder may be outside the Paper Galaxy repo. Paper Galaxy does not
copy or move your source files by default; it stores extracted text, chunks,
metadata, labels, vectors when requested, and map runs under `.paper-galaxy/`.

For a quick smoke test with the bundled synthetic corpus:

```bash
paper-galaxy init . --force
paper-galaxy index examples/tiny_corpus --project-dir . --min-chars 40
paper-galaxy serve --project-dir . --open
```

Current local-folder formats:

- `.txt`
- `.md`
- `.markdown`
- `.tex` with conservative structure extraction
- `.pdf` when the `pdf` extra has installed `pypdf`
- images such as `.png`, `.jpg`, `.webp`, and `.tiff` only when
  `--include-images --ocr` is used with local OCR dependencies

Use `paper-galaxy scan /path/to/your/papers --out galaxy.html --force` when you
want a one-file offline HTML export instead of a SQLite-backed local web app.

## CLI

```bash
paper-galaxy --help
paper-galaxy doctor
paper-galaxy init
paper-galaxy init /path/to/project
paper-galaxy scan examples/tiny_corpus --out galaxy.html --force
paper-galaxy scan examples/tiny_corpus --out galaxy.html --json-out galaxy.json --force
paper-galaxy index examples/tiny_corpus --project-dir . --min-chars 40
paper-galaxy index examples/tiny_corpus --project-dir . --min-chars 40 --extraction-report-json extraction-report.json
paper-galaxy search "neural operator" --project-dir .
paper-galaxy db-stats --project-dir .
paper-galaxy embed --project-dir . --model /path/to/local/sentence-transformer-model
paper-galaxy semantic-search "operator learning for PDEs" --project-dir . --model /path/to/local/sentence-transformer-model
paper-galaxy compare-neighbors neural_operators/fourier_neural_operator.md --project-dir . --model /path/to/local/sentence-transformer-model
paper-galaxy vector-stats --project-dir .
paper-galaxy clusters --project-dir .
paper-galaxy explain-pair neural_operators/fourier_neural_operator.md neural_operators/deep_operator_network.txt --project-dir .
paper-galaxy rename-cluster CLUSTER_SIGNATURE "Neural Operators" --project-dir .
paper-galaxy reset-cluster-label CLUSTER_SIGNATURE --project-dir .
paper-galaxy validate-project --project-dir . --json-out validation.json
paper-galaxy build-map-run --project-dir . --name "Tiny corpus map"
paper-galaxy map-runs --project-dir .
paper-galaxy show-map-run MAP_RUN_ID --project-dir .
paper-galaxy export-map-run MAP_RUN_ID --project-dir . --out map-run.json
paper-galaxy delete-map-run MAP_RUN_ID --project-dir . --yes
paper-galaxy export-project --project-dir . --out paper-galaxy-backup.zip --yes
paper-galaxy import-project paper-galaxy-backup.zip --project-dir /path/to/restore --dry-run
paper-galaxy plugins
paper-galaxy zotero detect
paper-galaxy zotero status
paper-galaxy zotero doctor --project-dir .
paper-galaxy zotero import --project-dir . --include-pdfs --include-notes --build-reading-map
paper-galaxy zotero graph --project-dir . --name "Zotero Reading Graph"
paper-galaxy serve --project-dir .
paper-galaxy extract-preview examples/tiny_corpus/neural_operators/fourier_neural_operator.md
```

`paper-galaxy init` creates `.paper-galaxy/project.toml` only. It does not scan
documents or copy corpus files.

## Zotero Reading Graph

Paper Galaxy can import a local Zotero Desktop library into the same SQLite
project state used by the local app. The primary path is Zotero Desktop's local
API at `http://localhost:23119/api/`; it is read-only for Paper Galaxy and does
not require a Zotero Web API key. Direct `zotero.sqlite` access is used only for
read-only diagnostics and path hints.

Typical workflow:

```bash
paper-galaxy init .
paper-galaxy zotero detect
paper-galaxy zotero status
paper-galaxy zotero doctor --project-dir .
paper-galaxy zotero import --project-dir . --include-pdfs --include-notes --build-reading-map
paper-galaxy serve --project-dir .
```

The import creates Paper Galaxy document records with stable `doc_zotero_*`
IDs, Zotero metadata tables, extracted chunks when local PDFs are readable, and
a saved map run named `Zotero Reading Graph`. If a PDF is missing, unsupported,
or outside the Zotero data directory, the item can still become a metadata-only
document with notes, abstract, creators, tags, collections, and a
`zotero://items/<key>` reference.

Real-library filters are intentionally explicit. `--collection` accepts a
Zotero collection key, exact name, or path; names are case-insensitive and
ambiguous matches fail with a clear error. Reading status filters accept
`all`, `read`, `reading`, `to_read`, and `unknown`; `unclassified` is accepted
as a deprecated alias for `unknown`. `--pdf-policy metadata` keeps imports fast
and metadata-only, while `--pdf-policy skip-missing` skips items whose expected
local PDF cannot be read.

Privacy boundary: the connector uses the Zotero local API, does not write to
Zotero, performs no upload, and does not copy or move PDFs by default. Imported
metadata and extracted local PDF text live inside the Paper Galaxy project
database under `.paper-galaxy/`, so do not commit that directory.

More details:

- [docs/ZOTERO_INTEGRATION.md](docs/ZOTERO_INTEGRATION.md)
- [docs/ZOTERO_REAL_WORLD_TESTING.md](docs/ZOTERO_REAL_WORLD_TESTING.md)
- [docs/READING_GRAPH.md](docs/READING_GRAPH.md)

`paper-galaxy scan` recursively scans a local folder and writes a static HTML
map. Supported Phase 1 formats are:

- `.txt`
- `.md`
- `.markdown`
- `.tex` with conservative structure extraction
- `.pdf` when optional `pypdf` is installed
- image files such as `.png`, `.jpg`, `.webp`, and `.tiff` only when
  `--include-images` is set

PDF support is basic and optional. If `pypdf` is unavailable, PDFs are skipped
with a clear reason. PDF extraction records page counts, per-page text counts,
and likely scanned/image-only warnings when text is very sparse.

Markdown extraction records YAML-style frontmatter keys and values, preserves
headings, removes fenced code blocks, and captures Markdown links plus Obsidian
wikilinks without fetching URLs. LaTeX extraction captures simple title, author,
abstract, sections, captions, labels, citations, and bibliography resources.

Image OCR is disabled by default. Use `--include-images --ocr` to attempt local
OCR on image files, and `--ocr-language TEXT` to choose the Tesseract language.
Missing OCR Python packages or a missing Tesseract binary are reported as
skips, not crashes.

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

Phase 4 adds local extraction diagnostics. Each indexing run writes compact
records to the SQLite `extraction_reports` table with method, status, character
count, warnings, and small metadata. Use `--extraction-report-json PATH` to
write a local JSON sidecar for the run. The JSON report does not include full
extracted text. Extraction options are fingerprinted so changing OCR/image
settings can reprocess files even if their content hash is unchanged.

`paper-galaxy search` uses local SQLite FTS5 over document titles, relative
paths, and extracted text. Search returns active documents by default.
`--include-missing` includes documents whose source files disappeared, while
`unindexed` documents remain hidden until a later successful indexing run makes
them active again. `paper-galaxy db-stats` reports local database counts.
Database files live under `.paper-galaxy/` and are gitignored.

`paper-galaxy embed` is the Phase 5 local semantic layer. It reads active
indexed documents and chunks, constructs transparent embedding text, stores
normalized float32 vectors in SQLite, and skips unchanged vectors using the
exact embedded text hash unless `--force` is set. Document vectors use the title
three times, the corpus-relative path once, and the first `--max-document-chars`
characters of extracted text. Chunk vectors use chunk text capped by
`--max-chunk-chars`.

`paper-galaxy semantic-search` embeds the query locally with the same model and
searches stored document or chunk vectors. It does not build vectors or download
models implicitly. `paper-galaxy compare-neighbors` shows three rankings for a
document: TF-IDF cosine neighbors, dense embedding neighbors, and a configurable
hybrid score. If vectors were built with `--no-normalize`, pass `--no-normalize`
to `semantic-search` and `compare-neighbors` so they use the matching local
model identity. `paper-galaxy vector-stats` reports registered models, vector
counts, and the last embedding run.

Phase 6 adds local explainability commands. `paper-galaxy clusters` lists
generated cluster labels, stable cluster signatures, representative documents,
and top evidence terms. `paper-galaxy rename-cluster` and
`paper-galaxy reset-cluster-label` write or remove manual label overrides in the
local SQLite database only. `paper-galaxy explain-pair SOURCE TARGET` explains
why two indexed documents are nearby using shared TF-IDF terms and matching
chunk excerpts. It does not use an LLM and does not print full extracted text.

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
canonical active documents, TF-IDF nearest neighbors, cluster labels, cluster
signatures, explanation-ready cluster metadata, and initial x/y coordinates.
The browser then runs a dependency-free vanilla JavaScript force simulation so
the map can move subtly, pan and zoom, fade unrelated nodes on hover, and keep
semantic links attached while nodes move.
Users can drag nodes to pin manual positions, double-click or use the inspector
to unpin, and use reset view or reset layout controls. Manual node positions
and graph display settings are stored only in browser `localStorage`, keyed by
the local database identity and map settings; they are not written to SQLite.
Graph labels now default to focus-only labels to avoid overlap: selected,
hovered, and direct-neighbor labels remain visible, while all-label display is
an explicit graph setting.

## Phase 7 Tools

`paper-galaxy validate-project` checks the project config, SQLite schema,
required tables, FTS table, document/vector/map-run counts, dangling rows, map
run consistency, optional dependency availability, and stale cluster label
overrides where feasible. It prints a status table and can write JSON without
full extracted text.

`paper-galaxy build-map-run` stores a deterministic snapshot of the current
TF-IDF map in SQLite. `paper-galaxy map-runs`, `show-map-run`,
`export-map-run`, and `delete-map-run` list, inspect, export, and remove those
saved snapshots. The web app includes a small selector for "Live map" versus
saved runs. Saved run coordinates are initial graph positions only; browser
dragging and pinning still stay in localStorage and are not written to SQLite.

`paper-galaxy export-project` writes a zip backup containing a manifest,
checksums, project metadata when present, and the local SQLite database when
confirmed with `--yes`. Source documents are not included by default.
`paper-galaxy import-project` validates the bundle and refuses to overwrite an
existing `.paper-galaxy/` directory unless `--force` is passed. Use `--dry-run`
to inspect planned writes.

`paper-galaxy plugins` lists built-in local extractor plugin boundaries. Phase
7 exposes only static built-ins; there is no remote plugin loading.

Packaging commands:

```bash
python -m build
make validate-example
make check
make launch-check
```

## Next Phase

The repository is public alpha. The next milestone is launch stabilization:
install feedback, extraction quality feedback, graph usability fixes, and
privacy-safe issue triage. The future personal cloud library is design-only and
not implemented; see [docs/CLOUD_LIBRARY_DESIGN.md](docs/CLOUD_LIBRARY_DESIGN.md).

There is still no cloud dependency, Zotero write-back, Zotero online sync,
desktop packaging, account system, telemetry, LLM chat, mandatory LLM labeling,
remote plugin loading, or React/Node frontend in the local app.

## Public Alpha Resources

- [Launch notes](docs/LAUNCH_NOTES.md)
- [FAQ](docs/FAQ.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Demo guide](docs/DEMO_GUIDE.md)
- [Feedback guide](docs/FEEDBACK.md)
- [Triage guide](docs/TRIAGE.md)

## Security And Privacy

Do not commit `.paper-galaxy/`, SQLite databases, generated HTML/JSON exports,
backup zip files, vector indexes, downloaded model files, or secrets. Public
issues should not include private document text. See [SECURITY.md](SECURITY.md)
([中文](SECURITY.zh-CN.md)) and [docs/PRIVACY.md](docs/PRIVACY.md)
([中文](docs/PRIVACY.zh-CN.md)).

## Roadmap And Contributing

See [docs/ROADMAP.md](docs/ROADMAP.md) for phase boundaries and
[CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidance. Chinese versions
are available at [docs/ROADMAP.zh-CN.md](docs/ROADMAP.zh-CN.md) and
[CONTRIBUTING.zh-CN.md](CONTRIBUTING.zh-CN.md).
