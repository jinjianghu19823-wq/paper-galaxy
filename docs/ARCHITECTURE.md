# Architecture

Paper Galaxy is planned as a local-first Python application with optional
heavier capabilities added phase by phase.

## Pipeline

```text
files
  -> extraction
  -> cleaning
  -> document and chunk records
  -> vectors
  -> similarity graph
  -> 2D projection
  -> clusters
  -> cluster labels
  -> interactive UI
```

The 2D layout is a view. High-dimensional similarity remains the canonical
source for nearest-neighbor search and proximity explanations.

## Planned Modules

- `cli`: command-line entrypoints.
- `config`: project and runtime configuration models.
- `paths`: project path and metadata helpers.
- `logging`: shared console and logging setup.
- `extract`: future local text extraction.
- `clean`: future normalization and cleaning.
- `records`: future document and chunk records.
- `vectors`: future TF-IDF and embedding vectors.
- `graph`: future similarity graph construction.
- `layout`: future 2D projection and stable map runs.
- `clusters`: future clustering and labeling.
- `ui`: future local web interface.

Only the Phase 0 modules exist today.

## Future Data Model Sketch

- `documents`: one row per source document.
- `chunks`: text chunks associated with documents.
- `vectors`: vector records for documents or chunks.
- `clusters`: cluster metadata and labels.
- `document_clusters`: many-to-many document to cluster assignments.
- `edges`: similarity graph edges with scores and provenance.
- `map_runs`: layout runs, parameters, seeds, and output coordinates.

## Local Data Boundary

Project metadata lives under `.paper-galaxy/`. Future database files should also
live there by default, with paths made explicit in project configuration.
