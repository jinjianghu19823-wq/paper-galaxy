# Paper Galaxy Agent Notes

## Purpose

Paper Galaxy is a local-first research cartography tool. It will eventually map a
personal corpus of papers, notes, PDFs, screenshots, Markdown, LaTeX, and Zotero
exports into an interactive 2D research universe.

## Layout

- `src/paper_galaxy/`: Python package and CLI entrypoint.
- `tests/`: pytest coverage for imports and Phase 0 CLI behavior.
- `docs/`: roadmap, architecture, decisions, and privacy notes.
- `.github/workflows/ci.yml`: basic CI checks.

## Current Phase

This repository is in Phase 0: scaffold, development environment,
documentation, and persistent project instructions only. Do not implement future
pipeline phases unless explicitly asked.

## Commands

- `python -m pytest`
- `python -m ruff check .`
- `python -m ruff format . --check`
- `python -m mypy src`
- `paper-galaxy doctor`

## Engineering Rules

- Keep the product local-first.
- Do not add cloud calls by default.
- Do not add telemetry.
- Do not add heavyweight dependencies to the default install.
- Do not add AGPL/copyleft dependencies without explicit approval.
- Do not implement future phases unless asked.
- Add tests for any behavior change.
- Update docs when architecture changes.

## Definition Of Done

- Code imports.
- CLI smoke test works.
- Tests pass.
- Lint passes.
- Typecheck passes, or any unavoidable typecheck issue is documented honestly.
