# Architecture

[English](ARCHITECTURE.md) | [简体中文](ARCHITECTURE.zh-CN.md)

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
- `extract`: local `.txt`, Markdown, LaTeX, optional PDF extraction, and
  optional image OCR wrappers.
- `ml.tfidf`: TF-IDF feature extraction and document top terms.
- `ml.layout`: deterministic 2D layout using TruncatedSVD with fallbacks.
- `ml.cluster`: conservative k-means clustering.
- `ml.neighbors`: nearest neighbors from TF-IDF cosine similarity.
- `ml.labels`: cluster labels from top TF-IDF terms.
- `explain.labels`: c-TF-IDF-style cluster labels, stable cluster signatures,
  representative documents, and manual label validation.
- `explain.pairs`: local pair explanations from shared terms and chunk matches.
- `explain.clusters`: cluster explanation payload helpers.
- `export.html`: self-contained offline HTML export.
- `export.json`: optional JSON sidecar export without full source text.
- `pipeline`: Phase 1 orchestration.
- `chunking`: deterministic paragraph/window text chunking.
- `records`: persistent dataclasses for documents, chunks, extraction reports,
  scan summaries, search results, and database stats.
- `storage.sqlite`: database path resolution plus explicit read-only,
  read-write, migration/bootstrap, and external read-only connection modes.
- `storage.migrations`: transactional current-schema bootstrap, ordered forward
  migrations, migration history, and pre-migration SQLite snapshots.
- `storage.json`: strict decoding for typed JSON stored in SQLite.
- `storage.repository`: explicit parameterized SQL operations.
- `indexer`: Phase 2 incremental indexing orchestration.
- `search`: local FTS search and database stats wrappers.
- `embeddings.codec`: float32 vector normalization and SQLite BLOB encoding.
- `embeddings.sentence_transformers`: strict lazy Sentence Transformers loader.
- `embeddings.builder`: local document/chunk embedding build orchestration.
- `embeddings.search`: SQLite-backed semantic search and vector stats.
- `embeddings.similarity`: TF-IDF, dense, and hybrid neighbor comparison.
- `embeddings.index`: optional local vector index path and FAISS availability
  helpers.
- `maps.runs`: persisted saved map run creation, lookup, and JSON export.
- `validation`: project health checks for config, schema, FTS, counts, dangling
  rows, optional dependencies, and map run consistency.
- `backup.snapshot`: online SQLite snapshots plus integrity/schema validation.
- `backup.archive`: strict streaming ZIP topology, limits, manifest, and
  checksum validation.
- `backup.publish`: same-filesystem atomic publication and rollback.
- `backup.staging`: private owned staging and conservative orphan cleanup.
- `backup.bundle`: portable v2 backup orchestration with strict v1 inspection.
- `storage.locking`: cross-process shared operation and exclusive maintenance
  locks, including legacy-project claim and database-handle drain.
- `plugins`: static built-in local plugin metadata.
- `zotero`: read-only local Zotero connector, normalization, attachment path
  resolution, importer, SQLite diagnostics, and reading graph builder.
- `web.server`: lazy FastAPI/Uvicorn app creation and local server startup.
- `web.api`: local JSON routes with read-only GET repositories and explicit
  writer repositories for mutation endpoints.
- `web.map_builder`: ephemeral map generation from active indexed documents.
- `web.static`: static HTML/CSS/vanilla JavaScript browser UI, including the
  Phase 3.1 dependency-free force graph renderer.

Future modules may add graph construction, better layout stability, richer
semantic map controls, and a richer local UI.

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
  -> record extraction report diagnostics
  -> chunk text
  -> upsert document metadata
  -> replace document text/chunks/FTS rows
  -> mark present but unindexable existing documents unindexed
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
- `extraction_reports`: per-run extraction method, status, warnings, character
  counts, and compact metadata.
