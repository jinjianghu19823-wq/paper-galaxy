# Paper Galaxy v0.1.0 Public Alpha Launch Notes

[English](LAUNCH_NOTES.md) | [简体中文](LAUNCH_NOTES.zh-CN.md)

Paper Galaxy is a local-first research cartography tool. It turns a folder of
papers, notes, Markdown, LaTeX, PDFs, and related research files into a local
map of documents, clusters, and explainable neighbors.

## What Works Now

- Static offline `paper-galaxy scan` HTML exports.
- Incremental local SQLite indexing with document and chunk records.
- Local SQLite FTS5 search.
- Local browser app with an interactive document graph.
- English and Simplified Chinese UI text for the local app and public site.
- Extraction reports for Markdown, LaTeX, PDFs, text, and optional image OCR.
- Optional local dense embeddings and semantic search.
- Cluster labels, manual local label overrides, and pair explanations.
- Project validation, saved TF-IDF map runs, backup export/import, and package
  build checks.
- Static GitHub Pages demo built from synthetic data only:
  <https://jinjianghu19823-wq.github.io/paper-galaxy/>

## Local-First Boundary

Paper Galaxy does not require an account, telemetry, automatic upload, hosted
backend, or cloud dependency. The installed app reads local files and local
SQLite project state. Optional OCR and embeddings run only when explicitly
enabled.

## Intentionally Not Implemented

The public alpha does not include cloud sync, accounts, hosted indexing,
document upload, telemetry, remote plugin loading, Zotero integration, desktop
packaging, mandatory OCR services, mandatory LLM labeling, LLM chat, React, or
Node build tooling.

The personal cloud library is design-only. It is documented as a future opt-in
direction and is not implemented in the runtime.

## Install

```bash
python -m pip install -e ".[dev,ml,pdf,app]"
```

## Quickstart

```bash
paper-galaxy doctor
paper-galaxy init . --force
paper-galaxy index examples/tiny_corpus --project-dir . --min-chars 40
paper-galaxy serve --project-dir .
```

## Privacy Warning

`.paper-galaxy/` can contain extracted text, chunks, vectors, labels, map runs,
and backup metadata. Do not commit it or attach it to public issues. Public bug
reports should use synthetic examples or minimal redacted snippets.

## Known Limitations

- PDF extraction is useful but not a full scholarly PDF parser.
- OCR requires optional dependencies and a local Tesseract binary.
- Embeddings require an explicit local model path unless model download is
  explicitly allowed.
- The public demo is static and does not run the local FastAPI backend.
- Zotero and cloud sync are not implemented.

## Reporting Issues

Use the repository issue templates:
<https://github.com/jinjianghu19823-wq/paper-galaxy/issues/new/choose>

Do not include private paper text, local databases, API keys, secrets, or
sensitive local paths.

## Future Direction

The next public milestone is launch stabilization: installation feedback,
extraction quality reports, graph usability fixes, clearer docs, and careful
triage of privacy concerns. A possible C1 encrypted backup vault remains a
design-review topic only until explicitly requested as an implementation phase.
