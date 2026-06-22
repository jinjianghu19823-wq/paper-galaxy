# Paper Galaxy Agent Notes

## Purpose

Paper Galaxy is a local-first research cartography tool. It will eventually map a
personal corpus of papers, notes, PDFs, screenshots, Markdown, LaTeX, and Zotero
exports into an interactive 2D research universe.

## Layout

- `src/paper_galaxy/`: Python package, CLI, scanner, extractors, pipeline,
  ML helpers, static exporters, chunking, SQLite storage, indexer, and search.
- `tests/`: pytest coverage for imports and Phase 0/Phase 1/Phase 2 behavior.
- `examples/tiny_corpus/`: synthetic local corpus for scan smoke tests.
- `docs/`: roadmap, architecture, decisions, and privacy notes.
- `.github/workflows/ci.yml`: basic CI checks.

## Current Phase

This repository is in Phase 2: local SQLite database and incremental indexing.
It can still export static offline HTML, and it can now persist document/chunk
records plus local FTS search. Do not implement Phase 3 web app work or later
phases unless explicitly asked.

## Commands

- `python -m pytest`
- `python -m ruff check .`
- `python -m ruff format . --check`
- `python -m mypy src`
- `paper-galaxy doctor`
- `paper-galaxy scan examples/tiny_corpus --out galaxy.html --force`
- `paper-galaxy index examples/tiny_corpus --project-dir . --min-chars 40`
- `paper-galaxy search "neural operator" --project-dir .`
- `paper-galaxy db-stats --project-dir .`

## Engineering Rules

- Keep the product local-first.
- Do not add cloud calls by default.
- Do not add telemetry.
- Do not add heavyweight dependencies to the default install.
- Do not add AGPL/copyleft dependencies without explicit approval.
- Do not implement future phases unless asked.
- Do not add FastAPI, React, desktop packaging, cloud sync, accounts,
  telemetry, OCR, Zotero integration, LLM chat, dense embeddings, or other
  Phase 3+ features unless a future task explicitly asks for that phase.
- Do not commit `.paper-galaxy/`, `*.sqlite3`, `galaxy.html`, `galaxy.json`, or
  other generated local artifacts.
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