- `embedding_models`: registered optional local embedding model identities.
- `vectors`: normalized float32 document/chunk vectors as SQLite BLOBs.
- `embedding_runs`: local embedding build run summaries.
- `vector_indexes`: optional local vector index metadata.
- `cluster_label_overrides`: local manual cluster display labels keyed by
  deterministic cluster signatures.
- `map_runs`: saved map run metadata, generation parameters, counts, warnings,
  and document-set signatures.
- `map_run_points`: saved initial point coordinates, cluster assignment, top
  terms, and nearest-neighbor summaries.
- `map_run_clusters`: saved cluster display metadata, terms, representatives,
  and warnings.
- `zotero_sources`: imported Zotero library/source identities.
- `zotero_import_runs`: local Zotero import run summaries.
- `zotero_items`: normalized Zotero top-level item metadata and reading status.
- `zotero_creators`: normalized creators for imported Zotero items.
- `zotero_collections`: collection metadata and paths.
- `zotero_item_collections`: item-to-collection membership.
- `zotero_item_tags`: imported Zotero tags.
- `zotero_attachments`: attachment metadata and local path resolution status.
- `zotero_document_links`: links between Zotero items/attachments and Paper
  Galaxy document rows.
- `documents_fts`: FTS5 table for local search.

Document status controls search visibility. `active` documents are returned by
default. `missing` documents keep their previous extracted text and FTS rows and
are returned only with `--include-missing`. `unindexed` documents represent
files that exist but currently fail extraction or minimum-length requirements;
they are hidden from search until a later successful indexing run reactivates
them.

Phase 2 prepares for Phase 3 by making project state persistent and incremental.
The static Phase 1 `scan` command remains file-based and independent.

### SQLite lifecycle and connection boundaries

Schema version 7 replaces implicit `CREATE IF NOT EXISTS` initialization with
an explicit, forward-only lifecycle:

- A new database is bootstrapped directly at the current schema in one explicit
  transaction. Bootstrap records the current migration registry in
  `schema_migrations` and commits before returning.
- The oldest supported historical schema is v6. Its fixture is reconstructed
  from repository history and upgraded only through the registered v6 -> v7
  migration. v7 adds migration history and structured `error_code` /
  `error_message` fields to scan, embedding, and Zotero import runs, plus a
  private child-version manifest for monotonic Zotero-derived documents.
- Migration takes a unique, mode-`0600` snapshot with SQLite's online backup
  API before changing schema, then verifies the snapshot with `quick_check` and
  `foreign_key_check`. It never replaces an existing backup or the live
  database/WAL sidecars.
- The migration sequence, every schema version update, and its history record
  share one `BEGIN IMMEDIATE` transaction. Any failure rolls the entire schema
  change back. Successful migration commits itself rather than relying on a
  later caller commit.
- Databases older than v6 without a supported registry path are rejected.
  Databases from a newer Paper Galaxy version are also rejected and are never
  silently downgraded or relabelled.
- A version number alone is not trusted. Bootstrap, migration, and operational
  connections verify required tables/columns, ordered primary keys, critical
  unique identities, foreign-key targets/actions, indexes, the FTS5 virtual
  table and `MATCH`, and migration-history identity.

Connection intent is explicit:

- `connect_read_only` opens an existing, current-schema project database with a
  SQLite URI in `mode=ro`, enables `query_only`, foreign-key enforcement, and a
  bounded busy timeout, and never creates a project directory, database, table,
  or schema metadata.
- `connect_read_write` opens an existing, current-schema project database for
  short transactions. Writers enable foreign keys, a bounded busy timeout,
  rollback-journal `DELETE` mode, and `synchronous=FULL`. This favors durable
  local research state and lets ordinary read-only opens avoid creating
  WAL/SHM files. The one-writer design and short commits bound lock intervals.
  Consistent backups still use SQLite's backup API rather than copying live
  database files.
- `connect_migration` is the only connection that may create a database or
  change schema. A new database file is claimed with mode `0600` before
  transactional bootstrap.
