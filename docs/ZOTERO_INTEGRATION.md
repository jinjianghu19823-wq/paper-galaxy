# Zotero Integration

[English](ZOTERO_INTEGRATION.md) | [简体中文](ZOTERO_INTEGRATION.zh-CN.md)

Paper Galaxy imports from Zotero Desktop as a local-first, read-only workflow.
It uses the Zotero Desktop local API at `http://localhost:23119/api/` as the
primary connector. It does not write to Zotero, performs no upload, and local
PDFs are not copied by default.

## Quickstart

```bash
paper-galaxy init .
paper-galaxy zotero detect
paper-galaxy zotero status
paper-galaxy zotero doctor --project-dir .
paper-galaxy zotero import --project-dir . --include-pdfs --include-notes --build-reading-map
paper-galaxy serve --project-dir .
```

Open the local app and switch the graph source to Zotero to inspect the saved
`Zotero Reading Graph`.

## What Gets Imported

- Zotero item metadata: title, item type, year, publication title, DOI, URL,
  abstract, date, version, and Zotero key.
- Creators, tags, and collection membership.
- Child notes and annotation text when `--include-notes` is enabled.
- Attachment metadata and local PDF extraction when `--include-pdfs` is enabled
  and the file can be resolved locally.
- Metadata-only documents for items without readable PDFs.

Imported items become Paper Galaxy documents with stable IDs:

- Zotero item rows: `zotero_item_<sha16>`
- Paper Galaxy document rows: `doc_zotero_<sha16>`

PDF paths are referenced in place. Paper Galaxy does not copy, move, or mutate
Zotero attachment files by default.

## Connector Boundary

The main connector reads the local API without authentication. Direct
`zotero.sqlite` access is fallback-only and read-only; it is used for diagnostics
and path hints because Zotero can change its database schema between releases.
Local API URLs are canonicalized to one HTTP loopback origin. Requests ignore
environment proxy settings, reject redirects, and reject pagination links that
change origin, so a local response cannot silently send library data elsewhere.

Paper Galaxy never writes to Zotero. There is no Zotero OAuth, no online Zotero
Web API sync path, no cloud sync, and no hosted account system in this feature.

## CLI Commands

- `paper-galaxy zotero detect`: best-effort local API and data-directory
  detection.
- `paper-galaxy zotero status`: check whether Zotero Desktop local API is
  reachable.
- `paper-galaxy zotero doctor`: no-write real-machine readiness check for the
  local API, collections, tags, attachment samples, optional `pypdf`, and
  existing project state.
- `paper-galaxy zotero validate-local`: alias for `zotero doctor`.
- `paper-galaxy zotero collections`: list local API collections.
- `paper-galaxy zotero items`: preview top-level items without importing.
- `paper-galaxy zotero import`: import items into local Paper Galaxy SQLite
  state.
- `paper-galaxy zotero graph`: build or rebuild a saved Zotero reading map from
  imported items.
- `paper-galaxy zotero imported`: list imported Zotero items.
- `paper-galaxy zotero validate`: report Zotero table counts and dangling links.
- `paper-galaxy zotero smoke-test`: dry-run a small local API sample.

Useful import options include `--collection`, repeatable `--tag`, repeatable
`--item-type`, `--include-pdfs/--no-include-pdfs`,
`--include-notes/--no-include-notes`, `--include-metadata-only`,
`--pdf-policy`, `--include-status`, `--limit`, `--since-version`, `--dry-run`,
`--full`, `--force`, and `--build-reading-map`.

`--collection` accepts a collection key, exact collection name, or slash-style
path. Matching is case-insensitive for names and paths; ambiguous names and
missing collections fail before import. The local beta supports only the Zotero
Desktop user library aliases `local`, `user`, `users/0`, and `/users/0`.
Each durable workstation source profile currently accepts at most one
collection; register separate profiles for separate collections. Multi-
collection union is not implemented; each separate collection/tag/status
profile has its own durable cursor and cannot cause another profile to skip
records.

## Incremental Sync Semantics

The default import is incremental. A profile's first sync starts at library
version 0 and establishes a complete baseline. A profile migrated from schema
v9 receives no guessed cursor, and its first v10 baseline deliberately
rematerializes existing shared documents rather than treating the old global
cursor as proof of completeness. Later runs use only that exact registered
profile's saved cursor.
The canonical loopback API origin and optional Zotero data directory are part
of the registered local profile identity. Once a project has established that
locator, a different origin or data directory is rejected before any remote
request or project write; the existing profile remains usable and its source,
cursor, membership, and run audit rows stay unchanged. Until an explicit
re-registration workflow is available, use a separate project for an
intentional locator change.
If the first remote read fails before a complete version-fenced response is
available, the failed run and removed profile remain as audit evidence, but the
unverified locator is not treated as an active claim. A corrected locator can
therefore establish the project's first successful profile without `--full`.
`--full` deliberately restarts the fetch at version 0; it cannot be combined
with `--since-version`. The latter is reserved for expert diagnostics or
recovery and must equal the exact profile's saved cursor, so it cannot skip an
unseen version range. Use `--full --force` when a full reconciliation must also
overwrite an otherwise identical local materialization. `--force` by itself
only affects records returned by the changed feed and never implies a full
fetch.

