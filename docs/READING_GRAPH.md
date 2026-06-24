# Reading Graph

[English](READING_GRAPH.md) | [简体中文](READING_GRAPH.zh-CN.md)

The Paper Galaxy reading graph is a saved local map focused on documents that
come from a reading workflow. The first concrete reading source is Zotero:
imported Zotero papers, notes, tags, collections, and local PDF text become a
`Zotero Reading Graph`.

## What The Graph Shows

- One point per imported active Zotero document.
- Cluster labels generated from local TF-IDF evidence.
- Nearest-neighbor links computed from high-dimensional TF-IDF cosine
  similarity, not from 2D map distance.
- Inspector metadata for Zotero key, creators, year, publication, tags,
  collections, attachment status, and reading status.
- Filters for reading status, tag, and collection in the local app.

The graph can include metadata-only documents. A Zotero item does not need a
readable PDF to appear; its abstract, title, creators, notes, tags, and
collections still contribute useful text.

## Reading Status

The first version infers a simple local status from tags:

- `read`
- `reading`
- `to_read`
- `unknown`

The import command accepts repeatable `--read-tag`, `--reading-tag`, and
`--to-read-tag` options so users can map their own Zotero tag vocabulary without
writing to Zotero.

The old `unclassified` spelling is accepted as a deprecated alias for
`unknown`, but new scripts should use `unknown`.

## Saved Map Run

`paper-galaxy zotero import --build-reading-map` creates a saved map run named
`Zotero Reading Graph`. It uses the same local saved-map infrastructure as
regular TF-IDF map runs, with metadata marking the source as Zotero.

You can rebuild it explicitly:

```bash
paper-galaxy zotero graph --project-dir . --name "Zotero Reading Graph"
```

The local web app can render the current live Zotero reading map through
`/api/zotero/reading-map`. The saved run remains useful as a stable snapshot of
the imported library at a point in time.

## Privacy Boundary

The reading graph is local project state. It does not upload documents, does not
write to Zotero, and does not copy PDFs by default. Imported full text can still
be sensitive, so `.paper-galaxy/` and `*.sqlite3` files should stay private and
gitignored.

## Current Limits

- The default method is TF-IDF. Dense or hybrid reading graphs can be added
  later from the existing optional embedding layer.
- Zotero import is one-way into Paper Galaxy. There is no Zotero write-back.
- Browser graph positions are still local UI state in `localStorage`; they are
  not written to SQLite.
- Cloud sync is a separate future design and is not implemented.
