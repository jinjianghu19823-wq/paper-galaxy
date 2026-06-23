# Roadmap

[English](ROADMAP.md) | [简体中文](ROADMAP.zh-CN.md)

Paper Galaxy should grow incrementally. Each phase must leave the repository in
a runnable, tested state.

## Phase 0: Scaffold (complete)

Goal: create the repository foundation.

Deliverables: Python package layout, minimal CLI, tests, docs, CI, privacy
position, and agent instructions.

Definition of done: package imports, `paper-galaxy doctor` runs, `init` creates
project metadata safely, tests pass, lint passes, and typecheck passes or is
documented honestly.

Non-goals: document extraction, OCR, parsing, vectors, maps, databases, servers,
frontends, Zotero integration, packaging, cloud sync, and LLM chat.

## Phase 1: Static CLI MVP (implemented)

Goal: generate a static local galaxy map from a folder.

Deliverables: folder scanning, text extraction for simple `.txt`, `.md`, `.tex`,
and optional basic PDFs, TF-IDF vectors, 2D reduction, k-means clustering,
nearest-neighbor summaries, top-term cluster labels, optional JSON sidecar, and
a static offline `galaxy.html`.

Definition of done: `examples/tiny_corpus` produces a readable static HTML map
with hover labels, click inspection, cluster legend, top terms, skipped-file
summary, and neighbor data based on TF-IDF cosine similarity.

Non-goals: persistent database, chunk embeddings, backend server, React UI,
cloud features, and high-quality OCR.

## Phase 2: Local Database (implemented)

Goal: persist document records and support incremental local scans.

Deliverables: SQLite schema, document hashes, stable document IDs, document and
chunk tables, scan run records, missing-file tracking, incremental indexing,
and basic local FTS5 full-text search.

Definition of done: rerunning an index skips unchanged documents, updates
changed files while preserving path-based document IDs, marks removed files as
missing, marks present but currently unindexable existing files as unindexed,
writes deterministic chunks, records scan summaries, and returns useful local
search results.

Non-goals: semantic embeddings, remote sync, multi-user accounts, and desktop
packaging.

## Phase 3: Interactive Local Web App (implemented)

Goal: provide an interactive local map and document inspector.

Deliverables: local backend, Obsidian-inspired dynamic graph UI, document
inspector, cluster panel, graph controls, and search view.

Definition of done: users can open a local app, browse a map, inspect a
document, and see nearby documents without uploading data.

Non-goals: OCR, dense embeddings, Zotero integration, desktop packaging, cloud
hosting, account system, telemetry, LLM chat, and React/Node tooling unless a
later task explicitly asks for them.

## Phase 4: Better Extraction (implemented)

Goal: improve corpus ingestion quality.

Deliverables: higher-quality PDF extraction, LaTeX structure extraction,
Markdown frontmatter and backlink parsing, optional local OCR for screenshots
and images, scanned-PDF detection, extraction warnings, and persisted extraction
quality reports.

Definition of done: extraction quality is measurable on a small fixture corpus
and failures are visible rather than silent. Graph labels also default to
focus-only display so the Phase 3.1 dynamic graph does not pile labels on small
corpora.

Non-goals: default cloud OCR, AGPL/copyleft dependencies without approval, and
mandatory system services such as GROBID, Tesseract, or Poppler. Phase 4 also
does not add dense embeddings, Zotero integration, cloud sync, accounts,
desktop packaging, or React/Node tooling.

## Phase 5: Semantic Embeddings (implemented)

Goal: add dense semantic similarity while keeping the baseline inspectable.

Deliverables: optional Sentence Transformer document embeddings, chunk
embeddings, local SQLite vector storage, semantic search, vector stats, and
hybrid similarity using dense embeddings plus TF-IDF.

Definition of done: semantic mode is optional, local, documented, and compared
against the TF-IDF baseline. Remote model names are rejected by default to avoid
hidden downloads; users must provide a local model path or explicitly pass
`--allow-model-download`.

Non-goals: cloud embedding APIs by default, hidden model downloads, and replacing
all explainable baselines. Phase 5 does not add LLM chat, cloud sync, accounts,
Zotero integration, desktop packaging, or React/Node tooling.

## Phase 6: Explainability And Labeling (implemented)

Goal: make clusters and neighbors understandable.

Deliverables: c-TF-IDF-style generated cluster labels, stable cluster
signatures, representative documents, manual cluster renaming in local SQLite,
cluster metadata in `/api/map`, `/api/clusters`, and "why nearby?" explanations
based on shared terms and matching chunks.

Definition of done: map views expose the evidence behind proximity, cluster
names can be corrected by the user, the CLI exposes clusters and pair
explanations, and tests cover labels, overrides, API routes, static assets, and
Phase 5 normalization compatibility.

Non-goals: hallucinated labels, mandatory LLM use, opaque similarity scores,
cloud labeling, desktop packaging, accounts, telemetry, Zotero, or React/Node
tooling.

## Phase 7: Professionalization (implemented)

Goal: make Paper Galaxy stable and extensible.

Deliverables: Python packaging metadata, build checks, project validation,
stable saved TF-IDF map runs, local backup export/import, static built-in plugin
boundaries, and documentation for install/release/backup workflows.

Definition of done: users can build the package, validate a local project,
persist and inspect map snapshots, export/import project state, list built-in
extractor boundaries, and run the previous Phase 0-6 checks.

Non-goals: desktop packaging, cloud dependency, telemetry, accounts, cloud
sync, Zotero integration, LLM chat, mandatory LLM labeling, remote plugin
loading, React/Node tooling, and locking user data into a proprietary format.

## Public Launch Readiness (current milestone)

Goal: make the repository safe and polished enough to publish.

Deliverables: public-readiness audit, community files, issue templates, pull
request template, static GitHub Pages demo site, Simplified Chinese public site
pages, demo build/check scripts, Pages deployment workflow, and publishing
checklist.

Definition of done: the demo is generated from synthetic data only, static
assets have no external runtime dependencies, public readiness checks fail on
secrets or generated local data, and existing Phase 0-7 checks still pass.

Non-goals: cloud runtime, hosted backend, account system, document upload,
remote plugin loading, React/Node frontend tooling, or cloud sync.

## Future Personal Cloud Library (design only)

The personal cloud library is a future opt-in design, not an implementation.
The staged design starts with an encrypted backup vault, then metadata sync, and
only later considers managed compute. Local-first use must remain available
without an account.

## Phase 8+: Future Work

Future phases may improve extraction, map stability, import/export formats, or
desktop packaging only when explicitly requested. Phase 8+ remains outside the
current implementation boundary.
