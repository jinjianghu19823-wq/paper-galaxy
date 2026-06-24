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

Install in an isolated environment on macOS/Linux:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,ml,pdf,app]"
paper-galaxy doctor
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,ml,pdf,app]"
paper-galaxy doctor
```

Then run the no-write Zotero readiness checks:

```bash
paper-galaxy zotero detect
paper-galaxy zotero status
paper-galaxy init .
paper-galaxy zotero doctor --limit 20 --json-out zotero-doctor.json
```

`zotero doctor` is a no-write readiness check. It probes the local API root,
top-level items, collections, tags, a small child-item sample, optional PDF
support, and any existing project database.

## Safe Dry Runs

Preview collections and a small item page:

```bash
paper-galaxy zotero collections --limit 20
paper-galaxy zotero items --limit 10
paper-galaxy zotero items --collection "Collection Name" --limit 20
```

Try imports without writing:

```bash
paper-galaxy zotero import --project-dir . --dry-run --limit 10 --json-out zotero-dry-run.json
paper-galaxy zotero import --project-dir . --collection "Collection Name" --dry-run
paper-galaxy zotero import --project-dir . --pdf-policy metadata --dry-run
paper-galaxy zotero import --project-dir . --pdf-policy skip-missing --dry-run
```

`--collection` accepts a collection key, exact name, or path. Name and path
matching are case-insensitive, and ambiguous names fail before import.

## Full Local Import

```bash
paper-galaxy zotero import --project-dir . --include-pdfs --include-notes --pdf-policy extract --build-reading-map --json-out zotero-import.json
paper-galaxy zotero validate --project-dir .
paper-galaxy validate-project --project-dir .
paper-galaxy serve --project-dir . --open
```

Windows PowerShell uses the same Paper Galaxy commands after activation:

```powershell
paper-galaxy zotero doctor --limit 20 --json-out zotero-doctor.json
paper-galaxy zotero import --project-dir . --dry-run --limit 10 --json-out zotero-dry-run.json
paper-galaxy zotero import --project-dir . --include-pdfs --include-notes --pdf-policy extract --build-reading-map --json-out zotero-import.json
paper-galaxy serve --project-dir . --open
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

## Troubleshooting

- Zotero Desktop must be open while the local API is queried.
- If `http://localhost:23119/api` is unavailable, enable the local API in
  Zotero Settings -> Advanced and restart Zotero.
- If data-directory detection is wrong, pass `--data-dir /path/to/Zotero`.
- The Zotero data directory shown in Zotero settings is authoritative.
- A missing `storage/` folder means stored Zotero PDFs may not resolve, but
  metadata-only import can still work.
- Linked PDFs outside the Zotero data directory are referenced in place and not
  copied.
- Missing PDFs do not necessarily block import when `--include-metadata-only`
  is enabled or `--pdf-policy metadata` is used.
- Always run a small dry-run before a full import on a large library.

## Manual Verification Checklist

- `zotero doctor`: readiness is `ready` or an explainable `warning`; API URL is
  shown; data directory and `storage/` status are reported; a non-empty library
  shows item/sample counts greater than zero.
- `zotero items --limit 5`: prints a top-level item table.
- `zotero items --collection <name>`: prints only items from that collection.
- Dry-run import: JSON has `"dry_run": true`; if the project database did not
  already exist, no database is created.
- Full import: imported/updated/unchanged counts are shown, warnings are
  summarized, and `map_run_id` is present when enough documents can be mapped.
- `zotero validate`: reports imported counts and dangling-link counts.
- Browser app: choose Zotero source, see points, click a point, and inspect
  Zotero key, creators, year, DOI/URL, tags, collections, PDF status, and Open
  in Zotero.

## Privacy Notes

Paper Galaxy does not write to Zotero, does not upload Zotero data, does not use
the Zotero online API, and does not copy PDFs by default. Imported metadata,
notes, extracted local PDF text, chunks, and saved reading maps live in the
local Paper Galaxy project database under `.paper-galaxy/`.

Before sharing bug reports, remove private titles, paths, tags, DOI/URL values,
or document excerpts from JSON reports.

Remove local Paper Galaxy state when you want a clean retry:

```bash
rm -rf .paper-galaxy
```

Windows PowerShell:

```powershell
Remove-Item -Recurse -Force .paper-galaxy
```
