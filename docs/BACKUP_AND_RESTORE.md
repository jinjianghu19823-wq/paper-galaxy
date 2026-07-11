# Backup And Restore

[English](BACKUP_AND_RESTORE.md) | [简体中文](BACKUP_AND_RESTORE.zh-CN.md)

Paper Galaxy backup bundles are local, sensitive ZIP files. Source documents
are never included. A bundle may contain extracted text, chunks, vectors,
saved maps, labels, Zotero-derived local state, and optional vector-index
files, so protect it as carefully as the original project.

## Export

```bash
paper-galaxy export-project \
  --project-dir . \
  --out paper-galaxy-backup.zip \
  --yes
```

The database confirmation is deliberate. An active SQLite database is copied
with SQLite's online backup API, including committed WAL pages; the live
database file is never copied with `read_bytes`. The self-contained snapshot is
normalized to rollback-journal mode and checked with `quick_check`,
`foreign_key_check`, and the supported schema-capability registry.

Export builds a complete archive in a unique staging directory beside the
requested output. It validates the new archive before one atomic replace, so a
failed export leaves an existing backup byte-for-byte unchanged and removes
its staging files. Export holds a shared project lock across configuration,
database snapshot, and index collection. An existing output is replaced only
when it is itself a fully valid Paper Galaxy backup; an arbitrary existing file
is never silently claimed. The output cannot alias the live database, its
sidecars, project configuration, or an included vector index. Symbolic-link
output paths are rejected. On POSIX, the published archive is mode `0600`.

Vector-index files are opt-in:

```bash
paper-galaxy export-project \
  --project-dir . \
  --out paper-galaxy-backup.zip \
  --include-vector-indexes \
  --yes
```

Version 2 bundles store an explicit archive-path to project-path mapping, so
two index files with the same basename remain distinct. Missing, symbolic, or
project-external index targets are not followed into the archive. When index
files are not included, their disposable file metadata is removed from the
snapshot and the files can be rebuilt locally after restore. Index files are
captured under the project lock, but Stage 4 content/model fingerprints remain
the authority for semantic freshness; rebuild an index after restore whenever
its provenance is uncertain.

## Inspect Or Dry Run

```bash
paper-galaxy import-project \
  paper-galaxy-backup.zip \
  --project-dir /path/to/restore \
  --dry-run
```

Inspection is a security boundary, not an optional convenience. It rejects:

- duplicate ZIP members or checksum names;
- members omitted from checksums, missing members, or digest mismatches;
- absolute, Windows-drive, backslash, `..`, symlink-like, and special entries;
- excessive entry counts, expanded sizes, or compression ratios;
- unknown formats, unsupported/future schemas, and manifest/payload mismatch;
- SQLite snapshots that fail integrity, foreign-key, or schema checks.

Checksum validation cannot be disabled from the CLI or import API. Validation
streams archive data under bounded limits rather than extracting arbitrary ZIP
paths. Default budgets are 1,024 entries, 8 GiB compressed archive/total
expanded data, 4 GiB per member, a 200:1 ratio, and a 256 MiB post-extraction
free-space reserve. Portable names are capped at 1,024 UTF-8 bytes and 255
bytes per component; `project.toml` has a separate 1 MiB memory bound. These
defaults can be tightened through the Python API for constrained systems.

## Restore

```bash
paper-galaxy import-project \
  paper-galaxy-backup.zip \
  --project-dir /path/to/restore
```

Restore extracts only manifest-declared files into a unique staging project on
the target filesystem. Configuration, database, and index mappings are made
project-relative and validated before publication. A custom relative
`database_path`, such as `state/sql/custom.sqlite3`, round-trips at that same
relative location. A source database configured outside the original project
is safely remapped to `.paper-galaxy/paper_galaxy.sqlite3`; restore never writes
back to an archived absolute path.

For a new project, the complete staged tree is installed with one rename. An
existing `.paper-galaxy` requires explicit `--force`. Forced restore first
renames every destination's old file into an owner-only rollback transaction,
publishes configuration last, and restores all original files if any later
step fails. A prepared/committed transaction journal and per-file digests make
that rollback recoverable after process interruption: the next real import
recovers the original state before attempting another publication. `--dry-run`
reports pending recovery but never performs it or creates a project lock.

Real restore holds an exclusive, versioned project-maintenance marker. All
Paper Galaxy read-only, writer, migration, and backup connections hold the
matching shared lock, so an active process causes an actionable refusal before
publication. Legacy projects are claimed safely and their old database handle
is drained before replacement. The restore does not delete unrelated source
documents or unreferenced project files. Close processes using the project and
retry if the maintenance lock or active SQLite sidecars are reported.
After a hard process kill or power loss, the durable transaction also blocks
all ordinary project connections until the next real import recovers it.

Export, inspection, and restore staging roots are private and carry a strict
operation/target/PID ownership marker. A normal exit removes them. A hard kill
can leave sensitive staging bytes until the next matching invocation, which
deletes only dead, owner-only, exactly marked directories; lookalike or unowned
directories are never glob-deleted.

Legacy version 1 bundles remain inspectable and restore their project metadata
and database to the historical default path. Their basename-only vector-index
entries have no reliable logical mapping and are therefore not restored.

## Verify Restored Project

```bash
paper-galaxy validate-project --project-dir /path/to/restore
paper-galaxy db-stats --project-dir /path/to/restore
paper-galaxy map-runs --project-dir /path/to/restore
```

Do not commit backup ZIPs, `.paper-galaxy/`, SQLite files, or vector indexes to
Git. Backups are not encrypted by Paper Galaxy; use trusted local disk or an
encrypted storage volume when confidentiality matters.