- `connect_external_read_only` opens an existing external SQLite database for
  Zotero diagnostics without creating Paper Galaxy schema or writing Zotero.

Read-only connections reject symbolic or incomplete WAL/SHM sidecars. They also
inspect the database header and refuse a clean legacy WAL-mode database whose
sidecars are absent, because opening it would create files. A Paper Galaxy
writer maintenance pass safely normalizes a project database to DELETE/FULL;
external Zotero databases are never normalized and instead ask the user to open
Zotero Desktop. When a valid active WAL and existing SHM are present, SQLite may
update SHM coordination bytes while reading; `query_only` still prevents data
or schema writes and no new directory entry is created.

Stored JSON is decoded by shape: nullable list/object fields have documented
empty defaults, while malformed JSON, scalar JSON where a container is
required, duplicate object keys, non-finite numbers, or a nested type mismatch
raises a structured
`StoredJSONError`. Repository and validation paths no longer hide persisted
corruption by silently replacing it with an empty value. Ordinary Web API
projections also exclude private raw Zotero payloads, stored local paths, and
internal configuration fields.

Project validation now runs through the read-only connection and reports:

- SQLite `quick_check` and `foreign_key_check` results;
- schema tables/columns, PK/UNIQUE/FK/index/FTS identity, and migration history;
- FTS/document/chunk/text presence and content consistency;
- orphan model/document/chunk vectors, dimensions, BLOB sizes, invalid JSON,
  stale provenance, and legacy vectors whose freshness cannot be proven; and
- invalid or lagging Zotero cursors/record versions and filter-profile
  inconsistencies, including child/collection/attachment versions and child
  manifest shape.

A check that cannot run is reported as `not_run` or `check_errors`; it is not
presented as a successful zero count.

### Backup archive and restore boundary

Backup is split across three trust boundaries rather than copying project
files directly:

1. `backup.snapshot` uses `sqlite3.Connection.backup()` to copy the committed
   database view, including active WAL pages, into a mode-`0600` staging file.
   The snapshot is normalized to rollback-journal mode and must pass
   `quick_check`, `foreign_key_check`, declared schema version, and the same
   capability registry used by operational connections.
2. `backup.archive` treats every ZIP as untrusted. It rejects duplicate or
   non-portable names, special/symlink entries, missing or extra checksum
   members, checksum duplicates, unsupported compression, excessive entry,
   expanded-size or ratio limits, and manifest/payload/schema disagreement.
   Digests and extraction are streamed with bounded chunks; path/component,
   project-config, archive-size, and free-space budgets bound memory, CPU, and
   disk consumption.
3. `backup.publish` keeps export staging beside the output and restore staging
   on the target filesystem. Export publishes one validated file with
   `os.replace`. A new restore publishes one complete directory; a forced
   restore moves overwritten files into an owner-only rollback transaction,
   publishes the configuration last, and restores original inodes on failure.
   A durable prepared/committed journal with original/new digests also supports
   recovery after process interruption rather than only in-process exceptions.

Backup holds a shared project lock across all inputs. Restore holds the
exclusive maintenance marker across interrupted-transaction recovery,
preflight, validation, and publication. Existing pre-marker connections are
drained through a legacy database-file lock; new connections consistently use
the stable build-owned marker. A sibling prepared transaction is itself an
operational connection gate until recovery. Dry-run never creates that marker.
Private staging roots carry versioned operation/target/PID ownership metadata;
later matching invocations conservatively remove dead owned orphans only.

The v2 manifest records explicit project-relative database and vector-index
destinations. Custom internal database paths round-trip. Absolute or escaping
database paths are mapped to the internal default, and project-external vector
indexes are omitted. The restore path never comes from an unchecked browser or
ZIP filename. Legacy v1 database/config backups remain readable, but their
basename-only vector indexes are not restored because their logical paths
cannot be proven.

### Short write transactions and run auditing

