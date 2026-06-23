CREATE TABLE IF NOT EXISTS schema_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS corpora (
  id TEXT PRIMARY KEY,
  root_path TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scan_runs (
  id TEXT PRIMARY KEY,
  corpus_id TEXT NOT NULL,
  corpus_path TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  files_found INTEGER NOT NULL DEFAULT 0,
  documents_inserted INTEGER NOT NULL DEFAULT 0,
  documents_updated INTEGER NOT NULL DEFAULT 0,
  documents_unchanged INTEGER NOT NULL DEFAULT 0,
  documents_missing INTEGER NOT NULL DEFAULT 0,
  skipped_files INTEGER NOT NULL DEFAULT 0,
  chunks_written INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  FOREIGN KEY(corpus_id) REFERENCES corpora(id)
);

CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,
  corpus_id TEXT NOT NULL,
  path TEXT NOT NULL,
  relative_path TEXT NOT NULL,
  file_type TEXT NOT NULL,
  title TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  mtime_ns INTEGER NOT NULL,
  char_count INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(corpus_id, relative_path),
  FOREIGN KEY(corpus_id) REFERENCES corpora(id)
);

CREATE TABLE IF NOT EXISTS document_texts (
  document_id TEXT PRIMARY KEY,
  text TEXT NOT NULL,
  FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chunks (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL,
  chunk_index INTEGER NOT NULL,
  text TEXT NOT NULL,
  char_count INTEGER NOT NULL,
  FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE,
  UNIQUE(document_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS skipped_files (
  id TEXT PRIMARY KEY,
  scan_run_id TEXT NOT NULL,
  corpus_id TEXT NOT NULL,
  relative_path TEXT NOT NULL,
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(scan_run_id) REFERENCES scan_runs(id),
  FOREIGN KEY(corpus_id) REFERENCES corpora(id)
);

CREATE TABLE IF NOT EXISTS extraction_reports (
  id TEXT PRIMARY KEY,
  scan_run_id TEXT NOT NULL,
  document_id TEXT,
  corpus_id TEXT NOT NULL,
  relative_path TEXT NOT NULL,
  file_type TEXT NOT NULL,
  method TEXT NOT NULL,
  status TEXT NOT NULL,
  char_count INTEGER NOT NULL DEFAULT 0,
  warnings_json TEXT NOT NULL DEFAULT '[]',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  FOREIGN KEY(scan_run_id) REFERENCES scan_runs(id),
  FOREIGN KEY(document_id) REFERENCES documents(id),
  FOREIGN KEY(corpus_id) REFERENCES corpora(id)
);

CREATE TABLE IF NOT EXISTS embedding_models (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  provider TEXT NOT NULL,
  dimension INTEGER NOT NULL,
  distance TEXT NOT NULL,
  config_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  UNIQUE(name, provider, dimension, distance, config_json)
);

CREATE TABLE IF NOT EXISTS vectors (
  id TEXT PRIMARY KEY,
  model_id TEXT NOT NULL,
  object_type TEXT NOT NULL,
  object_id TEXT NOT NULL,
  text_sha256 TEXT NOT NULL,
  dimension INTEGER NOT NULL,
  dtype TEXT NOT NULL,
  vector BLOB NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(model_id) REFERENCES embedding_models(id),
  UNIQUE(model_id, object_type, object_id)
);

CREATE TABLE IF NOT EXISTS embedding_runs (
  id TEXT PRIMARY KEY,
  model_id TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  documents_seen INTEGER NOT NULL DEFAULT 0,
  documents_embedded INTEGER NOT NULL DEFAULT 0,
  documents_unchanged INTEGER NOT NULL DEFAULT 0,
  chunks_seen INTEGER NOT NULL DEFAULT 0,
  chunks_embedded INTEGER NOT NULL DEFAULT 0,
  chunks_unchanged INTEGER NOT NULL DEFAULT 0,
  errors INTEGER NOT NULL DEFAULT 0,
  config_json TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY(model_id) REFERENCES embedding_models(id)
);

CREATE TABLE IF NOT EXISTS vector_indexes (
  id TEXT PRIMARY KEY,
  model_id TEXT NOT NULL,
  object_type TEXT NOT NULL,
  index_path TEXT NOT NULL,
  vector_count INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY(model_id) REFERENCES embedding_models(id)
);

CREATE TABLE IF NOT EXISTS cluster_label_overrides (
  id TEXT PRIMARY KEY,
  cluster_signature TEXT NOT NULL UNIQUE,
  label TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'manual',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
  document_id UNINDEXED,
  title,
  relative_path,
  text
);

CREATE INDEX IF NOT EXISTS idx_documents_corpus_relative_path
  ON documents(corpus_id, relative_path);

CREATE INDEX IF NOT EXISTS idx_documents_corpus_status
  ON documents(corpus_id, status);

CREATE INDEX IF NOT EXISTS idx_documents_sha256
  ON documents(sha256);

CREATE INDEX IF NOT EXISTS idx_chunks_document_id
  ON chunks(document_id);

CREATE INDEX IF NOT EXISTS idx_scan_runs_corpus_started_at
  ON scan_runs(corpus_id, started_at);

CREATE INDEX IF NOT EXISTS idx_extraction_reports_scan_run_id
  ON extraction_reports(scan_run_id);

CREATE INDEX IF NOT EXISTS idx_extraction_reports_document_id
  ON extraction_reports(document_id);

CREATE INDEX IF NOT EXISTS idx_extraction_reports_status
  ON extraction_reports(status);

CREATE INDEX IF NOT EXISTS idx_extraction_reports_corpus_relative_path
  ON extraction_reports(corpus_id, relative_path);

CREATE INDEX IF NOT EXISTS idx_vectors_model_object_type
  ON vectors(model_id, object_type);

CREATE INDEX IF NOT EXISTS idx_vectors_object
  ON vectors(object_type, object_id);

CREATE INDEX IF NOT EXISTS idx_vectors_text_sha256
  ON vectors(text_sha256);

CREATE INDEX IF NOT EXISTS idx_embedding_runs_model_started_at
  ON embedding_runs(model_id, started_at);

CREATE INDEX IF NOT EXISTS idx_cluster_label_overrides_signature
  ON cluster_label_overrides(cluster_signature);
