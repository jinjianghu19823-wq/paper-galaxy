# Paper Galaxy Agent Notes

## Purpose

Paper Galaxy is a local-first research cartography tool. It will eventually map a
personal corpus of papers, notes, PDFs, screenshots, Markdown, LaTeX, and Zotero
exports into an interactive 2D research universe.

## Layout

- `src/paper_galaxy/`: Python package, CLI, scanner, extractors, pipeline,
  ML helpers, static exporters, chunking, SQLite storage, indexer, search,
  optional embeddings, and local web app.
- `tests/`: pytest coverage for imports and Phase 0/Phase 1/Phase 2 behavior.
- `examples/tiny_corpus/`: synthetic local corpus for scan smoke tests.
- `docs/`: roadmap, architecture, decisions, and privacy notes.
- `.github/workflows/ci.yml`: basic CI checks.

## Current Phase

This repository is in Phase 5: optional local semantic embeddings. It can export
static offline HTML, persist document/chunk records and extraction reports in
SQLite, search local FTS, serve a local read-only browser app, optionally run
local image OCR, and optionally store local dense document/chunk vectors when
the user explicitly runs embedding commands. Do not implement Phase 6 or later
phases unless explicitly asked.

## Commands

- `python -m pytest`
- `python -m ruff check .`
- `python -m ruff format . --check`
- `python -m mypy src`
- `paper-galaxy doctor`
- `paper-galaxy scan examples/tiny_corpus --out galaxy.html --force`
- `paper-galaxy index examples/tiny_corpus --project-dir . --min-chars 40`
- `paper-galaxy index examples/tiny_corpus --project-dir . --min-chars 40 --extraction-report-json extraction-report.json`
- `paper-galaxy search "neural operator" --project-dir .`
- `paper-galaxy db-stats --project-dir .`
- `paper-galaxy embed --project-dir . --model /path/to/local/sentence-transformer-model`
- `paper-galaxy semantic-search "operator learning for PDEs" --project-dir . --model /path/to/local/sentence-transformer-model`
- `paper-galaxy compare-neighbors neural_operators/fourier_neural_operator.md --project-dir . --model /path/to/local/sentence-transformer-model`
- `paper-galaxy vector-stats --project-dir .`
- `paper-galaxy serve --project-dir .`
- `paper-galaxy extract-preview examples/tiny_corpus/neural_operators/fourier_neural_operator.md`

## Engineering Rules

- Keep the product local-first.
- Do not add cloud calls by default.
- Do not add telemetry.
- Do not add heavyweight dependencies to the default install.
- Do not add AGPL/copyleft dependencies without explicit approval.
- Do not implement future phases unless asked.
- Do not add React, Node build tooling, desktop packaging, cloud sync, accounts,
  telemetry, cloud OCR, Zotero integration, LLM chat, cloud embedding APIs, or
  other Phase 6+ features unless a future task explicitly asks for that phase.
- Optional embeddings must stay local-first. Do not allow hidden model
  downloads; require a local model path unless the user explicitly opts in with
  `--allow-model-download`.
- OCR must stay optional, local, and disabled by default.
- Frontend assets must not include remote URLs, CDN references, remote fonts, or
  external images.
- Graph movement, force settings, labels, and manual layout persistence are
  local browser UI state only. Do not write graph positions to SQLite.
- Do not commit `.paper-galaxy/`, `*.sqlite3`, `galaxy.html`, `galaxy.json`,
  `extraction-report.json`, local vector index files, downloaded model files,
  or other generated local artifacts.
- Add tests for any behavior change.
- Update docs when architecture changes.
- When a phase is complete and checks pass, commit and push automatically unless
  the user says otherwise.

## Definition Of Done

- Code imports.
- CLI smoke test works.
- Tests pass.
- Lint passes.
- Typecheck passes, or any unavoidable typecheck issue is documented honestly.
