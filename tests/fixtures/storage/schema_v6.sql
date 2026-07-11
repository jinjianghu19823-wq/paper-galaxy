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

CREATE TABLE IF NOT EXISTS map_runs (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL,
  status TEXT NOT NULL,
  similarity_mode TEXT NOT NULL,
  model_id TEXT,
  seed INTEGER NOT NULL,
  requested_clusters INTEGER,
  requested_neighbors INTEGER NOT NULL,
  requested_limit INTEGER NOT NULL,
  document_count INTEGER NOT NULL,
  cluster_count INTEGER NOT NULL,
  document_set_signature TEXT NOT NULL,
  warnings_json TEXT NOT NULL DEFAULT '[]',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY(model_id) REFERENCES embedding_models(id)
);

CREATE TABLE IF NOT EXISTS map_run_points (
  map_run_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  x REAL NOT NULL,
  y REAL NOT NULL,
  cluster_id INTEGER NOT NULL,
  cluster_label TEXT NOT NULL,
  cluster_signature TEXT NOT NULL,
  top_terms_json TEXT NOT NULL DEFAULT '[]',
  nearest_neighbors_json TEXT NOT NULL DEFAULT '[]',
  PRIMARY KEY(map_run_id, document_id),
  FOREIGN KEY(map_run_id) REFERENCES map_runs(id) ON DELETE CASCADE,
  FOREIGN KEY(document_id) REFERENCES documents(id)
);

