# Roadmap

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

## Phase 3: Interactive Local Web App

Goal: provide an interactive local map and document inspector.

Deliverables: local backend, interactive map UI, document inspector, cluster
panel, and search view.

Definition of done: users can open a local app, browse a map, inspect a
document, and see nearby documents without uploading data.

Non-goals: cloud hosting, account system, telemetry, and LLM chat.

## Phase 4: Better Extraction

Goal: improve corpus ingestion quality.

Deliverables: higher-quality PDF extraction, LaTeX structure extraction,
Markdown frontmatter and backlink parsing, and an OCR path for screenshots and
scanned PDFs.

Definition of done: extraction quality is measurable on a small fixture corpus
and failures are visible rather than silent.

Non-goals: default cloud OCR, AGPL/copyleft dependencies without approval, and
mandatory system services such as GROBID or Tesseract.

## Phase 5: Semantic Embeddings

Goal: add dense semantic similarity while keeping the baseline inspectable.

Deliverables: optional Sentence Transformer document embeddings, chunk
embeddings, local nearest-neighbor index, and hybrid similarity using dense
embeddings plus TF-IDF.

Definition of done: semantic mode is optional, local, documented, and compared
against the TF-IDF baseline.

Non-goals: cloud embedding APIs by default, hidden model downloads, and replacing
all explainable baselines.

## Phase 6: Explainability And Labeling

Goal: make clusters and neighbors understandable.

Deliverables: cluster labels from top terms or c-TF-IDF, manual cluster
renaming, and "why nearby?" explanations based on shared keywords or chunks.

Definition of done: map views expose the evidence behind proximity and cluster
names can be corrected by the user.

Non-goals: hallucinated labels, mandatory LLM use, and opaque similarity scores.

## Phase 7: Professionalization

Goal: make Paper Galaxy stable and extensible.

Deliverables: packaging, optional desktop shell, stable map runs, export/import,
and plugin architecture.

Definition of done: users can install, run, back up, and extend the app with
clear compatibility boundaries.

Non-goals: cloud dependency by default, telemetry, and locking user data into a
proprietary format.
