# FAQ

[English](FAQ.md) | [简体中文](FAQ.zh-CN.md)

## Is my data uploaded?

No. Paper Galaxy is local-first by default. The local app reads your files and
local SQLite database. It has no automatic upload, telemetry, account system, or
hosted backend.

## Does it require an account?

No. The current app runs locally without an account.

## What files are supported?

The scanner supports text, Markdown, LaTeX, optional basic PDFs through `pypdf`,
and image files only when image scanning/OCR options are explicitly enabled.

## Why does OCR need Tesseract?

OCR is optional and local. The Python extra installs wrappers, but actual image
OCR may require a user-installed local Tesseract binary.

## Are embeddings local?

Yes. Embedding commands are explicit local workflows. Remote model names are
rejected by default to avoid hidden downloads; provide a local model path or
explicitly opt in with `--allow-model-download`.

## Does the demo use real papers?

No. The public GitHub Pages demo uses synthetic sample content from
`examples/tiny_corpus` only.

## Can I use this with Zotero?

Not yet. Zotero integration is intentionally not implemented in this public
alpha.

## Can I use cloud sync?

No. Cloud sync is not implemented. The personal cloud library docs are
design-only and describe a possible future opt-in direction.

## Where is the database stored?

By default, project state is stored under `.paper-galaxy/`, including
`.paper-galaxy/paper_galaxy.sqlite3`.

## How do I delete local project state?

Delete the `.paper-galaxy/` directory for that project. This removes Paper
Galaxy metadata, extracted text, chunks, vectors, labels, and saved map runs for
that project.

## How do I report bugs without leaking private paper text?

Use synthetic files, minimal redacted examples, command output that does not
include private text, and screenshots with sensitive details hidden. Do not
attach `.paper-galaxy/`, SQLite databases, extracted chunks, API keys, or
private local paths.
