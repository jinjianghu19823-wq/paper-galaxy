# Privacy

[English](PRIVACY.md) | [简体中文](PRIVACY.zh-CN.md)

Paper Galaxy is local-first by default.

- User documents stay local by default.
- There is no telemetry.
- There is no automatic upload.
- There is no account requirement.
- The app should be usable offline.
- Phase 2 stores extracted text and chunks locally in
  `.paper-galaxy/paper_galaxy.sqlite3` by default.
- Phase 4 stores compact extraction reports locally in SQLite and can write an
  optional local JSON sidecar. These reports omit full extracted text.
- Phase 5 can store optional dense document/chunk vectors locally in SQLite.
  Vectors are generated only when the user runs `paper-galaxy embed`.
- Phase 6 stores manual cluster label overrides locally in SQLite only.
- Phase 6 pair explanations expose shared terms and short chunk excerpts in the
  local CLI or browser app. These excerpts may still contain sensitive source
  text, but they are not uploaded by Paper Galaxy.
- Phase 7 stores saved map run metadata, point coordinates, cluster labels,
  top terms, and nearest-neighbor summaries locally in SQLite. Saved runs do not
  store full source documents or chunk text.
- Phase 7 validation reports contain counts, schema status, warnings, and
  errors, not full extracted text.
- Phase 7 backup export includes the local SQLite database only when the user
  confirms with `--yes`. Source documents are not included by default.
- Phase 7 plugin metadata is static and local; there is no remote plugin
  loading or extension marketplace.
- Missing and unindexed records may preserve previously extracted local text and
  chunks so the local index can recover document history without rescanning
  unavailable content.
- Phase 3 starts a local server bound to `127.0.0.1` by default.
- The browser app communicates with the local backend only.
- Phase 3 static assets are served locally and do not reference CDNs, remote
  fonts, or external images.
- The browser app does not upload documents, collect telemetry, or call remote
  services.
- Phase 3.1 stores graph UI preferences and manually pinned node positions only
  in browser `localStorage`, keyed by local database identity and document IDs.
  These layout values are not uploaded and are not written to SQLite.
- Optional OCR runs locally only. It may require user-installed local OCR
  binaries such as Tesseract, but Paper Galaxy does not upload images,
  extracted text, OCR output, or extraction reports to a remote service.
- Optional embeddings run locally only. Remote Sentence Transformer model names
  are rejected by default to avoid hidden downloads; using
  `--allow-model-download` is an explicit user opt-in to model resolution.
- Cluster labels and pair explanations are generated locally from indexed text;
  there is no mandatory LLM or remote labeling service.
- Choosing a non-loopback host may expose the app to other devices on the local
  network.
- `.paper-galaxy/` is local project state and is gitignored.
- Deleting `.paper-galaxy/` removes the local Paper Galaxy database and project
  metadata for that project.

Future cloud features, if any, must be opt-in. They should clearly explain what
data leaves the machine, where it goes, and how the user can disable or delete
that data.

## Public Demo

The GitHub Pages demo is static and uses synthetic content from
`examples/tiny_corpus` only. It does not connect to a backend, upload documents,
load remote runtime assets, or include a local SQLite database. The demo JSON
contains metadata, graph points, labels, terms, neighbors, and short sample
explanation excerpts, not full source document text.

The public site includes English and Simplified Chinese pages. Both use the
same synthetic demo data.

## Future Cloud Library Caveat

The personal cloud library docs describe a possible future opt-in design. They
do not implement cloud sync, accounts, hosted indexing, or upload behavior in
the current codebase.

If a cloud feature is ever implemented, backups, vectors, map runs, labels, and
metadata must be treated as sensitive. Users must be able to understand what is
leaving the device before enabling it.
