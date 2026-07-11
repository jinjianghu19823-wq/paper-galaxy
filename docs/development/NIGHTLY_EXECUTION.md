# Nightly Execution

Milestone: **Paper Galaxy Local Research Workstation & Evidence-First Insight Engine**

## Current checkpoint

- Branch: `codex/local-research-workstation`
- Stage 4 base head: `3943cf3` (`Make backups consistent, portable and atomic`)
- Dependency: Draft PR #1 (`codex/safe-reproducible-release`) remains open and
  green. The workstation branch was created from its head and Stages 2-3 are
  pushed. Creating the stacked Draft PR is temporarily blocked because the
  local `gh` credential is invalid; after `gh auth login -h github.com`, it must
  target PR #1's branch and begin with `Depends on #1`.
- Completed working checkpoint: Stage 4 index/vector/run consistency. Schema v8,
  collision-safe model/source provenance, in-flight source CAS, dead-owner run
  recovery, exact blockwise search, optimistic compare snapshots, validation,
  and dry-run-first pruning are implemented. The 463-test full suite and all
  release gates are green. This document is included in checkpoint commit
  `Preserve index and vector consistency`; Stage 5 is next.
- Safety boundary: only synthetic fixtures and pytest temporary directories
  were bootstrapped or migrated. No real Paper Galaxy project, user database,
  source corpus, Zotero profile, or Zotero database was opened for migration,
  modified, restored, or deleted.

## Checkpoint sequence

1. **Completed and pushed:** harden demo publication, exact ranking, and public
   numeric determinism on PR #1.
2. **Branch and Stage 2 push complete; stacked PR temporarily blocked:** create
   `codex/local-research-workstation` from the green PR #1 head and open a
   stacked Draft PR when the connector limit resets.
3. **Completed and pushed:** add transactional SQLite migrations, explicit
   connection modes, strict stored JSON, consistency validation, and short
   audited write transactions.
4. **Completed and pushed:** make
   backup/restore consistent, portable, attack-resistant, and atomic.
5. **Completed in this checkpoint:** preserve indexing/vector/run consistency.
6. **Next:** add sources, durable jobs, and one-command local launch.
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
- Stage 2 was committed as `ddc5d3e` and pushed to
  `origin/codex/local-research-workstation`.
- Stage 3 red characterization: the new archive/atomicity suites produced 25
  expected failures and one pass against the old backup implementation. They
  reproduced active-WAL loss, direct output truncation, half-written forced
  restore, custom database-path loss, vector basename collision, checksum and
  path attacks, and missing ZIP resource limits.
- Stage 3 final audit fixed absent-target initialization races, pending-journal
  access, control-path collisions, undeclared DB overwrite, durable parent
  fsync, bounded ZIP/config/path/free-space handling, and strictly owned orphan
  staging cleanup. The auditor reports no remaining Stage 3 P0/P1; vector file
  semantic provenance is explicitly deferred to Stage 4.
- Stage 3 current green: 125 focused snapshot/archive/atomicity/path-safety,
  project-lock, and connection tests pass. `python -m pytest -q` passes all 410
  tests with the existing Starlette/httpx deprecation warning. Ruff, Ruff
  formatting, Mypy (77 source files), and `git diff --check` pass.
- Final post-audit `make release-check` passes: 410 tests, sdist and wheel,
  deterministic demo build, static demo validation, and strict public-readiness
  validation are all green.
- Stage 3 was committed as `3943cf3` and pushed to
  `origin/codex/local-research-workstation`.
- Stage 4 red characterization reproduced model-path weight reuse, in-flight
  reindex vector resurrection, chunk-configuration reuse, stale vector-index
  metadata, legacy provenance search, N+1 vector lookup, and hard-exit runs.
- Stage 4 current green: schema v8 migration/model-fingerprint/storage,
  ranking, run recovery, maintenance, transaction, validation, and embedding
  focused suites pass. The final adversarial audit also reproduced and fixed a
  NUL field-boundary collision in the first document-revision encoding and a
  cross-query compare snapshot race. The revision now hashes a namespaced
  canonical JSON array, semantic display loading recomputes exact document or
  chunk embedding input, and compare retries `PRAGMA data_version` changes at
  most three times before returning a safe actionable error.
- `python -m pytest`: 463 passed with the existing Starlette/httpx deprecation
  warning.
- `python -m ruff check .`: passed; `python -m ruff format . --check`: 134
  files already formatted; `python -m mypy src`: 82 source files passed.
- `python -m build`: built the sdist and wheel successfully. Final `make
  release-check` repeated Ruff, format, Mypy, all 463 tests, isolated package
  build, deterministic demo publication/static validation, and strict public
  readiness; all passed.
- `python scripts/check_demo_site.py --dist site_dist --serve`: the restricted
  sandbox correctly denied loopback binding, then the approved local-only retry
  passed all English/Chinese routes and `tiny-map.json` with HTTP 200. The four
  real JavaScript assets listed earlier also passed `node --check` again.
- Two independent final Stage 4 audits report no remaining P0/P1. One
  non-blocking corruption-repair limitation remains: if an external process
  directly mutates SQLite text while deliberately leaving its stored revision
  unchanged, validation and semantic search detect/refuse it, while the prune
  command itself trusts the stored revision. Normal Repository writes cannot
  create this state.
- No real model, project database, corpus, Zotero profile, or user vector index
  was opened or modified.

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

The stacked Draft PR still needs creation after local GitHub authentication is
restored. Remaining launch, sources/jobs, true incremental Zotero sync,
structured evidence, citations, immutable analysis snapshots, evidence-first
insights, the workspace UI/security checkpoint, E2E coverage, benchmarks, and
final bilingual/public demo work remain unfinished. Do not treat planned routes,
schemas, jobs, insights, UI states, E2E tests, or benchmark commands as delivered
before their checkpoint is green and committed.