Indexing performs discovery, file metadata reads, hashing, extraction, and
chunk preparation outside SQLite write transactions. Each unchanged touch,
file result, extraction diagnostic, or document/chunk replacement is committed
in a short atomic unit. A single extraction failure is recorded for that file
and does not poison the whole corpus run; an orchestration or sidecar-output
failure marks the run `failed` (or `interrupted` for interruption) with a safe,
bounded error code/message instead of leaving it permanently `running`.

Embedding inference and vector encoding also happen outside the write
transaction. Validated vector records are committed in bounded batches, and
the audit row records only successfully committed progress if a later batch
fails. Replacing or deactivating a document removes its document/chunk vectors
and invalidates affected vector-index metadata; an identical Zotero sync keeps
unchanged chunks and vectors intact. Zotero network fetching, normalization,
and PDF extraction do not occupy
one long writer transaction: the run is registered before fetching, item
changes commit in short units, and the source cursor advances only in the final
successful transaction. Fetch, normalization, item, or process-interruption
failures are audited; a post-import reading-map failure is a retryable warning
and does not rewrite the completed sync as failed. Regressive, divergent, or
partial parent/collection/attachment/note/annotation responses fail and roll
back before cursor advance. Migrated v6 child state is deliberately unknown;
an explicit `--force` full-child reconciliation establishes its first manifest.
Omitted children are not treated as deletions until the later incremental
Zotero checkpoint adds explicit tombstone reconciliation.

Saved-map API and export payloads share a domain-layer deep whitelist for run,
point, neighbor, term, representative, and cluster fields. Legacy nested raw
JSON, absolute paths, internal metadata, and raw warnings cannot cross those
ordinary boundaries.

## Phase 3 Local Web App

Phase 3 adds `paper-galaxy serve`, which starts a local FastAPI app with
Uvicorn. FastAPI and Uvicorn remain optional dependencies under the `app` extra,
and they are imported lazily so the base package can still be imported without
app dependencies.

The local app architecture is:

```text
paper-galaxy serve
  -> FastAPI local backend on 127.0.0.1 by default
  -> static HTML/CSS/vanilla JS frontend
  -> explicit SQLite read/write repository APIs
  -> ephemeral map builder using TF-IDF helpers
```

The browser app communicates only with the local backend. Static assets are
served from the Python package and do not reference CDNs, remote fonts, or
external images.

API endpoints:

- `GET /api/health`: app status and non-sensitive project/database availability
  flags.
- `GET /api/config`: read-only runtime app configuration.
- `GET /api/stats`: Phase 2 database counts.
- `GET /api/vector-stats`: Phase 5 embedding model and vector counts.
- `GET /api/search`: local SQLite FTS search.
- `GET /api/documents`: document metadata lists.
- `GET /api/documents/{document_id}`: safe metadata, corpus-relative identity,
  chunk previews, and text preview.
- `GET /api/map`: active document map points, cluster labels, nearest neighbors,
  cluster explanation metadata, stats, and warnings.
- `GET /api/map?run_id=...`: saved map run payload with persisted points and
  clusters.
- `GET /api/map-runs`: saved map run metadata list.
- `GET /api/map-runs/{run_id}`: saved map run payload.
- `DELETE /api/map-runs/{run_id}`: remove a saved map run.
- `GET /api/clusters`: generated/manual cluster labels, signatures, top terms,
  representatives, sizes, and warnings.
- `PUT /api/clusters/{cluster_signature}/label`: save a manual local label
  override.
- `DELETE /api/clusters/{cluster_signature}/label`: remove a manual local label
  override.
- `GET /api/explain/pair`: explain one document pair with shared terms and
  chunk excerpts.
- `GET /api/zotero/status`: imported Zotero counts, latest run, and warnings.
- `GET /api/zotero/items`: imported Zotero item list with status, tags, and
  collections.
- `GET /api/zotero/item/{zotero_item_id}`: imported Zotero item detail.
- `GET /api/zotero/reading-map`: live Zotero reading map payload from imported
  local records.

