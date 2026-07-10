# Nightly Execution

Milestone: **Paper Galaxy Local Research Workstation & Evidence-First Insight Engine**

## Current checkpoint

- Branch: `codex/safe-reproducible-release`
- Base/head at start: `1aba6d566d94c879720ccc6760355d9afe4249d6` / `46e908bee410b7caf156c629c033fb9aef3f3f68`
- Active phase: P0 hardening for Draft PR #1
- Next checkpoint commit: `Prevent unsafe demo publication and restore exact ranking`
- Safety boundary: synthetic fixtures and pytest temporary directories only; no real project or Zotero data is opened, migrated, restored, or deleted.

## Checkpoint sequence

1. Harden demo publication, exact ranking, and public numeric determinism on PR #1.
2. Create `codex/local-research-workstation` from the green PR #1 head and open a stacked Draft PR.
3. Add transactional SQLite migrations and explicit connection modes.
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

Everything after the active P0 checkpoint remains unfinished until implemented and verified. Do not treat planned routes, schemas, jobs, insights, UI states, E2E tests, or benchmark commands as delivered before their checkpoint is green and committed.