CREATE TABLE IF NOT EXISTS map_run_clusters (
  map_run_id TEXT NOT NULL,
  cluster_id INTEGER NOT NULL,
  cluster_signature TEXT NOT NULL,
  display_label TEXT NOT NULL,
  generated_label TEXT NOT NULL,
  source TEXT NOT NULL,
  size INTEGER NOT NULL,
  document_ids_json TEXT NOT NULL DEFAULT '[]',
  top_terms_json TEXT NOT NULL DEFAULT '[]',
  representatives_json TEXT NOT NULL DEFAULT '[]',
  warnings_json TEXT NOT NULL DEFAULT '[]',
  PRIMARY KEY(map_run_id, cluster_id),
  FOREIGN KEY(map_run_id) REFERENCES map_runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS zotero_sources (
  id TEXT PRIMARY KEY,
  source_type TEXT NOT NULL,
  local_api_url TEXT,
  data_dir TEXT,
  library_id TEXT,
  library_type TEXT,
  name TEXT NOT NULL,
  last_version INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS zotero_import_runs (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  items_seen INTEGER NOT NULL DEFAULT 0,
  items_imported INTEGER NOT NULL DEFAULT 0,
  items_updated INTEGER NOT NULL DEFAULT 0,
  items_unchanged INTEGER NOT NULL DEFAULT 0,
  attachments_seen INTEGER NOT NULL DEFAULT 0,
  attachments_resolved INTEGER NOT NULL DEFAULT 0,
  pdfs_extracted INTEGER NOT NULL DEFAULT 0,
  notes_imported INTEGER NOT NULL DEFAULT 0,
  skipped INTEGER NOT NULL DEFAULT 0,
  warnings_json TEXT NOT NULL DEFAULT '[]',
  config_json TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY(source_id) REFERENCES zotero_sources(id)
);

CREATE TABLE IF NOT EXISTS zotero_items (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  zotero_key TEXT NOT NULL,
  version INTEGER,
  item_type TEXT NOT NULL,
  title TEXT NOT NULL,
  year TEXT,
  date TEXT,
  date_added TEXT,
  date_modified TEXT,
  publication_title TEXT,
  doi TEXT,
  url TEXT,
  abstract_note TEXT,
  extra TEXT,
  reading_status TEXT NOT NULL DEFAULT 'unknown',
  data_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(source_id, zotero_key),
  FOREIGN KEY(source_id) REFERENCES zotero_sources(id)
);

CREATE TABLE IF NOT EXISTS zotero_creators (
  id TEXT PRIMARY KEY,
  zotero_item_id TEXT NOT NULL,
  creator_type TEXT NOT NULL,
  first_name TEXT,
  last_name TEXT,
  name TEXT,
  order_index INTEGER NOT NULL,
  FOREIGN KEY(zotero_item_id) REFERENCES zotero_items(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS zotero_collections (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  zotero_key TEXT NOT NULL,
  parent_key TEXT,
  name TEXT NOT NULL,
  path TEXT,
  version INTEGER,
  data_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE(source_id, zotero_key),
  FOREIGN KEY(source_id) REFERENCES zotero_sources(id)
);

CREATE TABLE IF NOT EXISTS zotero_item_collections (
  zotero_item_id TEXT NOT NULL,
  collection_id TEXT NOT NULL,
  PRIMARY KEY(zotero_item_id, collection_id),
  FOREIGN KEY(zotero_item_id) REFERENCES zotero_items(id) ON DELETE CASCADE,
  FOREIGN KEY(collection_id) REFERENCES zotero_collections(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS zotero_item_tags (
  zotero_item_id TEXT NOT NULL,
  tag TEXT NOT NULL,
  tag_type INTEGER,
  PRIMARY KEY(zotero_item_id, tag),
  FOREIGN KEY(zotero_item_id) REFERENCES zotero_items(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS zotero_attachments (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  parent_zotero_item_id TEXT,
  zotero_key TEXT NOT NULL,
  title TEXT,
  filename TEXT,
  content_type TEXT,
  link_mode TEXT,
  zotero_path TEXT,
  resolved_path TEXT,
  path_status TEXT NOT NULL,
  version INTEGER,
  data_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(source_id, zotero_key),
  FOREIGN KEY(source_id) REFERENCES zotero_sources(id),
  FOREIGN KEY(parent_zotero_item_id) REFERENCES zotero_items(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS zotero_document_links (
  document_id TEXT NOT NULL,
  zotero_item_id TEXT NOT NULL,
  attachment_id TEXT,
  role TEXT NOT NULL,
  PRIMARY KEY(document_id, zotero_item_id, role),
  FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE,
  FOREIGN KEY(zotero_item_id) REFERENCES zotero_items(id) ON DELETE CASCADE,
  FOREIGN KEY(attachment_id) REFERENCES zotero_attachments(id) ON DELETE SET NULL
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

CREATE INDEX IF NOT EXISTS idx_map_runs_created_at
  ON map_runs(created_at);

CREATE INDEX IF NOT EXISTS idx_map_runs_document_set_signature
  ON map_runs(document_set_signature);

CREATE INDEX IF NOT EXISTS idx_map_run_points_document_id
  ON map_run_points(document_id);

CREATE INDEX IF NOT EXISTS idx_map_run_clusters_signature
  ON map_run_clusters(cluster_signature);

CREATE INDEX IF NOT EXISTS idx_zotero_items_source_key
  ON zotero_items(source_id, zotero_key);

CREATE INDEX IF NOT EXISTS idx_zotero_items_reading_status
  ON zotero_items(reading_status);

CREATE INDEX IF NOT EXISTS idx_zotero_items_title
  ON zotero_items(title);

CREATE INDEX IF NOT EXISTS idx_zotero_collections_source_key
  ON zotero_collections(source_id, zotero_key);

CREATE INDEX IF NOT EXISTS idx_zotero_item_tags_tag
  ON zotero_item_tags(tag);

CREATE INDEX IF NOT EXISTS idx_zotero_attachments_source_key
  ON zotero_attachments(source_id, zotero_key);

CREATE INDEX IF NOT EXISTS idx_zotero_document_links_item
  ON zotero_document_links(zotero_item_id);

CREATE INDEX IF NOT EXISTS idx_zotero_document_links_document
  ON zotero_document_links(document_id);