All Web GET paths use read-only connections and cannot bootstrap or migrate a
database. Missing, locked, corrupt, migration-required, and future-schema
states are returned as structured safe errors. Normal health/config and data
responses do not expose absolute project, database, source, attachment, or
model paths; detailed local paths remain available to explicit CLI diagnostics.

Map generation reads active indexed records and extracted text from SQLite. It
does not re-extract source files. It reuses the Phase 1 TF-IDF, layout,
clustering, label, top-term, and neighbor helpers. Nearest neighbors are
computed from high-dimensional TF-IDF cosine similarity, not from 2D map
distance.

Phase 3.1 keeps `/api/map` as the canonical source of semantic graph data:
documents, initial x/y coordinates, cluster labels, and nearest-neighbor lists.
The frontend derives SVG links from each point's `nearest_neighbors` entries and
does not recompute document similarity in the browser.

The browser-side `web.static/graph.js` force graph then treats the API x/y
coordinates as initial positions only. It runs a local `requestAnimationFrame`
simulation with center, repulsion, link spring, collision, damping, and subtle
pointer forces. It supports hover focus, click selection, drag-to-pin nodes,
background pan, mouse-wheel zoom, reset view, reset layout, and force/display
settings. SVG elements are created on data load and updated in place on each
tick.

Manual layout is frontend-only state. Dragged or pinned node positions and
graph display settings are stored in browser `localStorage`, keyed by a
non-sensitive local project namespace and map settings. Graph movement does not
mutate SQLite,
create map runs, or change the indexed corpus.

Missing and unindexed documents are excluded from the default map. Search can
include missing documents when explicitly requested, but unindexed documents
remain hidden from search until a successful indexing run reactivates them.

If the database is missing, `/api/health` still works and the app returns
structured empty states with the indexing command instead of a traceback. If the
database exists but has no active documents, map endpoints return empty arrays
and warnings.

## Zotero Reading Graph

Zotero support adds a read-only connector from Zotero Desktop into Paper Galaxy
project state:

```text
Zotero Desktop local API
  -> normalize Zotero items, collections, notes, annotations, and attachments
  -> resolve local attachment paths without copying PDFs
  -> upsert Zotero metadata and Paper Galaxy documents
  -> chunk/index imported text
  -> build a saved "Zotero Reading Graph" map run
  -> local web app Zotero graph source
```

The local API is the primary path. Direct `zotero.sqlite` access is used only
for read-only diagnostics and path hints. Paper Galaxy does not write to Zotero,
does not upload Zotero data, and does not copy PDFs by default.

Imported Zotero items use stable IDs derived from the source and Zotero key.
Metadata-only items use `zotero://items/<key>` as their document path. Items
with readable local PDFs can store extracted PDF text and chunks in the Paper
Galaxy SQLite database, but the original PDF file stays in place.

The real-library beta adds no-write local readiness checks through
`paper-galaxy zotero doctor`, explicit collection resolution by key/name/path,
validated reading-status filters, local user-library validation, and explicit
PDF policies for extraction, metadata-only imports, or skipping missing PDFs.

The reading graph uses the same high-dimensional TF-IDF similarity, layout,
cluster label, nearest-neighbor, and saved-map infrastructure as the document
map. Reading status is inferred locally from tags, and the browser UI can
filter by status, tag, and collection.

## Phase 4 Extraction Quality

Phase 4 extends the extractor result model with defaulted fields:

- `method`: extractor path such as `markdown`, `latex`, `pdf-pypdf`, or
  `image-ocr-tesseract`.
- `warnings`: non-fatal extraction concerns.
- `metadata`: compact JSON-serializable quality and structure hints.
- `sections`: headings or section names.
- `links`: Markdown wikilinks, Markdown link targets, citation keys, or
  bibliography references.

The scanner still discovers text, Markdown, LaTeX, and PDF files by default.
Image extensions are discovered only when `include_images` is enabled. OCR is
attempted only when `ocr` is enabled. The OCR wrapper imports Pillow and
pytesseract lazily and checks for the local `tesseract` binary before running,
so normal installs and CI do not require OCR system services.

