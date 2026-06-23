# Architectural Decisions

## ADR 0001: Python-First Backend

Paper Galaxy starts as a Python-first project because the first useful work is
local scanning, extraction, vectorization, clustering, and file-based export.

## ADR 0002: Local-First Default

User documents stay on the user's machine by default. Runtime code must not add
cloud calls unless a future task explicitly asks for them.

## ADR 0003: SQLite Planned For Phase 2

SQLite is planned as the first persistent store because it is local, portable,
inspectable, and sufficient for an incremental single-user research corpus.

## ADR 0004: TF-IDF Before Dense Embeddings

The first similarity baseline should be TF-IDF because it is simple,
inspectable, deterministic, and useful for explaining why documents are nearby.
Dense embeddings can be added later as an optional semantic layer.

## ADR 0005: Optional Heavy Dependencies

Default runtime dependencies stay light. Heavy or specialized packages belong in
optional extras so Phase 0 and simple CLI usage remain easy to install.

## ADR 0006: No Telemetry

Paper Galaxy should not collect telemetry. Any future diagnostics or usage
collection would need an explicit opt-in design and a separate decision.

## ADR 0007: No Cloud Dependency

The app should be useful offline. Future cloud sync, hosted inference, or shared
workspaces must be optional and opt-in.

## ADR 0008: Avoid Licensing Traps

Do not add AGPL/copyleft dependencies without explicit approval. The MIT license
may need reconsideration if future optional dependencies introduce incompatible
licensing constraints.

## ADR 0009: Static HTML Export For Phase 1

Phase 1 exports a single self-contained HTML file with inline CSS, inline
JavaScript, and embedded metadata. It does not load external assets, CDNs,
fonts, or scripts. This keeps the MVP offline, inspectable, and easy to share as
a local artifact.

## ADR 0010: TF-IDF, TruncatedSVD, And KMeans Baseline

Phase 1 uses scikit-learn TF-IDF, cosine similarity, TruncatedSVD, and k-means
as the first inspectable map pipeline. TF-IDF terms support simple cluster
labels and "why nearby?" explanations before denser semantic embeddings are
introduced.

## ADR 0011: Optional PDF Support Via pypdf

Basic PDF extraction uses optional `pypdf` only. If `pypdf` is unavailable, PDF
files are skipped with a clear reason. OCR, PyMuPDF, GROBID, and Tesseract are
not part of Phase 1.

## ADR 0012: Standard Library sqlite3 Instead Of An ORM

Phase 2 uses Python's standard library `sqlite3` with explicit SQL. This keeps
the local persistence layer lightweight, inspectable, and dependency-free while
the schema is still small.

## ADR 0013: SQLite FTS5 For Basic Local Search

Phase 2 uses SQLite FTS5 over titles, relative paths, and extracted text. This
provides offline full-text search without a server, cloud service, or separate
search dependency.

## ADR 0014: Stable IDs From Corpus And Relative Path

Corpus IDs are derived from the resolved corpus root path. Document IDs are
derived from corpus ID plus corpus-relative path, so editing a file preserves
the document ID while changing its content hash and updated metadata.

## ADR 0015: Missing Files Are Marked, Not Deleted

When a previously indexed file disappears, Phase 2 marks its document row as
`missing` instead of deleting it. This preserves history and lets future app
views decide how to display or recover missing documents.

Missing documents keep their previous extracted text, chunks, and FTS rows so
`paper-galaxy search --include-missing` can still find them. Default search
filters to `active` documents only.

## ADR 0016: Present But Currently Unindexable Files Are Unindexed

If a previously indexed file still exists but cannot currently be indexed, such
as when extraction fails or the text falls below the configured minimum length,
Phase 2 marks its document row as `unindexed`. This avoids returning stale
search results while preserving the stable document ID and prior local records
for later recovery.

## ADR 0017: FastAPI And Uvicorn As Optional App Extra

Phase 3 uses FastAPI and Uvicorn for the local browser app. They remain in the
optional `app` dependency extra, and server modules import them lazily so the
default install stays light.

## ADR 0018: Static Vanilla JavaScript Frontend

