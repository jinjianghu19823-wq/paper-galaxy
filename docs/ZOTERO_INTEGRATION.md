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
paper-galaxy zotero import --project-dir . --include-pdfs --include-notes --build-reading-map
paper-galaxy serve --project-dir .
```

Open the local app and switch the graph source to Zotero to inspect the saved
`Zotero Reading Graph`.

## What Gets Imported

- Zotero item metadata: title, item type, year, publication title, DOI, URL,
  abstract, date, version, and Zotero key.
- Creators, tags, and collection membership.
- Child notes when `--include-notes` is enabled.
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

Paper Galaxy never writes to Zotero. There is no Zotero OAuth, no online Zotero
Web API sync path, no cloud sync, and no hosted account system in this feature.

## CLI Commands

- `paper-galaxy zotero detect`: best-effort local API and data-directory
  detection.
- `paper-galaxy zotero status`: check whether Zotero Desktop local API is
  reachable.
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
`--include-status`, `--limit`, `--since-version`, `--dry-run`, `--force`, and
`--build-reading-map`.

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

For deeper graph behavior, see [READING_GRAPH.md](READING_GRAPH.md).
