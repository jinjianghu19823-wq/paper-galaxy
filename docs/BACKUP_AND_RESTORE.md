# Backup And Restore

[English](BACKUP_AND_RESTORE.md) | [简体中文](BACKUP_AND_RESTORE.zh-CN.md)

Paper Galaxy backup bundles are local zip files. They contain a manifest,
checksums, a README, project metadata when present, and the SQLite database only
when the user confirms with `--yes`.

## Export

```bash
paper-galaxy export-project --project-dir . --out paper-galaxy-backup.zip --yes
```

Source documents are not included by default. Treat the backup as sensitive
because the SQLite database can contain extracted text, chunks, vectors, saved
map runs, and local labels.

## Dry Run Import

```bash
paper-galaxy import-project paper-galaxy-backup.zip --project-dir /path/to/restore --dry-run
```

## Import

```bash
paper-galaxy import-project paper-galaxy-backup.zip --project-dir /path/to/restore
```

If `/path/to/restore/.paper-galaxy` already exists, import refuses to overwrite
it unless `--force` is passed.

## Verify Restored Project

```bash
paper-galaxy validate-project --project-dir /path/to/restore
paper-galaxy map-runs --project-dir /path/to/restore
```