Phase 3 serves static HTML, CSS, and vanilla JavaScript from the Python package.
There is no React, Node, npm, Vite, CDN, remote font, or frontend build step.

## ADR 0019: Localhost Binding By Default

`paper-galaxy serve` binds to `127.0.0.1` by default. If a user chooses a
non-loopback host, the CLI prints a local-network exposure warning.

## ADR 0020: Phase 3 Web App Is Read-Only

The Phase 3 browser app reads the local SQLite database and does not mutate the
corpus or run indexing. Indexing remains CLI-driven through `paper-galaxy
index`.

## ADR 0021: Phase 3.1 Dependency-Free Force Graph

Phase 3.1 implements the interactive graph as static vanilla JavaScript instead
of adding D3, React, Node, npm, Vite, or a frontend build step. This preserves
the Phase 3 local package shape: Python serves packaged HTML/CSS/JavaScript and
the browser app remains usable without network access or external assets.

## ADR 0022: Manual Layout Stays In Browser Local Storage

Dragging and pinning graph nodes is a view preference, not indexed project
data. Phase 3.1 stores manual node positions and graph display settings in
browser `localStorage`, keyed by local database identity and map settings. It
does not write manual positions to SQLite or introduce persistent `map_runs`.

## ADR 0023: Graph Interactions Are Frontend-Only

Hover focus, pan/zoom, drag/pin, reset layout, and force settings are
frontend-only interactions. The backend continues to provide read-only
documents, cluster labels, initial coordinates, and TF-IDF nearest-neighbor
links through `/api/map`; the browser does not recompute similarity from 2D map
distance.

## ADR 0024: Phase 4 Stores Extraction Quality Reports In SQLite

Phase 4 records compact extraction diagnostics in an `extraction_reports` table.
Reports store method, status, character count, warnings, and compact metadata,
but not full extracted text. This makes skips and low-quality extraction visible
without changing search visibility semantics.

## ADR 0025: OCR Is Optional, Local, And Disabled By Default

Image OCR is available only when image discovery and OCR are explicitly enabled.
The Python OCR wrappers live in the optional `ocr` extra, and the local
Tesseract binary is detected at runtime. Missing OCR dependencies produce
warnings or skip records instead of failing the whole indexing run.

## ADR 0026: PyMuPDF Is Not Used In Phase 4

Phase 4 keeps PDF extraction on optional `pypdf`. PyMuPDF is not added because
the project is avoiding licensing ambiguity and heavier parser dependencies
unless explicitly approved later.

## ADR 0027: Graph Labels Default To Focus-Only

The dynamic graph defaults to labels for selected, hovered, and direct-neighbor
nodes only. High-zoom or always-on labels are explicit settings and use simple
overlap prevention. This avoids visual clutter on small corpora while preserving
inspectable focus context.

## ADR 0028: Phase 4 Keeps The Static Vanilla Frontend

The extraction and graph-label improvements do not add React, Node, npm, Vite,
remote assets, or a frontend build step. Static HTML/CSS/vanilla JavaScript
remains the browser surface.

## ADR 0029: Phase 5 Embeddings Are Optional And Local

Dense embeddings live behind the optional `embeddings` extra. The default
development/app install remains useful with TF-IDF, SQLite FTS, and the local
web app even when Sentence Transformers and FAISS are unavailable.

## ADR 0030: No Hidden Model Downloads

Sentence Transformers can resolve or download remote model names. Phase 5
rejects non-local model names by default and requires either a local model path
or an explicit `--allow-model-download` opt-in. Tests use fake encoders and do
not download models.

## ADR 0031: Store Vectors As SQLite Float32 BLOBs

Phase 5 stores normalized float32 document/chunk vectors in SQLite with explicit
dimension, dtype, model ID, object ID, and embedded-text SHA-256 metadata. JSON
is used for compact metadata, not vector payloads.

## ADR 0032: Semantic Workflows Stay CLI-Driven

Embedding generation remains a CLI command. The local web API exposes vector
stats without loading models or generating embeddings. Semantic map switching
can build on the stored vector layer later without adding cloud calls or a
frontend build step.
