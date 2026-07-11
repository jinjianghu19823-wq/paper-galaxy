# Nightly Execution

Milestone: **Paper Galaxy Local Research Workstation & Evidence-First Insight Engine**

## Current checkpoint

- Branch: `codex/local-research-workstation`
- Last pushed head before this checkpoint: `609540d` (`Preserve index and
  vector consistency`).
- Dependency: Draft PR #1 (`codex/safe-reproducible-release`) remains open and
  green. Stacked Draft PR #2 targets PR #1's branch and begins with
  `Depends on #1`:
  <https://github.com/jinjianghu19823-wq/paper-galaxy/pull/2>.
- Current green checkpoint: Stage 5 source registry, durable local jobs, safe
  project lifecycle, one-command loopback launch, and the first local Web
  security boundary. Schema v9, source/job validation, ownership fencing,
  source-tree zero-write guards, and installed-wheel API smoke are complete.
  The 588-test full suite and `make release-check` pass. This checkpoint still
  needs its semantic commit and push before Stage 6 begins.
- Safety boundary: only synthetic fixtures and pytest temporary directories
  were bootstrapped or migrated. No real Paper Galaxy project, user database,
  source corpus, Zotero profile, or Zotero database was opened for migration,
  modified, restored, or deleted.

## Checkpoint sequence

1. **Completed and pushed:** harden demo publication, exact ranking, and public
   numeric determinism on PR #1.
2. **Completed:** create `codex/local-research-workstation` from the green PR #1
   head and open stacked Draft PR #2 with `Depends on #1`.
3. **Completed and pushed:** add transactional SQLite migrations, explicit
   connection modes, strict stored JSON, consistency validation, and short
   audited write transactions.
4. **Completed and pushed:** make
   backup/restore consistent, portable, attack-resistant, and atomic.
5. **Completed and pushed:** preserve indexing/vector/run consistency.
6. **Green; commit/push pending:** add sources, durable jobs, and one-command
   local launch.
7. **Next:** implement incremental read-only Zotero sync.
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
- Stage 5 schema v9 adds persistent, path-private corpus/Zotero source records
  and durable single-writer jobs. Migration capability checks now compare the
  exact critical partial-index SQL; legacy Zotero profile URL/path identities
  are canonicalized during backfill; validation covers source/job JSON and
  state consistency.
- Launch performs all corpus relationship checks before project creation,
  repairs a source registration whose first enqueue was interrupted, and never
  places project metadata or a custom database inside a source tree. Direct
  indexing and Zotero import enforce the same zero-write relationship. Corpus
  root/file identity is rechecked across discovery and commit boundaries.
- Job enqueue rechecks source state in the same transaction. Worker lease,
  metadata identity, owner instance, and durable row status fence every
  publication boundary. Normal server stop records `interrupted`; an explicit
  cancellation records `cancelled`; a cancellation arriving after an atomic
  publication records `completed`. Successful input jobs atomically enqueue one
  deduplicated analysis follow-up after all earlier source work.
- Restore and the worker are mutually exclusive. The local API constrains
  Zotero HTTP to one canonical loopback origin with proxies and redirects
  disabled. Web Host/Origin/write-token checks, safe 500 responses, security
  headers, bounded query parameters, and path-free source/job payloads are
  covered by adversarial tests.
- `python -m pytest`: 588 passed with the existing Starlette/httpx deprecation
  warning. `python -m ruff check .`, `python -m ruff format . --check` (146
  files), and `python -m mypy src` (89 source files) passed.
- `python -m build` initially could not reach PyPI from the sandbox, then the
  approved network retry built both sdist and wheel successfully. The final
  `make release-check` repeated all 588 tests, lint, formatting, typecheck,
  isolated build, deterministic demo publication/static check, and strict
  public-readiness check; all passed.
- The current wheel was installed from `dist/` in an isolated temporary path.
  A system-site dependency smoke imported the installed wheel, completed
  `index_corpus` and `rebuild_analysis`, returned health `ok`, 3 search results,
  and an 8-document map, and proved the synthetic source digest unchanged.
  A truly clean `[full]` dependency install could not run because the platform
  rejected the PyPI approval after its usage limit was reached; the local
  wheel itself also installed/imported in a dependency-empty venv with
  `--no-deps`.
- `python scripts/check_demo_site.py --dist site_dist --serve` and the actual
  installed `launch` socket smoke are blocked by this sandbox's
  `socket.bind(127.0.0.1, 0)` `EPERM`. The required escalation was rejected by
  the platform usage limit. Static demo validation and strict readiness pass;
  rerun the existing smoke script on a loopback-capable Mac/Windows/Linux host.
- `pytest-cov` is still absent from the active environment, so the requested
  coverage command exits before collection rather than producing a report.
- A final independent adversarial audit reproduced and fixed source/enqueue
  crash recovery and stale-PID worker recovery, then reported no remaining
  explicit Stage 5 P0/P1. All migrations, restores, scans, sources, jobs, and
  smoke projects used synthetic data under pytest/temp directories; no real
  user project or Zotero data was touched.

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

Stage 5 is green and awaiting its checkpoint commit/push. Remaining true
incremental Zotero sync, structured evidence, citations, immutable analysis snapshots,
evidence-first insights, the workspace UI/security checkpoint, E2E coverage,
benchmarks, and final bilingual/public demo work remain unfinished. Do not
treat planned routes, schemas, jobs, insights, UI states, E2E tests, or
benchmark commands as delivered before their checkpoint is green and
committed.

The next implementation command after the Stage 5 push is:

```text
python -m pytest -q tests/test_zotero_integration.py
```
