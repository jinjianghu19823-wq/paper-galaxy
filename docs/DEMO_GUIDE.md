# Public Demo Guide

[English](DEMO_GUIDE.md) | [简体中文](DEMO_GUIDE.zh-CN.md)

The public demo is a static GitHub Pages site:
<https://jinjianghu19823-wq.github.io/paper-galaxy/>

## How It Works

The demo is generated from the synthetic `examples/tiny_corpus` fixture. The
build script indexes that corpus in a temporary project, builds a TF-IDF map
payload, strips local paths and database details, and writes static JSON to
`site/data/tiny-map.json`.

## What Is Synthetic

All demo documents are synthetic notes about neural operators, numerical PDEs,
randomized linear algebra, and thesis ideas. No user papers or private
documents are included. The demo contains no real Zotero database, no Zotero
storage folder, no PDFs, no local Zotero paths, and no `zotero://items/...`
records.

## Simulated Versus Real Features

The public demo has a static graph, cluster legend, document inspector, and
precomputed explanation snippets. It does not run the FastAPI backend, mutate a
SQLite database, re-index files, or read local documents.

The installed local app can read your local project database, run local search,
show document chunks, use saved map runs, rename clusters, and inspect pair
explanations from your indexed corpus. It can also show a Zotero Reading Graph
after you explicitly import from Zotero Desktop on your own computer.

## Reproduce The Demo Locally

```bash
python scripts/build_demo_site.py --out site_dist
python scripts/check_demo_site.py --dist site_dist --serve
```

Then open the local server URL printed by the check command, or inspect the
generated `site_dist/` directory. Do not commit `site_dist/`; it is generated.
