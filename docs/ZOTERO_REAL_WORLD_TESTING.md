# Zotero Real-World Testing

[English](ZOTERO_REAL_WORLD_TESTING.md) | [简体中文](ZOTERO_REAL_WORLD_TESTING.zh-CN.md)

Use this checklist when testing Paper Galaxy against a real local Zotero
Desktop library. It is designed to avoid writes to Zotero and to avoid printing
full document text.

## Before Import

1. Open Zotero Desktop.
2. Confirm the local API is enabled in Zotero Settings -> Advanced.
3. Work inside a Paper Galaxy project directory that is not committed with
   `.paper-galaxy/` or `*.sqlite3` files.

Run:

```bash
paper-galaxy zotero detect
paper-galaxy zotero status
paper-galaxy zotero doctor --project-dir . --json-out zotero-doctor.json
```

`zotero doctor` is a no-write readiness check. It probes the local API root,
top-level items, collections, tags, a small child-item sample, optional PDF
support, and any existing project database.

## Safe Dry Runs

Preview collections and a small item page:

```bash
paper-galaxy zotero collections --limit 20
paper-galaxy zotero items --limit 20
paper-galaxy zotero items --collection "Collection Name" --limit 20
```

Try imports without writing:

```bash
paper-galaxy zotero import --project-dir . --limit 25 --dry-run --verbose
paper-galaxy zotero import --project-dir . --collection "Collection Name" --dry-run
paper-galaxy zotero import --project-dir . --pdf-policy metadata --dry-run
paper-galaxy zotero import --project-dir . --pdf-policy skip-missing --dry-run
```

`--collection` accepts a collection key, exact name, or path. Name and path
matching are case-insensitive, and ambiguous names fail before import.

## Full Local Import

```bash
paper-galaxy init .
paper-galaxy zotero import --project-dir . --include-pdfs --include-notes --build-reading-map --json-out zotero-import.json
paper-galaxy zotero validate --project-dir .
paper-galaxy validate-project --project-dir .
paper-galaxy serve --project-dir .
```

Open the local app, switch the graph source to Zotero, and inspect:

- point count and cluster labels;
- reading status, tag, and collection filters;
- item inspector metadata, including Zotero key, item type, year, DOI, URL,
  attachment/PDF status, and `Open in Zotero`;
- neighbors generated from high-dimensional TF-IDF similarity.

## Incremental Runs

The importer records Zotero versions from the local API. For large libraries,
use incremental runs after the first import:

```bash
paper-galaxy zotero import --project-dir . --since-version VERSION --json-out zotero-import.json
```

The summary includes fetched, selected, filtered, skipped, unchanged, warning,
PDF, attachment, and annotation counts.

## Status And PDF Policies

Reading status filters accept `all`, `read`, `reading`, `to_read`, and
`unknown`. `unclassified` remains a deprecated alias for `unknown`.

PDF policies:

- `extract`: default; extract local PDF text when possible.
- `metadata`: record attachment metadata only.
- `skip-missing`: skip items that appear to have a PDF but cannot produce local
  PDF text.

## Privacy Notes

Paper Galaxy does not write to Zotero, does not upload Zotero data, does not use
the Zotero online API, and does not copy PDFs by default. Imported metadata,
notes, extracted local PDF text, chunks, and saved reading maps live in the
local Paper Galaxy project database under `.paper-galaxy/`.

Before sharing bug reports, remove private titles, paths, tags, DOI/URL values,
or document excerpts from JSON reports.