The `indexer` stores one `extraction_reports` row per discovered file in a run.
Rows contain method, status, character count, warnings JSON, compact metadata
JSON, and foreign keys to the scan run and document when available. Statuses
include `extracted`, `skipped`, `failed`, `unindexed`, `ocr_unavailable`, and
`scanned_pdf_candidate`.

Extraction options are included in a small fingerprint stored in report
metadata. If an unchanged file was last processed with different material
options, such as enabling image OCR, indexing re-extracts it instead of relying
only on the source hash.

`paper-galaxy index --extraction-report-json PATH` writes an optional local
sidecar report with counts and per-file diagnostics. It intentionally omits
full extracted text.

Markdown extraction keeps frontmatter values searchable, records frontmatter
metadata keys, preserves headings, strips fenced code blocks, captures Obsidian
wikilinks and Markdown links, and never fetches URLs. LaTeX extraction remains a
heuristic pass, not a full TeX parser, but captures simple title, author,
abstract, sections, captions, labels, citations, and bibliography resources.
PDF extraction remains based on optional `pypdf`; it records page count,
per-page character counts, metadata-title source, encrypted-PDF warnings, page
failures, and likely scanned/image-only PDF warnings.

The Phase 3.1 graph label policy is also tightened in Phase 4. Labels are
focus-only by default: selected, hovered, and direct-neighbor labels are visible.
High-zoom or always-on labels are explicit UI settings and use a simple budget
plus bounding-box collision skip to reduce overlap.

## Phase 5 Semantic Embeddings

Phase 5 adds an optional local dense-vector layer without replacing the TF-IDF
baseline. The normal install remains `.[dev,ml,pdf,app]`; embedding commands
require the optional `embeddings` extra.

The embedding flow is:

```text
paper-galaxy embed
  -> load explicit local Sentence Transformer model path
  -> read active documents/chunks from SQLite
  -> construct transparent embedding text
  -> encode batches locally
  -> normalize vectors for cosine similarity
  -> store float32 BLOB vectors with text hashes
  -> record embedding run counts
```

Model loading is intentionally strict. `--model PATH_OR_NAME` loads a local path
when it exists. A non-local name is refused by default with an explanation that
remote or cached model names are disabled to avoid hidden downloads. Only
`--allow-model-download` lets Sentence Transformers resolve or download a model
name.

Document embedding text repeats the title three times, includes the
corpus-relative path once, and appends the first configured slice of extracted
text. Chunk embeddings use chunk text directly. Missing and unindexed documents
are not embedded by default.

`paper-galaxy semantic-search` embeds the query locally and computes cosine
similarity against stored vectors in SQLite. It does not build vectors
implicitly and does not require FAISS for correctness. `paper-galaxy
compare-neighbors` computes TF-IDF neighbors from the inspectable baseline,
dense neighbors from stored vectors, and hybrid neighbors with configurable
weights. `/api/vector-stats` exposes model and vector counts to the local web
app without loading embedding models.

## Phase 6 Explainability And Labeling

Phase 6 adds an explainability layer without adding remote calls or LLM
labeling. Cluster labels are generated from active indexed documents using a
c-TF-IDF-style term pass over actual local text. Generic academic filler terms
are filtered, top evidence terms and representatives are exposed, and each
cluster receives a deterministic signature from sorted active document IDs.

Manual cluster labels are local display overrides. They are stored in
`cluster_label_overrides` by cluster signature and change only the display
label. Generated labels, top terms, representatives, and document membership
remain available for inspection. Manual graph layout remains browser
`localStorage`; only cluster label overrides are written to SQLite.

Pair explanations answer "why nearby?" with a transparent baseline:

```text
source + target documents
  -> TF-IDF shared term scores
  -> chunk-level cosine matches
  -> short source/target excerpts
  -> JSON-safe explanation payload
```

