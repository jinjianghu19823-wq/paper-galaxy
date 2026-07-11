# Nightly Execution

Milestone: **Paper Galaxy Local Research Workstation & Evidence-First Insight Engine**

## Current checkpoint

- Branch: `codex/local-research-workstation`
- Committed head: `28aaba39c5faa3cd51cc9d195a4c359aa4173a24`
- Dependency: Draft PR #1 (`codex/safe-reproducible-release`) remains open and
  green at the same P0 head. The workstation branch was created from that head;
  its stacked Draft PR will use PR #1's branch as the base and begin with
  `Depends on #1`.
- Active phase: Stage 2 SQLite lifecycle implementation and its full release
  gate are green in the working tree; checkpoint commit, push, and stacked Draft
  PR are still pending.
- Next checkpoint commit: `Add transactional migrations and safe SQLite connections`
- Safety boundary: only synthetic fixtures and pytest temporary directories
  were bootstrapped or migrated. No real Paper Galaxy project, user database,
  source corpus, Zotero profile, or Zotero database was opened for migration,
  modified, restored, or deleted.

## Checkpoint sequence

1. **Completed and pushed:** harden demo publication, exact ranking, and public
   numeric determinism on PR #1.
2. **Branch created; PR pending the first commit:** create
   `codex/local-research-workstation` from the green PR #1 head and open a
   stacked Draft PR.
3. **Implementation and release gate complete; commit pending:** add transactional
   SQLite migrations, explicit connection modes, strict stored JSON,
   consistency validation, and short audited write transactions.
4. Make backup/restore consistent, portable, attack-resistant, and atomic.
5. Preserve indexing/vector/run consistency.
6. Add sources, durable jobs, and one-command local launch.
7. Implement incremental read-only Zotero sync.
8. Add structured evidence, citations, and deterministic analysis snapshots.
9. Add evidence-first insights and reading plans.
10. Rework API/UI request coordination and local web security.
11. Add E2E coverage, benchmarks, bilingual docs, and the synthetic public demo.

## Validation ledger

Record exact results here at each green checkpoint. The final gate is:

- P0 red phase: `tests/test_demo_publication_safety.py` produced 14 expected
  failures against the unsafe publisher; deterministic/ranking additions
  produced 6 expected failures.
- P0 green phase: 57 focused demo/ranking tests passed.
- `make release-check`: passed with 199 tests, Ruff, formatting, Mypy, package
  build, default demo build/check, and strict public readiness.
- `python scripts/check_demo_site.py --dist site_dist --serve`: passed; all
  English and Simplified Chinese routes plus `tiny-map.json` returned HTTP 200.
- The committed synthetic fixture was explicitly refreshed once for canonical
  cluster IDs and eight-decimal public numbers. A subsequent default build
  left the tracked-source status unchanged.
- Stage 2 migration/connection/transaction/privacy development suites passed at
  successive 98, 104, 121, and 144-test checkpoints. The independent final
  P0/P1 audit reported no remaining Stage 2 blockers and reproduced 124 focused
  passes.
- `python -m pytest -q`: 314 passed with one pre-existing Starlette/httpx
  deprecation warning.
- `make release-check`: passed with the same 314 tests, Ruff, formatting (114
  files), Mypy (72 source files), isolated sdist/wheel build, default demo
  publisher/check, and strict public readiness.
- The real static scripts that exist in this revision passed `node --check`:
  `src/paper_galaxy/web/static/app.js`, `graph.js`, `site/assets/demo.js`, and
  `site/assets/graph-demo.js`. The requested `site/app.js`, `site/graph.js`, and
  `site/i18n.js` paths do not exist in this repository, so they correctly return
  Node `MODULE_NOT_FOUND` rather than being reported as passes.
- `python scripts/check_demo_site.py --dist site_dist --serve` could not bind an
  ephemeral loopback port in this sandbox (`PermissionError: [Errno 1]`); the
  escalation request was rejected by the platform usage limit. The non-serving
  static check and strict readiness check both passed. An earlier P0 run in an
  environment with loopback permission had passed the serving smoke check.
- `python -m pytest --cov=paper_galaxy --cov-report=term-missing` could not run
  because the active environment lacks `pytest-cov`. `pytest-cov>=5.0` is now
  declared in the `dev` extra; install the refreshed dev environment before the
  final milestone coverage run.

```text
python -m pytest
python -m pytest --cov=paper_galaxy --cov-report=term-missing
python -m ruff check .
python -m ruff format . --check
python -m mypy src
python -m build
node --check src/paper_galaxy/web/static/app.js
node --check src/paper_galaxy/web/static/graph.js
node --check site/app.js
node --check site/graph.js
node --check site/i18n.js
python scripts/build_demo_site.py --out site_dist
python scripts/check_demo_site.py --dist site_dist --serve
python scripts/public_readiness_check.py --strict --require-site-dist
python scripts/benchmark_local.py --documents 100 500 2000 --json-out /tmp/paper-galaxy-benchmark.json
make release-check
make clean-build
git diff --check
git status --porcelain
```

## Unfinished work

Stage 2 still needs its checkpoint commit, push, and stacked Draft PR.
Backup/restore hardening, remaining vector lifecycle pruning and scalable
top-k, launch, sources/jobs, true incremental Zotero sync, structured evidence,
citations, immutable analysis snapshots, evidence-first insights, the workspace
UI/security checkpoint, E2E coverage, benchmarks, and final bilingual/public
demo work remain unfinished. Do not treat planned routes, schemas, jobs,
insights, UI states, E2E tests, or benchmark commands as delivered before their
checkpoint is green and committed.
