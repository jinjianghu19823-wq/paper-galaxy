# Paper Galaxy Agent Notes

## Purpose

Paper Galaxy is a local-first research cartography tool. It will eventually map a
personal corpus of papers, notes, PDFs, screenshots, Markdown, LaTeX, and Zotero
exports into an interactive 2D research universe.

## Layout

- `src/paper_galaxy/`: Python package, CLI, scanner, extractors, pipeline,
  ML helpers, static exporters, chunking, SQLite storage, indexer, search,
  optional embeddings, explainability helpers, saved map runs, validation,
  backups, plugin metadata, local web app, and Zotero Reading Graph importer.
- `tests/`: pytest coverage for imports, Phase 0-Phase 7 behavior, and Zotero
  Reading Graph behavior.
- `examples/tiny_corpus/`: synthetic local corpus for scan smoke tests.
- `docs/`: roadmap, architecture, decisions, and privacy notes.
- `.github/workflows/ci.yml`: basic CI checks.

## Current Phase

This repository is in public alpha with Phase 7 professionalization and the
first Zotero Reading Graph implementation. The repository is public, and the
current milestone focuses on local Zotero import, reading graph usability, and
privacy-safe feedback. The product remains local-first. Do not implement cloud
runtime features unless explicitly asked.

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
- `paper-galaxy clusters --project-dir .`
- `paper-galaxy explain-pair neural_operators/fourier_neural_operator.md neural_operators/deep_operator_network.txt --project-dir .`
- `paper-galaxy rename-cluster CLUSTER_SIGNATURE "Neural Operators" --project-dir .`
- `paper-galaxy reset-cluster-label CLUSTER_SIGNATURE --project-dir .`
- `paper-galaxy validate-project --project-dir . --json-out validation.json`
- `paper-galaxy build-map-run --project-dir . --name "Tiny corpus map"`
- `paper-galaxy map-runs --project-dir .`
- `paper-galaxy show-map-run MAP_RUN_ID --project-dir .`
- `paper-galaxy export-map-run MAP_RUN_ID --project-dir . --out map-run.json`
- `paper-galaxy delete-map-run MAP_RUN_ID --project-dir . --yes`
- `paper-galaxy export-project --project-dir . --out paper-galaxy-backup.zip --yes`
- `paper-galaxy import-project paper-galaxy-backup.zip --project-dir /path/to/restore --dry-run`
- `paper-galaxy plugins`
- `paper-galaxy zotero detect`
- `paper-galaxy zotero status`
- `paper-galaxy zotero import --project-dir . --include-pdfs --include-notes --build-reading-map`
- `paper-galaxy zotero graph --project-dir . --name "Zotero Reading Graph"`
- `paper-galaxy zotero validate --project-dir .`
- `paper-galaxy zotero smoke-test --project-dir .`
- `paper-galaxy serve --project-dir .`
- `paper-galaxy extract-preview examples/tiny_corpus/neural_operators/fourier_neural_operator.md`
- `python -m build`
- `python scripts/build_demo_site.py --out site_dist`
- `python scripts/check_demo_site.py --dist site_dist`
- `python scripts/check_demo_site.py --dist site_dist --serve`
- `python scripts/public_readiness_check.py --strict`
- `python scripts/public_readiness_check.py --strict --require-site-dist`
- `python scripts/check_live_site.py --base-url https://jinjianghu19823-wq.github.io/paper-galaxy/`
- `make launch-check`
- `make post-public-check`
- `make release-check`
- `make live-check`
- `make launch-report`

## Engineering Rules

- Keep the product local-first.
- Do not add cloud calls by default.
- Do not add telemetry.
- Do not add heavyweight dependencies to the default install.
- Do not add AGPL/copyleft dependencies without explicit approval.
- Do not implement future phases unless asked.
- Do not add React, Node build tooling, desktop packaging, cloud sync, accounts,
  telemetry, cloud OCR, Zotero write-back, Zotero online sync, LLM chat, cloud
  embedding APIs, or other future features unless a future task explicitly asks
  for that phase.
- Zotero integration must remain read-only toward Zotero. Do not mutate
  Zotero's database, items, tags, collections, notes, or attachments.
- Do not copy Zotero PDFs by default; reference local attachment paths and store
  extracted text only inside the Paper Galaxy project database.
- Do not add remote plugin loading; Phase 7 plugins are static built-in
  boundaries only.
- The personal cloud library is design-only in this milestone. Do not add cloud
  sync, hosted auth, storage SDKs, accounts, payment code, or background upload.
- Cluster labels and pair explanations must remain local and inspectable. Do
  not add mandatory LLM labeling or remote explanation services.
- Pair explanations may return short excerpts, but should not return full
  extracted document text.
- Optional embeddings must stay local-first. Do not allow hidden model
  downloads; require a local model path unless the user explicitly opts in with
  `--allow-model-download`.
- OCR must stay optional, local, and disabled by default.
- Frontend assets must not include remote URLs, CDN references, remote fonts, or
  external images.
- Public demo social preview assets must remain local files under `site/assets/`.
- Graph movement, force settings, labels, and manual layout persistence are
  local browser UI state only. Do not write graph positions to SQLite.
- Do not commit `.paper-galaxy/`, `*.sqlite3`, `galaxy.html`, `galaxy.json`,
  `extraction-report.json`, `validation.json`, `map-run*.json`,
  `paper-galaxy-backup*.zip`, local vector index files, downloaded model files,
  Zotero databases, Zotero storage folders, PDFs, or other generated local
  artifacts.
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