The pair endpoint and CLI do not return full extracted document text. Dense pair
evidence can be added later, but Phase 6 completion does not require loading
embedding models.

## Phase 7 Professionalization

Phase 7 keeps the application local-first while adding operational boundaries.

Validation is implemented in `validation.validate_project`. It checks the
configured project directory, schema version, required tables, FTS readability,
core row counts, dangling chunks/texts/vectors/map points, optional dependency
availability, saved map run count consistency, and stale cluster overrides when
a live map can be generated. Reports contain counts and issues, not full
extracted text.

Saved map runs use the existing TF-IDF map builder as the canonical generator.
`maps.runs.build_and_store_map_run` stores map metadata in `map_runs`, points in
`map_run_points`, and cluster snapshots in `map_run_clusters`. Dense or hybrid
saved map runs are rejected in Phase 7 rather than silently changing semantics.
The web app can display a saved run through `run_id`, but browser movement and
manual node positions remain localStorage-only UI state.

Backup bundles are zip files with `manifest.json`, `checksums.sha256`,
`README_EXPORT.txt`, optional `project.toml`, and an explicitly confirmed
SQLite database export. They do not include source documents by default. Import
validates the bundle, supports dry runs, and refuses to overwrite an existing
metadata directory without `--force`.

The plugin module is a static registry of built-in extractor boundaries. It is
metadata only in Phase 7 and intentionally does not load third-party, remote, or
user-provided code.

## Public Static Demo Site

The public launch site is a static GitHub Pages artifact generated from
committed `site/` source files. It does not run FastAPI and does not require
Node, npm, React, Vite, external fonts, analytics, or remote runtime assets.

```text
examples/tiny_corpus
  -> scripts/build_demo_site.py
  -> site_dist/data/tiny-map.json
site/
  -> site_dist/
  -> GitHub Pages artifact
```

`scripts/build_demo_site.py` indexes the synthetic tiny corpus in a temporary
project directory, builds the same TF-IDF map payload used by the local web app,
adds short precomputed pair explanations, strips local database paths, replaces
machine-dependent document, chunk, and cluster IDs with namespaced demo IDs,
and writes a JSON payload without full source text. Default builds write only
inside `site_dist/`; refreshing the committed `site/data/tiny-map.json` fixture
requires the explicit `--refresh-source-data` flag and never runs in CI, Pages,
or release targets.

`scripts/check_demo_site.py` verifies the generated static site, demo JSON
shape, local asset policy, absence of absolute paths, absence of full source
text, English and Simplified Chinese pages, and optional local HTTP serving.

GitHub Pages deployment is handled by `.github/workflows/pages.yml`. Repository
Pages settings still need Source = GitHub Actions.

## Public Readiness Audit

`scripts/public_readiness_check.py` checks for generated local data, likely
secrets, downloaded model files, large accidental binaries, required community
files, static demo asset policy, README launch content, and package metadata.
It can write `public-readiness.json` and exits nonzero on blockers in strict
mode.

Post-public launch activation adds `scripts/check_live_site.py` for deployed
GitHub Pages verification and `scripts/launch_report.py` for a concise local
launch report. These scripts inspect static pages and repository state only;
they do not add analytics, telemetry, cloud runtime, or hosted backend code.

## Future Cloud Library Boundary

The cloud library docs under `docs/cloud-library/` are design-only. They do not
add cloud runtime modules, hosted auth, sync workers, storage SDKs, payment
code, telemetry, or background upload. The local architecture remains useful
without accounts or cloud services.

## Future Data Model Sketch

- `documents`: one row per source document.
- `chunks`: text chunks associated with documents.
- `clusters`: cluster metadata and labels.
- `document_clusters`: many-to-many document to cluster assignments.
- `edges`: similarity graph edges with scores and provenance.
- `map_runs`: layout runs, parameters, seeds, and output coordinates.

## Local Data Boundary

Project metadata lives under `.paper-galaxy/`. Future database files should also
live there by default, with paths made explicit in project configuration.
