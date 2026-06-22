# Architecture

Paper Galaxy is planned as a local-first Python application with optional
heavier capabilities added phase by phase.

## Pipeline

```text
files
  -> extraction
  -> cleaning
  -> document and chunk records
  -> vectors
  -> similarity graph
  -> 2D projection
  -> clusters
  -> cluster labels
  -> interactive UI
```

The 2D layout is a view. High-dimensional similarity remains the canonical
source for nearest-neighbor search and proximity explanations.

## Modules

- `cli`: command-line entrypoints.
- `config`: project and runtime configuration models.
- `paths`: project path and metadata helpers.
- `logging`: shared console and logging setup.
- `models`: dataclasses shared by the Phase 1 pipeline.
- `ingest.scanner`: deterministic recursive local file discovery.
- `extract`: simple local `.txt`, Markdown, LaTeX, and optional PDF extraction.
- `ml.tfidf`: TF-IDF feature extraction and document top terms.
- `ml.layout`: deterministic 2D layout using TruncatedSVD with fallbacks.
- `ml.cluster`: conservative k-means clustering.
- `ml.neighbors`: nearest neighbors from TF-IDF cosine similarity.
- `ml.labels`: cluster labels from top TF-IDF terms.
- `export.html`: self-contained offline HTML export.
- `export.json`: optional JSON sidecar export without full source text.
- `pipeline`: Phase 1 orchestration.
- `chunking`: deterministic paragraph/window text chunking.
- `records`: persistent dataclasses for documents, chunks, scan summaries,
  search results, and database stats.
- `storage.sqlite`: database path resolution and SQLite connection setup.
- `storage.migrations`: idempotent schema initialization.
- `storage.repository`: explicit parameterized SQL operations.
- `indexer`: Phase 2 incremental indexing orchestration.
- `search`: local FTS search and database stats wrappers.

Future modules may add persistent records, graph construction, better layout
stability, and a richer local UI.

## Phase 1 Static Export

Phase 1 does not run a server and does not require frontend build tooling. The
CLI writes a single HTML file with inline CSS, inline JavaScript, and embedded
metadata. The HTML contains titles, relative paths, file types, coordinates,
cluster labels, top terms, nearest-neighbor summaries, and skipped-file reasons.
It does not embed full extracted source text.

Nearest neighbors come from high-dimensional TF-IDF cosine similarity. The 2D
coordinates are an exploratory view only, so visual closeness should not be used
as the canonical neighbor definition.

## Phase 2 Local Storage

Phase 2 adds a local SQLite database under `.paper-galaxy/` by default. The
indexing flow is:

```text
resolve project database
  -> initialize schema
  -> discover corpus files
  -> hash each file
  -> skip unchanged documents
  -> extract changed/new text
  -> chunk text
  -> upsert document metadata
  -> replace document text/chunks/FTS rows
  -> mark unseen active documents missing
  -> finish scan run summary
```

The storage layer uses standard library `sqlite3`, foreign keys, explicit SQL,
and SQLite FTS5. It does not run a server and does not add an ORM.

Schema overview:

- `schema_meta`: schema version.
- `corpora`: indexed corpus roots.
- `scan_runs`: one row per indexing run.
- `documents`: stable path-based document records and hashes.
- `document_texts`: local extracted full text.
- `chunks`: deterministic text chunks for future app views.
- `skipped_files`: per-run skipped files and reasons.
- `documents_fts`: FTS5 table for local search.

Phase 2 prepares for Phase 3 by making project state persistent and incremental.
The static Phase 1 `scan` command remains file-based and independent.

## Future Data Model Sketch

- `documents`: one row per source document.
- `chunks`: text chunks associated with documents.
- `vectors`: vector records for documents or chunks.
- `clusters`: cluster metadata and labels.
- `document_clusters`: many-to-many document to cluster assignments.
- `edges`: similarity graph edges with scores and provenance.
- `map_runs`: layout runs, parameters, seeds, and output coordinates.

## Local Data Boundary

Project metadata lives under `.paper-galaxy/`. Future database files should also
live there by default, with paths made explicit in project configuration.
