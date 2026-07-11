# Changelog

[English](CHANGELOG.md) | [简体中文](CHANGELOG.zh-CN.md)

## Unreleased

- Added schema v10 and true profile-scoped, read-only Zotero synchronization.
  Default syncs resume from a CAS-fenced cursor owned by the exact registered
  collection/tag/status profile; `--full` is explicit. The local API client
  preserves `Last-Modified-Version` across safe pagination, retries bounded
  transient reads, consumes `/items?since=` and `/deleted?since=`, batches
  parent hydration, and rejects version drift before publishing a cursor.
  Child-only note, annotation, and attachment updates now rebuild their parent
  documents without per-parent child requests. Verified remote deletions are
  tombstoned and cascade through child/attachment/profile state; deleted parents
  leave live search/maps, while collection renames/deletions refresh affected
  parent documents. Versioned profile-item membership now applies union
  visibility across active profiles and source removal. A source-global
  materialization fingerprint covers attachment/include/PDF/note/metadata,
  reading-tag, minimum-text, and chunk settings; changes require explicit
  `--full`, and default jobs inherit the latest compatible completed-run
  configuration. Incomplete runs never advance a cursor, and unchanged
  materializations skip PDF extraction and preserve chunks/vectors. Migration
  tests use a frozen real v9 schema fixture, and the first v10 baseline
  deliberately rematerializes instead of trusting the legacy global cursor.
  Locator drift is rejected before remote access or writes, initial
  source/profile/run registration is atomic, and each changed parent now
  refreshes membership across all compatible active profiles without advancing
  their cursors. A failed first locator remains auditable without claiming the
  project, while a source-wide monotonic version fence rejects older snapshots
  before each business transaction and final cursor publication. Full-sync
  generation preparation is deferred until the complete remote response has
  passed that shared version check and source-wide fence, so a stale full
  response cannot invalidate the last published generation. Content-changing
  full syncs prepare attachment/PDF/text/chunk work outside the write
  transaction, then publish the generation, memberships, documents, vectors,
  cursor, and run audit atomically; incomplete, cancelled, failed, or crashed
  runs retain the previous generation. The initial registration transaction
  also rechecks the exact source and active-profile locator, closing a
  concurrent first-registration overwrite race.
- Added the first local research workstation checkpoint: schema v9 registered
  corpus/Zotero sources, a durable single-writer job queue with cooperative
  cancellation and crash recovery, safe project initialization, and
  `paper-galaxy launch` with loopback-only automatic port selection. The local
  Web app now exposes bounded source/job controls protected by Host checks,
  same-origin validation, a per-process write token, CSP, and other browser
  hardening headers. Zotero HTTP traffic is constrained to one loopback origin,
  without proxies or redirects. Launch and direct indexing reject projects or
  databases inside source trees before writing, job/source enqueue is fenced in
  one transaction, worker ownership is rechecked at commit boundaries, and
  restore refuses an active background worker.
- Added schema v8 vector provenance and lifecycle hardening: exact local-model
  fingerprints, collision-safe canonical document revisions,
  source-revision compare-and-swap writes, automatic dead-owner
  run recovery, active/provenance-only semantic reads, bounded-memory NumPy
  top-k with batched metadata loading, expanded vector validation, and an
  explicit dry-run-first stale-vector prune. Replaced Windows `os.kill(pid, 0)`
  probes in run and backup recovery with non-destructive process handles, and
  removed the unused FAISS extra.
- Replaced implicit SQLite initialization with schema v7 transactional
  bootstrap/migrations, explicit read-only/read-write/migration connections,
  future-schema refusal, strict schema/JSON validation, short audited write
  transactions, and migration snapshots made with SQLite's backup API.
- Hardened project backup/restore with active-WAL-safe SQLite snapshots,
  strict streaming ZIP/checksum/resource validation, portable custom database
  and vector-index mappings, owned-output atomic archive publication, durable
  crash-recoverable forced restore, and cross-process project maintenance
  locks. Pending recovery gates normal connections; bounded extraction and
  strictly owned staging cleanup limit resource and privacy exposure. Checksum
  validation can no longer be disabled.
- Replaced destructive demo output replacement with a staged, validated
  publisher. Only empty directories or outputs carrying a supported Paper
  Galaxy build marker can be replaced; symlinked, dangerous, and unowned
  destinations are rejected with rollback-safe recovery.
- Restored exact-score ranking before stable tie-breaks for cluster terms and
  pair explanations, and rounded public demo floats to at most eight decimal
  places with finite JSON numbers, positive zero, canonical cluster IDs, and
  stable UTF-8 serialization.
- Made release cleanup build-only: `clean`, `clean-build`, release checks, and
  the compatibility `clean-artifacts` target preserve local projects,
  databases, Zotero data, backups, vector indexes, and user exports.
- Made default demo builds write only to the output directory, added an
  explicit source-fixture refresh mode, stabilized every public demo ID across
  absolute corpus paths, and added a CI clean-worktree gate.
- Updated package license metadata to the current SPDX string format.
- Hardened the Zotero Reading Graph beta for real local libraries: no-write
  `zotero doctor`, collection filtering by key/name/path, validated reading
  statuses and local library aliases, explicit PDF policies, annotation import,
  richer import summaries, and inspector metadata for DOI/URL/PDF/Zotero links.
- Added the first Zotero Reading Graph integration: read-only local API client,
  Zotero schema v6 tables, importer, metadata/PDF/notes handling, CLI commands,
  local web API endpoints, UI filters, docs, and synthetic-only public demo
  boundaries.
- Added post-public-launch activation docs, FAQ, troubleshooting, demo guide,
  feedback guide, triage guide, launch notes, and live-site verification.
- Added `scripts/check_live_site.py`, `scripts/launch_report.py`, release
  workflow checks, and Makefile targets for `live-check`, `post-public-check`,
  `release-check`, and `launch-report`.
- Strengthened public-readiness checks with source-only/site-dist modes,
  release/feedback documentation checks, cloud design boundary checks, and
  cloud runtime source scanning.
- Added local social preview SVG assets and public demo metadata for Open Graph,
  Twitter cards, canonical URLs, and language alternates.
- Added public launch readiness scripts, community files, and GitHub Pages demo
  workflow.
- Added a static English and Simplified Chinese public demo site generated from
  the synthetic tiny corpus.
- Added an English/Simplified Chinese language toggle to the local web app.
- Added future personal cloud library design docs without implementing cloud
  runtime functionality.
- Added Simplified Chinese versions of the main public repository documents.

## 0.1.0

- Added Phase 7 project validation with console and JSON reports.
- Added SQLite schema v5 saved map runs, map run CLI commands, and web run
  selection.
- Added local project backup export/import with manifests and checksums.
- Added a static built-in plugin registry for local extractor boundaries.
- Added package metadata, build checks, release docs, and backup docs.
- Kept Phase 7 local-first: no telemetry, accounts, cloud sync, remote plugin
  loading, LLM chat, React, or Node build tooling.

## 0.0.1

- Initial local-first scaffold through Phase 6 development.
