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
