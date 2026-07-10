# Public Demo Guide

[English](DEMO_GUIDE.md) | [简体中文](DEMO_GUIDE.zh-CN.md)

The public demo is a static GitHub Pages site:
<https://jinjianghu19823-wq.github.io/paper-galaxy/>

## How It Works

The demo is generated from the synthetic `examples/tiny_corpus` fixture. The
build script indexes that corpus in a temporary project, builds a TF-IDF map
payload, strips local paths and database details, and builds the complete site
in a unique sibling staging directory. It validates that staging tree before
publishing it with crash-recoverable sibling renames. Generated JSON is written
only to `site_dist/data/tiny-map.json` by default.

An existing output is replaceable only when it is empty or contains the
supported `.paper-galaxy-demo-build.json` ownership marker. Non-empty unowned
directories, symlinked path components, the filesystem root, the user home,
the repository root, Git metadata, the site source, and the corpus are rejected.
Build or publish failures preserve the previous output and are recovered on the
next run without exposing a half-built site. On macOS, the root-owned system
alias `/tmp` is normalized to `/private/tmp`; user-created symlinks remain
forbidden.

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
The default build does not modify `site/`. Public artifact floats are finite,
rounded to at most eight decimal places, and serialized with stable UTF-8 JSON
settings. If a legacy unmarked `site_dist/` exists, use the build-only
`make clean-build` target before rebuilding; do not point `--out` at a personal
or project data directory.

To intentionally refresh the committed source fixture after a reviewed payload
change, run this explicit operation once and inspect the diff:

```bash
python scripts/build_demo_site.py --out site_dist --refresh-source-data
git diff -- site/data/tiny-map.json
```

CI, Pages, release checks, and normal demo builds must not use
`--refresh-source-data`.

The committed fixture was explicitly refreshed for the eight-decimal numeric
contract and canonical cluster ordering. Subsequent default builds must leave
the worktree unchanged.
