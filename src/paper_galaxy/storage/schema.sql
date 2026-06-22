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
