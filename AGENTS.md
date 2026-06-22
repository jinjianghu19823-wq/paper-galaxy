# Paper Galaxy Agent Notes

## Purpose

Paper Galaxy is a local-first research cartography tool. It will eventually map a
personal corpus of papers, notes, PDFs, screenshots, Markdown, LaTeX, and Zotero
exports into an interactive 2D research universe.

## Layout

- `src/paper_galaxy/`: Python package, CLI, scanner, extractors, pipeline,
  ML helpers, and static exporters.
- `tests/`: pytest coverage for imports and Phase 0/Phase 1 CLI behavior.
- `examples/tiny_corpus/`: synthetic local corpus for scan smoke tests.
- `docs/`: roadmap, architecture, decisions, and privacy notes.
- `.github/workflows/ci.yml`: basic CI checks.

## Current Phase

This repository is in Phase 1: static CLI MVP. It can scan a local folder,
extract simple text formats, build a TF-IDF map, and export static offline HTML.
Do not implement Phase 2 database work or later phases unless explicitly asked.

## Commands

- `python -m pytest`
- `python -m ruff check .`
- `python -m ruff format . --check`
- `python -m mypy src`
- `paper-galaxy doctor`
- `paper-galaxy scan examples/tiny_corpus --out galaxy.html --force`

## Engineering Rules

- Keep the product local-first.
- Do not add cloud calls by default.
- Do not add telemetry.
- Do not add heavyweight dependencies to the default install.
- Do not add AGPL/copyleft dependencies without explicit approval.
- Do not implement future phases unless asked.
- Do not add SQLite, FastAPI, React, desktop packaging, cloud sync, accounts,
  telemetry, OCR, Zotero integration, LLM chat, or dense embeddings unless a
  future task explicitly asks for that phase.
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