Filter identity and local document materialization are separate contracts.
Collection/tag/item-type/status filters own independent cursors, while all
profiles for one Zotero source share the same local item and document rows. A
source-global fingerprint therefore covers the resolved attachment root,
PDF/note/attachment/metadata inclusion flags, PDF policy, read/reading/to-read
tag sets, `min_chars`, and chunk size/overlap. A change is rejected before
remote fetch or local writes unless `--full` is explicit. Only after the
complete remote response has one validated version and passes the source-wide
fence does that full sync prepare a new materialization generation and require
peer profiles to establish fresh baselines. A durable Zotero job without
explicit content overrides inherits the latest completed configuration
compatible with that generation, rather than silently falling back to defaults.
For a content-changing full sync, attachment/PDF/text/chunk preparation happens
before the write lock, while the generation switch and cursor publish share one
transaction. Incomplete, cancelled, failed, and process-interrupted runs leave
the prior published generation intact.

The connector reads all changed parent and child records from `/items?since=`,
uses bounded `itemKey` hydration only for a missing parent, and reads the
deletion log from `/deleted?since=`. Every page and endpoint must report the
same non-negative `Last-Modified-Version`. Header drift, pagination failure,
cancellation, malformed payloads, a database error, or an incomplete `--limit`
run leaves the profile cursor unchanged. The final cursor, completed-run audit,
and profile success timestamp publish in one short fenced transaction.
The source-wide published library version is also a monotonic lower bound:
responses older than it are rejected before business-row writes, and every
collection/child/deletion/item transaction plus final cursor publication
rechecks the fence against a concurrently completed peer sync.
The initial registration transaction likewise rechecks the canonical API/data
directory/library locator stored by the source and every active profile before
upsert, so two concurrent first registrations cannot overwrite one another.
CLI and durable-job summaries report the previous/new cursor, changed parent
and child counts, deletion count, and duration.

Deleted parents remain as local tombstone/audit state but their Paper Galaxy
documents become `missing` and disappear from normal search and maps. The delete
cascades to cached children, attachments, and every profile membership, while
preserving local audit rows. A verified child deletion rebuilds its parent
without the removed child. Collection-only rename/delete changes hydrate and
rebuild the parents known to use that collection before cursor publication.
An omission without deletion-feed evidence is still treated as an unsafe
partial response. Completely identical materializations skip attachment/PDF
work and do not rewrite document text, chunks, FTS, or vectors.

`zotero_profile_items` records membership with its observed library version.
Every changed parent is evaluated against the persisted filters of all active,
materialization-compatible profiles. Paper Galaxy updates those versioned
memberships without advancing peer cursors, so an old positive tag, collection,
item-type, or status match cannot keep a document visible after newer metadata
disproves it. A peer waiting for a new full-materialization baseline remains
fenced instead of being reactivated early. The linked document stays active
while any non-removed registered profile includes it; it becomes `unindexed`
when the active-profile union is empty. Removing or re-registering a source
recomputes the same union, without deleting the shared item or its audit
history.

After a successful import has registered the read-only local profile, it can be
queued during workstation startup:

```bash
paper-galaxy launch --project-dir . --zotero-sync --open
```

`--include-status` accepts `all`, `read`, `reading`, `to_read`, and `unknown`.
The old `unclassified` spelling is accepted as a deprecated alias for
`unknown`.

`--pdf-policy extract` is the default. `metadata` records attachment metadata
without extracting PDF text. `skip-missing` skips items that appear to have a
PDF but cannot produce local PDF text.

## Data Location

Imported metadata, extracted local PDF text, chunks, and saved reading maps live
inside the Paper Galaxy project database under `.paper-galaxy/`. That directory
may contain sensitive research material and should not be committed.

Run:

```bash
paper-galaxy zotero validate --project-dir .
paper-galaxy validate-project --project-dir .
```

to inspect counts and consistency without printing full source text.

## Failure Modes

- Zotero Desktop closed: open Zotero and rerun `paper-galaxy zotero status`.
- Local API disabled: enable Zotero's local API from Zotero settings, then
  restart Zotero.
- Missing PDFs: items can still import as metadata-only documents.
- Linked PDFs outside the data directory: paths are recorded conservatively and
  never copied by default.
- Extraction errors: the import records a warning and continues with metadata.

For a real-library checklist, see
[ZOTERO_REAL_WORLD_TESTING.md](ZOTERO_REAL_WORLD_TESTING.md). For deeper graph
behavior, see [READING_GRAPH.md](READING_GRAPH.md).
