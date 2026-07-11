"""Transactional bootstrap and ordered migrations for project databases."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import sqlite3
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Protocol
from urllib.parse import SplitResult, urlsplit, urlunsplit
from uuid import uuid4

from paper_galaxy.errors import (
    DatabaseCorruptError,
    DatabaseError,
    DatabaseLockedError,
    FTSUnavailableError,
    FutureSchemaError,
    UnsupportedSchemaError,
)
from paper_galaxy.storage.provenance import (
    document_content_revision_sha256,
    registered_source_identity,
)

CURRENT_SCHEMA_VERSION = 9
SCHEMA_VERSION = str(CURRENT_SCHEMA_VERSION)
OLDEST_SUPPORTED_SCHEMA_VERSION = 6
LEGACY_UNKNOWN_PROVENANCE = "legacy-unknown"


def _columns(names: str) -> frozenset[str]:
    return frozenset(names.split())


# This is the capability signature of the real v6 schema preserved in
# tests/fixtures/storage/schema_v6.sql.  It intentionally describes schema
# identity, not row-level integrity; validation.py owns the latter.
_V6_REQUIRED_COLUMNS: dict[str, frozenset[str]] = {
    "schema_meta": _columns("key value"),
    "corpora": _columns("id root_path created_at updated_at"),
    "scan_runs": _columns(
        "id corpus_id corpus_path started_at finished_at files_found "
        "documents_inserted documents_updated documents_unchanged "
        "documents_missing skipped_files chunks_written status"
    ),
    "documents": _columns(
        "id corpus_id path relative_path file_type title sha256 size_bytes "
        "mtime_ns char_count status first_seen_at last_seen_at updated_at"
    ),
    "document_texts": _columns("document_id text"),
    "chunks": _columns("id document_id chunk_index text char_count"),
    "skipped_files": _columns(
        "id scan_run_id corpus_id relative_path reason created_at"
    ),
    "extraction_reports": _columns(
        "id scan_run_id document_id corpus_id relative_path file_type method "
        "status char_count warnings_json metadata_json created_at"
    ),
    "embedding_models": _columns(
        "id name provider dimension distance config_json created_at"
    ),
    "vectors": _columns(
        "id model_id object_type object_id text_sha256 dimension dtype vector "
        "metadata_json created_at updated_at"
    ),
    "embedding_runs": _columns(
        "id model_id started_at finished_at status documents_seen "
        "documents_embedded documents_unchanged chunks_seen chunks_embedded "
        "chunks_unchanged errors config_json"
    ),
    "vector_indexes": _columns(
        "id model_id object_type index_path vector_count created_at metadata_json"
    ),
    "cluster_label_overrides": _columns(
        "id cluster_signature label source metadata_json created_at updated_at"
    ),
    "map_runs": _columns(
        "id name created_at status similarity_mode model_id seed "
        "requested_clusters requested_neighbors requested_limit document_count "
        "cluster_count document_set_signature warnings_json metadata_json"
    ),
    "map_run_points": _columns(
        "map_run_id document_id x y cluster_id cluster_label cluster_signature "
        "top_terms_json nearest_neighbors_json"
    ),
    "map_run_clusters": _columns(
        "map_run_id cluster_id cluster_signature display_label generated_label "
        "source size document_ids_json top_terms_json representatives_json "
        "warnings_json"
    ),
    "zotero_sources": _columns(
        "id source_type local_api_url data_dir library_id library_type name "
        "last_version created_at updated_at"
    ),
    "zotero_import_runs": _columns(
        "id source_id started_at finished_at status items_seen items_imported "
        "items_updated items_unchanged attachments_seen attachments_resolved "
        "pdfs_extracted notes_imported skipped warnings_json config_json"
    ),
    "zotero_items": _columns(
        "id source_id zotero_key version item_type title year date date_added "
        "date_modified publication_title doi url abstract_note extra "
        "reading_status data_json created_at updated_at"
    ),
    "zotero_creators": _columns(
        "id zotero_item_id creator_type first_name last_name name order_index"
    ),
    "zotero_collections": _columns(
        "id source_id zotero_key parent_key name path version data_json"
    ),
    "zotero_item_collections": _columns("zotero_item_id collection_id"),
    "zotero_item_tags": _columns("zotero_item_id tag tag_type"),
    "zotero_attachments": _columns(
        "id source_id parent_zotero_item_id zotero_key title filename "
        "content_type link_mode zotero_path resolved_path path_status version "
        "data_json created_at updated_at"
    ),
    "zotero_document_links": _columns("document_id zotero_item_id attachment_id role"),
}

_V7_REQUIRED_COLUMNS = {
    **_V6_REQUIRED_COLUMNS,
    "schema_migrations": _columns("version name applied_at"),
    "scan_runs": _V6_REQUIRED_COLUMNS["scan_runs"]
    | _columns("error_code error_message"),
    "embedding_runs": _V6_REQUIRED_COLUMNS["embedding_runs"]
    | _columns("error_code error_message"),
    "zotero_import_runs": _V6_REQUIRED_COLUMNS["zotero_import_runs"]
    | _columns("error_code error_message"),
    "zotero_items": _V6_REQUIRED_COLUMNS["zotero_items"]
    | _columns("child_manifest_json"),
}

_V8_REQUIRED_COLUMNS = {
    **_V7_REQUIRED_COLUMNS,
    "scan_runs": _V7_REQUIRED_COLUMNS["scan_runs"] | _columns("owner_pid"),
    "documents": _V7_REQUIRED_COLUMNS["documents"]
    | _columns("content_revision_sha256"),
    "chunks": _V7_REQUIRED_COLUMNS["chunks"] | _columns("text_sha256"),
    "embedding_models": _V7_REQUIRED_COLUMNS["embedding_models"]
    | _columns("model_fingerprint fingerprint_algorithm"),
    "vectors": _V7_REQUIRED_COLUMNS["vectors"]
    | _columns("source_content_sha256 model_fingerprint algorithm_version"),
    "embedding_runs": _V7_REQUIRED_COLUMNS["embedding_runs"]
    | _columns("sources_changed owner_pid"),
    "vector_indexes": _V7_REQUIRED_COLUMNS["vector_indexes"]
    | _columns("model_fingerprint algorithm_version vector_set_sha256"),
    "zotero_import_runs": _V7_REQUIRED_COLUMNS["zotero_import_runs"]
    | _columns("owner_pid"),
}

_CURRENT_REQUIRED_COLUMNS = {
    **_V8_REQUIRED_COLUMNS,
    "registered_sources": _columns(
        "id kind display_name root_path zotero_source_id profile_signature "
        "config_json last_success_at last_error_code last_error_message "
        "created_at updated_at removed_at"
    ),
    "jobs": _columns(
        "id queue_sequence kind source_id request_key status params_json "
        "progress_current "
        "progress_total message result_summary_json error_code error_message "
        "cancel_requested owner_pid owner_instance_id heartbeat_at writer_slot "
        "revision created_at started_at finished_at updated_at"
    ),
}

_V6_FORBIDDEN_COLUMNS: dict[str, frozenset[str]] = {
    "scan_runs": _columns("error_code error_message owner_pid"),
    "documents": _columns("content_revision_sha256"),
    "chunks": _columns("text_sha256"),
    "embedding_models": _columns("model_fingerprint fingerprint_algorithm"),
    "vectors": _columns("source_content_sha256 model_fingerprint algorithm_version"),
    "embedding_runs": _columns("error_code error_message sources_changed owner_pid"),
    "vector_indexes": _columns("model_fingerprint algorithm_version vector_set_sha256"),
    "zotero_import_runs": _columns("error_code error_message owner_pid"),
    "zotero_items": _columns("child_manifest_json"),
}

_V7_FORBIDDEN_COLUMNS: dict[str, frozenset[str]] = {
    "scan_runs": _columns("owner_pid"),
    "documents": _columns("content_revision_sha256"),
    "chunks": _columns("text_sha256"),
    "embedding_models": _columns("model_fingerprint fingerprint_algorithm"),
    "vectors": _columns("source_content_sha256 model_fingerprint algorithm_version"),
    "embedding_runs": _columns("sources_changed owner_pid"),
    "vector_indexes": _columns("model_fingerprint algorithm_version vector_set_sha256"),
    "zotero_import_runs": _columns("owner_pid"),
}

_V8_FORBIDDEN_TABLES = frozenset({"registered_sources", "jobs"})

_REQUIRED_INDEXES: dict[str, tuple[str, tuple[str, ...]]] = {
    "idx_chunks_document_id": ("chunks", ("document_id",)),
    "idx_cluster_label_overrides_signature": (
        "cluster_label_overrides",
        ("cluster_signature",),
    ),
    "idx_documents_corpus_relative_path": (
        "documents",
        ("corpus_id", "relative_path"),
    ),
    "idx_documents_corpus_status": ("documents", ("corpus_id", "status")),
    "idx_documents_sha256": ("documents", ("sha256",)),
    "idx_embedding_runs_model_started_at": (
        "embedding_runs",
        ("model_id", "started_at"),
    ),
    "idx_extraction_reports_corpus_relative_path": (
        "extraction_reports",
        ("corpus_id", "relative_path"),
    ),
    "idx_extraction_reports_document_id": (
        "extraction_reports",
        ("document_id",),
    ),
    "idx_extraction_reports_scan_run_id": (
        "extraction_reports",
        ("scan_run_id",),
    ),
    "idx_extraction_reports_status": ("extraction_reports", ("status",)),
    "idx_map_run_clusters_signature": (
        "map_run_clusters",
        ("cluster_signature",),
    ),
    "idx_map_run_points_document_id": ("map_run_points", ("document_id",)),
    "idx_map_runs_created_at": ("map_runs", ("created_at",)),
    "idx_map_runs_document_set_signature": (
        "map_runs",
        ("document_set_signature",),
    ),
    "idx_scan_runs_corpus_started_at": (
        "scan_runs",
        ("corpus_id", "started_at"),
    ),
    "idx_vectors_model_object_type": (
        "vectors",
        ("model_id", "object_type"),
    ),
    "idx_vectors_object": ("vectors", ("object_type", "object_id")),
    "idx_vectors_text_sha256": ("vectors", ("text_sha256",)),
    "idx_zotero_attachments_source_key": (
        "zotero_attachments",
        ("source_id", "zotero_key"),
    ),
    "idx_zotero_collections_source_key": (
        "zotero_collections",
        ("source_id", "zotero_key"),
    ),
    "idx_zotero_document_links_document": (
        "zotero_document_links",
        ("document_id",),
    ),
    "idx_zotero_document_links_item": (
        "zotero_document_links",
        ("zotero_item_id",),
    ),
    "idx_zotero_item_tags_tag": ("zotero_item_tags", ("tag",)),
    "idx_zotero_items_reading_status": ("zotero_items", ("reading_status",)),
    "idx_zotero_items_source_key": (
        "zotero_items",
        ("source_id", "zotero_key"),
    ),
    "idx_zotero_items_title": ("zotero_items", ("title",)),
}

_V8_REQUIRED_INDEXES = {
    **_REQUIRED_INDEXES,
    "idx_chunks_text_sha256": ("chunks", ("text_sha256",)),
}

_CURRENT_REQUIRED_INDEXES = {
    **_V8_REQUIRED_INDEXES,
    "idx_registered_sources_active": (
        "registered_sources",
        ("kind", "removed_at", "display_name", "id"),
    ),
    "idx_registered_sources_zotero": (
        "registered_sources",
        ("zotero_source_id",),
    ),
    "idx_jobs_status_created_at": ("jobs", ("status", "queue_sequence")),
    "idx_jobs_active_dedupe": ("jobs", ("request_key",)),
    "idx_jobs_single_writer": ("jobs", ("writer_slot",)),
    "idx_jobs_source_created_at": ("jobs", ("source_id", "created_at")),
}

_CURRENT_REQUIRED_INDEX_SQL: dict[str, str] = {
    "idx_jobs_active_dedupe": (
        "create unique index idx_jobs_active_dedupe on jobs(request_key) "
        "where status in('queued','running','cancelling')"
    ),
    "idx_jobs_single_writer": (
        "create unique index idx_jobs_single_writer on jobs(writer_slot) "
        "where status in('running','cancelling')"
    ),
}

_V6_REQUIRED_PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "schema_meta": ("key",),
    "corpora": ("id",),
    "scan_runs": ("id",),
    "documents": ("id",),
    "document_texts": ("document_id",),
    "chunks": ("id",),
    "skipped_files": ("id",),
    "extraction_reports": ("id",),
    "embedding_models": ("id",),
    "vectors": ("id",),
    "embedding_runs": ("id",),
    "vector_indexes": ("id",),
    "cluster_label_overrides": ("id",),
    "map_runs": ("id",),
    "map_run_points": ("map_run_id", "document_id"),
    "map_run_clusters": ("map_run_id", "cluster_id"),
    "zotero_sources": ("id",),
    "zotero_import_runs": ("id",),
    "zotero_items": ("id",),
    "zotero_creators": ("id",),
    "zotero_collections": ("id",),
    "zotero_item_collections": ("zotero_item_id", "collection_id"),
    "zotero_item_tags": ("zotero_item_id", "tag"),
    "zotero_attachments": ("id",),
    "zotero_document_links": ("document_id", "zotero_item_id", "role"),
}

_V7_REQUIRED_PRIMARY_KEYS = {
    **_V6_REQUIRED_PRIMARY_KEYS,
    "schema_migrations": ("version",),
}

_V8_REQUIRED_PRIMARY_KEYS = _V7_REQUIRED_PRIMARY_KEYS

_CURRENT_REQUIRED_PRIMARY_KEYS = {
    **_V8_REQUIRED_PRIMARY_KEYS,
    "registered_sources": ("id",),
    "jobs": ("id",),
}

_V6_REQUIRED_UNIQUE_KEYS: dict[str, tuple[tuple[str, ...], ...]] = {
    "documents": (("corpus_id", "relative_path"),),
    "chunks": (("document_id", "chunk_index"),),
    "embedding_models": (("name", "provider", "dimension", "distance", "config_json"),),
    "vectors": (("model_id", "object_type", "object_id"),),
    "cluster_label_overrides": (("cluster_signature",),),
    "zotero_items": (("source_id", "zotero_key"),),
    "zotero_collections": (("source_id", "zotero_key"),),
    "zotero_attachments": (("source_id", "zotero_key"),),
}

_V7_REQUIRED_UNIQUE_KEYS = {
    **_V6_REQUIRED_UNIQUE_KEYS,
    "schema_migrations": (("name",),),
}

_V8_REQUIRED_UNIQUE_KEYS = _V7_REQUIRED_UNIQUE_KEYS

_CURRENT_REQUIRED_UNIQUE_KEYS = {
    **_V8_REQUIRED_UNIQUE_KEYS,
    "registered_sources": (("kind", "profile_signature"),),
    "jobs": (("queue_sequence",),),
}

_REQUIRED_FOREIGN_KEYS: dict[
    str,
    frozenset[tuple[tuple[str, ...], str, tuple[str, ...], str]],
] = {
    "scan_runs": frozenset({(("corpus_id",), "corpora", ("id",), "NO ACTION")}),
    "documents": frozenset({(("corpus_id",), "corpora", ("id",), "NO ACTION")}),
    "document_texts": frozenset({(("document_id",), "documents", ("id",), "CASCADE")}),
    "chunks": frozenset({(("document_id",), "documents", ("id",), "CASCADE")}),
    "skipped_files": frozenset(
        {
            (("scan_run_id",), "scan_runs", ("id",), "NO ACTION"),
            (("corpus_id",), "corpora", ("id",), "NO ACTION"),
        }
    ),
    "extraction_reports": frozenset(
        {
            (("scan_run_id",), "scan_runs", ("id",), "NO ACTION"),
            (("document_id",), "documents", ("id",), "NO ACTION"),
            (("corpus_id",), "corpora", ("id",), "NO ACTION"),
        }
    ),
    "vectors": frozenset({(("model_id",), "embedding_models", ("id",), "NO ACTION")}),
    "embedding_runs": frozenset(
        {(("model_id",), "embedding_models", ("id",), "NO ACTION")}
    ),
    "vector_indexes": frozenset(
        {(("model_id",), "embedding_models", ("id",), "NO ACTION")}
    ),
    "map_runs": frozenset({(("model_id",), "embedding_models", ("id",), "NO ACTION")}),
    "map_run_points": frozenset(
        {
            (("map_run_id",), "map_runs", ("id",), "CASCADE"),
            (("document_id",), "documents", ("id",), "NO ACTION"),
        }
    ),
    "map_run_clusters": frozenset({(("map_run_id",), "map_runs", ("id",), "CASCADE")}),
    "zotero_import_runs": frozenset(
        {(("source_id",), "zotero_sources", ("id",), "NO ACTION")}
    ),
    "zotero_items": frozenset(
        {(("source_id",), "zotero_sources", ("id",), "NO ACTION")}
    ),
    "zotero_creators": frozenset(
        {(("zotero_item_id",), "zotero_items", ("id",), "CASCADE")}
    ),
    "zotero_collections": frozenset(
        {(("source_id",), "zotero_sources", ("id",), "NO ACTION")}
    ),
    "zotero_item_collections": frozenset(
        {
            (("zotero_item_id",), "zotero_items", ("id",), "CASCADE"),
            (("collection_id",), "zotero_collections", ("id",), "CASCADE"),
        }
    ),
    "zotero_item_tags": frozenset(
        {(("zotero_item_id",), "zotero_items", ("id",), "CASCADE")}
    ),
    "zotero_attachments": frozenset(
        {
            (("source_id",), "zotero_sources", ("id",), "NO ACTION"),
            (
                ("parent_zotero_item_id",),
                "zotero_items",
                ("id",),
                "CASCADE",
            ),
        }
    ),
    "zotero_document_links": frozenset(
        {
            (("document_id",), "documents", ("id",), "CASCADE"),
            (("zotero_item_id",), "zotero_items", ("id",), "CASCADE"),
            (("attachment_id",), "zotero_attachments", ("id",), "SET NULL"),
        }
    ),
    "registered_sources": frozenset(
        {(("zotero_source_id",), "zotero_sources", ("id",), "RESTRICT")}
    ),
    "jobs": frozenset({(("source_id",), "registered_sources", ("id",), "RESTRICT")}),
}

_FTS_COLUMNS = ("document_id", "title", "relative_path", "text")
_FTS_DECLARATION = re.compile(
    r"\bcreate\s+virtual\s+table\b.*\busing\s+fts5\s*\(",
    re.IGNORECASE | re.DOTALL,
)


class MigrationFunction(Protocol):
    """Callable contract for a migration implementation."""

    def __call__(self, connection: sqlite3.Connection) -> None: ...


@dataclass(frozen=True)
class Migration:
    """One forward-only schema transition."""

    version: int
    name: str
    up: MigrationFunction


def _migrate_v7(connection: sqlite3.Connection) -> None:
    """Record migration history and make interrupted runs explainable."""

    connection.execute(
        """
        CREATE TABLE schema_migrations (
          version INTEGER PRIMARY KEY,
          name TEXT NOT NULL UNIQUE,
          applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute("ALTER TABLE scan_runs ADD COLUMN error_code TEXT")
    connection.execute("ALTER TABLE scan_runs ADD COLUMN error_message TEXT")
    connection.execute("ALTER TABLE embedding_runs ADD COLUMN error_code TEXT")
    connection.execute("ALTER TABLE embedding_runs ADD COLUMN error_message TEXT")
    connection.execute("ALTER TABLE zotero_import_runs ADD COLUMN error_code TEXT")
    connection.execute("ALTER TABLE zotero_import_runs ADD COLUMN error_message TEXT")
    connection.execute(
        """
        ALTER TABLE zotero_items
        ADD COLUMN child_manifest_json TEXT
        """
    )


def _migrate_v8(connection: sqlite3.Connection) -> None:
    """Record content provenance needed to reject stale vectors safely."""

    connection.execute("ALTER TABLE scan_runs ADD COLUMN owner_pid INTEGER")
    connection.execute(
        """
        ALTER TABLE documents
        ADD COLUMN content_revision_sha256 TEXT NOT NULL DEFAULT 'legacy-unknown'
        """
    )
    last_document_rowid = 0
    while True:
        document_rows = connection.execute(
            """
            SELECT d.rowid, d.id, d.title, d.relative_path, dt.text
            FROM documents d
            JOIN document_texts dt ON dt.document_id = d.id
            WHERE d.rowid > ?
            ORDER BY d.rowid
            LIMIT 500
            """,
            (last_document_rowid,),
        ).fetchall()
        if not document_rows:
            break
        connection.executemany(
            "UPDATE documents SET content_revision_sha256 = ? WHERE id = ?",
            (
                (
                    document_content_revision_sha256(
                        title=str(row[2]),
                        relative_path=str(row[3]),
                        text=str(row[4]),
                    ),
                    str(row[1]),
                )
                for row in document_rows
            ),
        )
        last_document_rowid = int(document_rows[-1][0])
    connection.execute(
        """
        ALTER TABLE chunks
        ADD COLUMN text_sha256 TEXT NOT NULL DEFAULT 'legacy-unknown'
        """
    )
    last_chunk_rowid = 0
    while True:
        chunk_rows = connection.execute(
            """
            SELECT rowid, id, text
            FROM chunks
            WHERE rowid > ?
            ORDER BY rowid
            LIMIT 500
            """,
            (last_chunk_rowid,),
        ).fetchall()
        if not chunk_rows:
            break
        connection.executemany(
            "UPDATE chunks SET text_sha256 = ? WHERE id = ?",
            (
                (
                    hashlib.sha256(str(row[2]).encode("utf-8")).hexdigest(),
                    str(row[1]),
                )
                for row in chunk_rows
            ),
        )
        last_chunk_rowid = int(chunk_rows[-1][0])
    connection.execute(
        """
        ALTER TABLE embedding_models
        ADD COLUMN model_fingerprint TEXT NOT NULL DEFAULT 'legacy-unknown'
        """
    )
    connection.execute(
        """
        ALTER TABLE embedding_models
        ADD COLUMN fingerprint_algorithm TEXT NOT NULL DEFAULT 'legacy-unknown'
        """
    )
    connection.execute(
        """
        ALTER TABLE vectors
        ADD COLUMN source_content_sha256 TEXT NOT NULL DEFAULT 'legacy-unknown'
        """
    )
    connection.execute(
        """
        ALTER TABLE vectors
        ADD COLUMN model_fingerprint TEXT NOT NULL DEFAULT 'legacy-unknown'
        """
    )
    connection.execute(
        """
        ALTER TABLE vectors
        ADD COLUMN algorithm_version TEXT NOT NULL DEFAULT 'legacy-unknown'
        """
    )
    connection.execute(
        """
        ALTER TABLE embedding_runs
        ADD COLUMN sources_changed INTEGER NOT NULL DEFAULT 0
        """
    )
    connection.execute("ALTER TABLE embedding_runs ADD COLUMN owner_pid INTEGER")
    connection.execute(
        """
        ALTER TABLE vector_indexes
        ADD COLUMN model_fingerprint TEXT NOT NULL DEFAULT 'legacy-unknown'
        """
    )
    connection.execute(
        """
        ALTER TABLE vector_indexes
        ADD COLUMN algorithm_version TEXT NOT NULL DEFAULT 'legacy-unknown'
        """
    )
    connection.execute(
        """
        ALTER TABLE vector_indexes
        ADD COLUMN vector_set_sha256 TEXT NOT NULL DEFAULT 'legacy-unknown'
        """
    )
    # No historical vector index has a trustworthy model/vector-set signature.
    # Deleting metadata is safe; build-owned files remain untouched for a future
    # explicit maintenance command rather than being recursively removed here.
    connection.execute("DELETE FROM vector_indexes")
    connection.execute("ALTER TABLE zotero_import_runs ADD COLUMN owner_pid INTEGER")
    connection.execute("CREATE INDEX idx_chunks_text_sha256 ON chunks(text_sha256)")


def _migrate_v9(connection: sqlite3.Connection) -> None:
    """Add registered local sources and the durable single-writer job queue."""

    connection.execute(
        """
        CREATE TABLE registered_sources (
          id TEXT PRIMARY KEY,
          kind TEXT NOT NULL CHECK(kind IN ('corpus_directory', 'zotero_profile')),
          display_name TEXT NOT NULL,
          root_path TEXT,
          zotero_source_id TEXT,
          profile_signature TEXT NOT NULL,
          config_json TEXT NOT NULL DEFAULT '{}',
          last_success_at TEXT,
          last_error_code TEXT,
          last_error_message TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          removed_at TEXT,
          CHECK(
            (kind = 'corpus_directory' AND root_path IS NOT NULL
              AND zotero_source_id IS NULL)
            OR
            (kind = 'zotero_profile' AND root_path IS NULL
              AND zotero_source_id IS NOT NULL)
          ),
          UNIQUE(kind, profile_signature),
          FOREIGN KEY(zotero_source_id)
            REFERENCES zotero_sources(id) ON DELETE RESTRICT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE jobs (
          id TEXT PRIMARY KEY,
          queue_sequence INTEGER NOT NULL UNIQUE CHECK(queue_sequence > 0),
          kind TEXT NOT NULL CHECK(kind IN (
            'index_corpus', 'zotero_sync', 'rebuild_analysis', 'backup_project'
          )),
          source_id TEXT,
          request_key TEXT NOT NULL,
          status TEXT NOT NULL CHECK(status IN (
            'queued', 'running', 'cancelling', 'completed', 'failed',
            'interrupted', 'cancelled'
          )),
          params_json TEXT NOT NULL DEFAULT '{}',
          progress_current INTEGER NOT NULL DEFAULT 0 CHECK(progress_current >= 0),
          progress_total INTEGER CHECK(progress_total IS NULL OR progress_total >= 0),
          message TEXT NOT NULL DEFAULT '',
          result_summary_json TEXT NOT NULL DEFAULT '{}',
          error_code TEXT,
          error_message TEXT,
          cancel_requested INTEGER NOT NULL DEFAULT 0
            CHECK(cancel_requested IN (0, 1)),
          owner_pid INTEGER,
          owner_instance_id TEXT,
          heartbeat_at TEXT,
          writer_slot INTEGER NOT NULL DEFAULT 1 CHECK(writer_slot = 1),
          revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
          created_at TEXT NOT NULL,
          started_at TEXT,
          finished_at TEXT,
          updated_at TEXT NOT NULL,
          CHECK(progress_total IS NULL OR progress_current <= progress_total),
          CHECK(status != 'cancelling' OR cancel_requested = 1),
          CHECK(
            (status = 'queued' AND started_at IS NULL AND finished_at IS NULL
              AND owner_pid IS NULL AND owner_instance_id IS NULL)
            OR
            (status IN ('running', 'cancelling') AND started_at IS NOT NULL
              AND finished_at IS NULL AND owner_pid IS NOT NULL
              AND owner_instance_id IS NOT NULL)
            OR
            (status IN ('completed', 'failed', 'interrupted', 'cancelled')
              AND finished_at IS NOT NULL)
          ),
          FOREIGN KEY(source_id)
            REFERENCES registered_sources(id) ON DELETE RESTRICT
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX idx_registered_sources_active
        ON registered_sources(kind, removed_at, display_name, id)
        """
    )
    connection.execute(
        """
        CREATE INDEX idx_registered_sources_zotero
        ON registered_sources(zotero_source_id)
        """
    )
    connection.execute(
        "CREATE INDEX idx_jobs_status_created_at ON jobs(status, queue_sequence)"
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX idx_jobs_active_dedupe
        ON jobs(request_key)
        WHERE status IN ('queued', 'running', 'cancelling')
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX idx_jobs_single_writer
        ON jobs(writer_slot)
        WHERE status IN ('running', 'cancelling')
        """
    )
    connection.execute(
        "CREATE INDEX idx_jobs_source_created_at ON jobs(source_id, created_at)"
    )
    _backfill_registered_sources(connection)


def _backfill_registered_sources(connection: sqlite3.Connection) -> None:
    corpus_rows = connection.execute(
        """
        SELECT id, root_path, created_at, updated_at
        FROM corpora
        WHERE root_path NOT LIKE 'zotero://sources/%'
        ORDER BY created_at, id
        """
    ).fetchall()
    for row in corpus_rows:
        root_path = os.path.normpath(str(row[1]))
        config = {"legacy_corpus_id": str(row[0])}
        source_id, signature = registered_source_identity(
            kind="corpus_directory",
            locator=os.path.normcase(root_path),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO registered_sources(
              id, kind, display_name, root_path, zotero_source_id,
              profile_signature, config_json, created_at, updated_at
            )
            VALUES (?, 'corpus_directory', ?, ?, NULL, ?, ?, ?, ?)
            """,
            (
                source_id,
                Path(root_path).name or "Corpus",
                root_path,
                signature,
                json.dumps(config, sort_keys=True, separators=(",", ":")),
                str(row[2]),
                str(row[3]),
            ),
        )

    zotero_rows = connection.execute(
        """
        SELECT id, local_api_url, data_dir, library_id, library_type, name,
               created_at, updated_at
        FROM zotero_sources
        ORDER BY created_at, id
        """
    ).fetchall()
    for row in zotero_rows:
        zotero_source_id = str(row[0])
        zotero_config: dict[str, object] = {
            "local_api_url": _canonical_legacy_zotero_api_url(row[1]),
            "data_dir": _canonical_legacy_zotero_data_dir(row[2]),
            "library_id": row[3],
            "library_type": row[4],
            "filters": {},
        }
        source_id, signature = registered_source_identity(
            kind="zotero_profile",
            locator=zotero_source_id,
            config=zotero_config,
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO registered_sources(
              id, kind, display_name, root_path, zotero_source_id,
              profile_signature, config_json, created_at, updated_at
            )
            VALUES (?, 'zotero_profile', ?, NULL, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                str(row[5]),
                zotero_source_id,
                signature,
                json.dumps(zotero_config, sort_keys=True, separators=(",", ":")),
                str(row[6]),
                str(row[7]),
            ),
        )


def _canonical_legacy_zotero_api_url(value: object) -> object:
    """Canonicalize valid historical local API URLs without blocking migration."""

    try:
        return _canonical_local_api_url_for_migration(value)
    except ValueError:
        # v8 allowed incomplete discovery rows. Preserve those rows for local
        # diagnosis; registration will continue to reject an unsafe endpoint.
        return value


def _canonical_local_api_url_for_migration(value: object) -> str:
    """Frozen copy of the runtime v9 loopback URL canonicalization contract."""

    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ValueError("Zotero local API URL is missing or invalid.")
    if any(
        character.isspace() or unicodedata.category(character).startswith("C")
        for character in value
    ):
        raise ValueError("Zotero local API URL is malformed.")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Zotero local API URL is malformed.") from exc
    if (
        parsed.scheme.lower() != "http"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.netloc.endswith(":")
    ):
        raise ValueError("Zotero local API URL is not a safe loopback URL.")
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname != "localhost":
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError as exc:
            raise ValueError("Zotero local API host must be loopback.") from exc
        if not address.is_loopback:
            raise ValueError("Zotero local API host must be loopback.")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Zotero local API port is invalid.")
    if "\\" in parsed.path:
        raise ValueError("Zotero local API path is invalid.")
    host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = f"{host}:{port}" if port is not None else host
    return urlunsplit(SplitResult("http", netloc, parsed.path or "", "", ""))


def _canonical_legacy_zotero_data_dir(value: object) -> object:
    """Apply the runtime Zotero data-directory normalization to valid values."""

    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or any(unicodedata.category(character).startswith("C") for character in value)
    ):
        return value
    path = Path(value).expanduser()
    if not path.is_absolute():
        return value
    try:
        return str(path.resolve(strict=False))
    except OSError:
        return value


MIGRATIONS: tuple[Migration, ...] | dict[int, Migration] = (
    Migration(7, "record_migrations_and_run_failures", _migrate_v7),
    Migration(8, "record_vector_source_provenance", _migrate_v8),
    Migration(9, "register_sources_and_durable_jobs", _migrate_v9),
)


def initialize_database(
    connection: sqlite3.Connection,
    *,
    backup_path: Path | None = None,
) -> None:
    """Bootstrap/migrate while translating unsafe SQLite failure modes."""

    try:
        _initialize_database(connection, backup_path=backup_path)
    except DatabaseError:
        raise
    except sqlite3.Error as exc:
        database_path = _database_path(connection)
        message = str(exc).lower()
        if "locked" in message or "busy" in message:
            raise DatabaseLockedError(
                database_path,
                detail_message=f"Database at {database_path} is locked: {exc}",
            ) from exc
        if (
            "not a database" in message
            or "malformed" in message
            or "corrupt" in message
        ):
            raise DatabaseCorruptError(
                database_path,
                detail_message=f"Database at {database_path} is unreadable: {exc}",
            ) from exc
        raise


def _initialize_database(
    connection: sqlite3.Connection,
    *,
    backup_path: Path | None = None,
) -> None:
    """Bootstrap or migrate a database and durably commit before returning.

    Operational read and write connections never call this function. This is the
    sole schema-changing entry point, which makes GET/read-only paths incapable of
    performing DDL by accident.
    """

    if connection.in_transaction:
        raise RuntimeError("Database initialization requires no active transaction.")

    database_path = _database_path(connection)
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing_tables = _user_tables(connection)
        if not existing_tables:
            _bootstrap_database(connection)
            validate_schema_capability(
                connection,
                version=CURRENT_SCHEMA_VERSION,
                database_path=database_path,
            )
            connection.commit()
            return

        found_version = _read_schema_version(connection, database_path)
        if found_version > CURRENT_SCHEMA_VERSION:
            raise FutureSchemaError(
                database_path,
                found_version=found_version,
                current_version=CURRENT_SCHEMA_VERSION,
            )
        if found_version == CURRENT_SCHEMA_VERSION:
            validate_schema_capability(
                connection,
                version=found_version,
                database_path=database_path,
            )
            connection.commit()
            return
        if found_version < OLDEST_SUPPORTED_SCHEMA_VERSION:
            raise UnsupportedSchemaError(
                database_path,
                detail_message=(
                    f"Database at {database_path} uses unsupported schema version "
                    f"{found_version}; the oldest supported version is "
                    f"{OLDEST_SUPPORTED_SCHEMA_VERSION}."
                ),
            )

        validate_schema_capability(
            connection,
            version=found_version,
            database_path=database_path,
        )

        selected = [
            migration
            for migration in _migration_entries()
            if found_version < int(migration.version) <= CURRENT_SCHEMA_VERSION
        ]
        expected_versions = list(range(found_version + 1, CURRENT_SCHEMA_VERSION + 1))
        if [int(migration.version) for migration in selected] != expected_versions:
            raise UnsupportedSchemaError(
                database_path,
                detail_message=(
                    f"No continuous migration path exists from schema version "
                    f"{found_version} to {CURRENT_SCHEMA_VERSION}."
                ),
            )

        snapshot_path = backup_path or _default_backup_path(
            database_path, found_version
        )
        if snapshot_path is not None:
            _backup_database(connection, snapshot_path)

        for migration in selected:
            migration.up(connection)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, name)
                VALUES (?, ?)
                """,
                (int(migration.version), str(migration.name)),
            )
            connection.execute(
                """
                UPDATE schema_meta
                SET value = ?
                WHERE key = 'schema_version'
                """,
                (str(migration.version),),
            )
        validate_schema_capability(
            connection,
            version=CURRENT_SCHEMA_VERSION,
            database_path=database_path,
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def read_schema_version(connection: sqlite3.Connection) -> int:
    """Return a validated schema version without changing the database."""

    return _read_schema_version(connection, _database_path(connection))


def validate_schema_capability(
    connection: sqlite3.Connection,
    *,
    version: int,
    database_path: Path | None = None,
) -> None:
    """Reject a version label that does not match a known schema identity.

    This deliberately checks only the structural contract needed to migrate or
    operate safely.  Row integrity, FTS synchronization, and stale-data checks
    remain the responsibility of the project validation service.
    """

    path = database_path or _database_path(connection)
    if version == OLDEST_SUPPORTED_SCHEMA_VERSION:
        required_columns = _V6_REQUIRED_COLUMNS
        required_primary_keys = _V6_REQUIRED_PRIMARY_KEYS
        required_unique_keys = _V6_REQUIRED_UNIQUE_KEYS
        required_indexes = _REQUIRED_INDEXES
        forbidden_columns = _V6_FORBIDDEN_COLUMNS
        forbidden_tables: frozenset[str] = frozenset()
    elif version == 7:
        required_columns = _V7_REQUIRED_COLUMNS
        required_primary_keys = _V7_REQUIRED_PRIMARY_KEYS
        required_unique_keys = _V7_REQUIRED_UNIQUE_KEYS
        required_indexes = _REQUIRED_INDEXES
        forbidden_columns = _V7_FORBIDDEN_COLUMNS
        forbidden_tables = frozenset()
    elif version == 8:
        required_columns = _V8_REQUIRED_COLUMNS
        required_primary_keys = _V8_REQUIRED_PRIMARY_KEYS
        required_unique_keys = _V8_REQUIRED_UNIQUE_KEYS
        required_indexes = _V8_REQUIRED_INDEXES
        forbidden_columns = {}
        forbidden_tables = _V8_FORBIDDEN_TABLES
    elif version == CURRENT_SCHEMA_VERSION:
        required_columns = _CURRENT_REQUIRED_COLUMNS
        required_primary_keys = _CURRENT_REQUIRED_PRIMARY_KEYS
        required_unique_keys = _CURRENT_REQUIRED_UNIQUE_KEYS
        required_indexes = _CURRENT_REQUIRED_INDEXES
        forbidden_columns = {}
        forbidden_tables = frozenset()
    else:
        raise UnsupportedSchemaError(
            path,
            detail_message=(
                f"Database at {path} declares schema version {version}, but this "
                "build has no capability signature for that version."
            ),
        )

    problems: list[str] = []
    table_rows = connection.execute(
        """
        SELECT name
        FROM sqlite_schema
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        """
    ).fetchall()
    tables = {str(row[0]) for row in table_rows}
    for table_name in sorted(forbidden_tables):
        if table_name in tables:
            problems.append(
                f"v{version} unexpectedly contains future table {table_name}"
            )
    for table_name, required in sorted(required_columns.items()):
        if table_name not in tables:
            problems.append(f"missing table {table_name}")
            continue
        actual = _table_columns(connection, table_name)
        missing = sorted(required - set(actual))
        if missing:
            problems.append(
                f"table {table_name} is missing columns {', '.join(missing)}"
            )

    if version == OLDEST_SUPPORTED_SCHEMA_VERSION:
        if "schema_migrations" in tables:
            problems.append("v6 unexpectedly contains schema_migrations")
    for table_name, forbidden in sorted(forbidden_columns.items()):
        if table_name not in tables:
            continue
        present = sorted(forbidden & set(_table_columns(connection, table_name)))
        if present:
            problems.append(
                f"v{version} table {table_name} already contains future columns "
                f"{', '.join(present)}"
            )

    _check_table_constraints(
        connection,
        tables,
        required_primary_keys,
        required_unique_keys,
        problems,
    )
    _check_required_foreign_keys(connection, tables, problems)
    _check_required_indexes(connection, required_indexes, problems)
    if version == CURRENT_SCHEMA_VERSION:
        _check_required_index_sql(connection, problems)
    _check_fts_shape(connection, problems)
    if version >= 7 and "schema_migrations" in tables:
        _check_migration_history(connection, through_version=version, problems=problems)

    if problems:
        raise UnsupportedSchemaError(
            path,
            detail_message=(
                f"Database at {path} declares schema version {version}, but its "
                f"Paper Galaxy schema identity is incomplete: {'; '.join(problems)}."
            ),
        )


def _table_columns(connection: sqlite3.Connection, table_name: str) -> tuple[str, ...]:
    escaped = table_name.replace('"', '""')
    rows = connection.execute(f'PRAGMA table_info("{escaped}")').fetchall()
    return tuple(str(row[1]) for row in rows)


def _check_table_constraints(
    connection: sqlite3.Connection,
    tables: set[str],
    required_primary_keys: dict[str, tuple[str, ...]],
    required_unique_keys: dict[str, tuple[tuple[str, ...], ...]],
    problems: list[str],
) -> None:
    for table_name, expected_primary_key in sorted(required_primary_keys.items()):
        if table_name not in tables:
            continue
        escaped = table_name.replace('"', '""')
        rows = connection.execute(f'PRAGMA table_info("{escaped}")').fetchall()
        actual_primary_key = tuple(
            str(row[1])
            for row in sorted(
                (row for row in rows if int(row[5]) > 0),
                key=lambda row: int(row[5]),
            )
        )
        if actual_primary_key != expected_primary_key:
            problems.append(
                f"table {table_name} has primary key {actual_primary_key!r}, "
                f"expected {expected_primary_key!r}"
            )

    for table_name, expected_keys in sorted(required_unique_keys.items()):
        if table_name not in tables:
            continue
        actual_keys = _table_unique_keys(connection, table_name)
        for expected_key in expected_keys:
            if expected_key not in actual_keys:
                problems.append(
                    f"table {table_name} is missing unique key {expected_key!r}"
                )


def _table_unique_keys(
    connection: sqlite3.Connection,
    table_name: str,
) -> set[tuple[str, ...]]:
    escaped = table_name.replace('"', '""')
    rows = connection.execute(f'PRAGMA index_list("{escaped}")').fetchall()
    keys: set[tuple[str, ...]] = set()
    for row in rows:
        unique = bool(row[2])
        origin = str(row[3])
        partial = bool(row[4])
        if not unique or origin == "pk" or partial:
            continue
        index_name = str(row[1]).replace('"', '""')
        column_rows = connection.execute(
            f'PRAGMA index_info("{index_name}")'
        ).fetchall()
        keys.add(tuple(str(column_row[2]) for column_row in column_rows))
    return keys


def _check_required_foreign_keys(
    connection: sqlite3.Connection,
    tables: set[str],
    problems: list[str],
) -> None:
    for table_name, expected_keys in sorted(_REQUIRED_FOREIGN_KEYS.items()):
        if table_name not in tables:
            continue
        actual_keys = _table_foreign_keys(connection, table_name)
        for expected_key in sorted(expected_keys):
            if expected_key not in actual_keys:
                problems.append(
                    f"table {table_name} is missing foreign key {expected_key!r}"
                )


def _table_foreign_keys(
    connection: sqlite3.Connection,
    table_name: str,
) -> set[tuple[tuple[str, ...], str, tuple[str, ...], str]]:
    escaped = table_name.replace('"', '""')
    rows = connection.execute(f'PRAGMA foreign_key_list("{escaped}")').fetchall()
    groups: dict[int, list[tuple[object, ...]]] = {}
    for raw_row in rows:
        row = tuple(raw_row)
        groups.setdefault(int(str(row[0])), []).append(row)

    keys: set[tuple[tuple[str, ...], str, tuple[str, ...], str]] = set()
    for group in groups.values():
        ordered = sorted(group, key=lambda row: int(str(row[1])))
        first = ordered[0]
        keys.add(
            (
                tuple(str(row[3]) for row in ordered),
                str(first[2]),
                tuple(str(row[4]) for row in ordered),
                str(first[6]).upper(),
            )
        )
    return keys


def _check_required_indexes(
    connection: sqlite3.Connection,
    required_indexes: dict[str, tuple[str, tuple[str, ...]]],
    problems: list[str],
) -> None:
    rows = connection.execute(
        """
        SELECT name, tbl_name, sql
        FROM sqlite_schema
        WHERE type = 'index'
        """
    ).fetchall()
    indexes = {str(row[0]): (str(row[1]), row[2]) for row in rows}
    for index_name, (expected_table, expected_columns) in sorted(
        required_indexes.items()
    ):
        definition = indexes.get(index_name)
        if definition is None:
            problems.append(f"missing index {index_name}")
            continue
        actual_table, sql = definition
        if actual_table != expected_table or sql is None:
            problems.append(f"index {index_name} has the wrong table or kind")
            continue
        escaped = index_name.replace('"', '""')
        column_rows = connection.execute(f'PRAGMA index_info("{escaped}")').fetchall()
        actual_columns = tuple(str(row[2]) for row in column_rows)
        if actual_columns != expected_columns:
            problems.append(
                f"index {index_name} has columns {actual_columns!r}, expected "
                f"{expected_columns!r}"
            )


def _check_required_index_sql(
    connection: sqlite3.Connection,
    problems: list[str],
) -> None:
    for index_name, expected_sql in sorted(_CURRENT_REQUIRED_INDEX_SQL.items()):
        row = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE type = 'index' AND name = ?",
            (index_name,),
        ).fetchone()
        if row is None or row[0] is None:
            continue
        normalized = _normalize_index_sql(str(row[0]))
        if normalized != expected_sql:
            problems.append(
                f"index {index_name} has the wrong uniqueness or predicate semantics"
            )


def _normalize_index_sql(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.strip().lower()).rstrip(";")
    normalized = re.sub(r"\s*\(\s*", "(", normalized)
    normalized = re.sub(r"\s*,\s*", ",", normalized)
    return re.sub(r"\s*\)", ")", normalized)


def _check_fts_shape(
    connection: sqlite3.Connection,
    problems: list[str],
) -> None:
    row = connection.execute(
        """
        SELECT sql
        FROM sqlite_schema
        WHERE type = 'table' AND name = 'documents_fts'
        """
    ).fetchone()
    if row is None:
        problems.append("missing FTS5 virtual table documents_fts")
        return
    declaration = str(row[0] or "")
    if _FTS_DECLARATION.search(declaration) is None:
        problems.append("documents_fts is not an FTS5 virtual table")
        return
    actual_columns = _table_columns(connection, "documents_fts")
    if actual_columns != _FTS_COLUMNS:
        problems.append(
            f"documents_fts has columns {actual_columns!r}, expected {_FTS_COLUMNS!r}"
        )
        return
    try:
        connection.execute(
            """
            SELECT COUNT(*)
            FROM documents_fts
            WHERE documents_fts MATCH ?
            """,
            ("paper_galaxy_schema_capability_probe",),
        ).fetchone()
    except sqlite3.Error as exc:
        problems.append(f"documents_fts MATCH is unavailable ({type(exc).__name__})")


def _check_migration_history(
    connection: sqlite3.Connection,
    *,
    through_version: int,
    problems: list[str],
) -> None:
    expected = {
        int(migration.version): str(migration.name)
        for migration in _migration_entries()
        if int(migration.version) <= through_version
    }
    rows = connection.execute(
        "SELECT version, name FROM schema_migrations ORDER BY version"
    ).fetchall()
    actual = {int(row[0]): str(row[1]) for row in rows}
    if actual != expected:
        problems.append("schema_migrations does not match the migration registry")


def _bootstrap_database(connection: sqlite3.Connection) -> None:
    schema = (
        files("paper_galaxy.storage").joinpath("schema.sql").read_text(encoding="utf-8")
    )
    try:
        for statement in _sql_statements(schema):
            connection.execute(statement)
        connection.execute(
            """
            INSERT INTO schema_meta(key, value)
            VALUES ('schema_version', ?)
            """,
            (SCHEMA_VERSION,),
        )
        for migration in _migration_entries():
            connection.execute(
                """
                INSERT INTO schema_migrations(version, name)
                VALUES (?, ?)
                """,
                (int(migration.version), str(migration.name)),
            )
    except sqlite3.OperationalError as exc:
        if "fts5" in str(exc).lower():
            raise FTSUnavailableError(
                "SQLite FTS5 is not available in this Python build."
            ) from exc
        raise


def _sql_statements(schema: str) -> Iterable[str]:
    pending: list[str] = []
    for line in schema.splitlines(keepends=True):
        pending.append(line)
        candidate = "".join(pending).strip()
        if candidate and sqlite3.complete_statement(candidate):
            yield candidate
            pending.clear()
    remainder = "".join(pending).strip()
    if remainder:
        raise RuntimeError("Bundled SQLite schema contains an incomplete statement.")


def _migration_entries() -> list[Migration]:
    registry = MIGRATIONS
    entries = list(registry.values()) if isinstance(registry, dict) else list(registry)
    return sorted(entries, key=lambda migration: int(migration.version))


def _user_tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            """
            SELECT name
            FROM sqlite_schema
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            """
        ).fetchall()
    }


def _read_schema_version(connection: sqlite3.Connection, database_path: Path) -> int:
    try:
        row = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
    except sqlite3.DatabaseError as exc:
        if "no such table" not in str(exc).lower():
            raise
        raise UnsupportedSchemaError(
            database_path,
            detail_message=(
                f"Database at {database_path} does not contain a supported "
                "schema_meta table."
            ),
        ) from exc
    if row is None:
        raise UnsupportedSchemaError(
            database_path,
            detail_message=(
                f"Database at {database_path} has no declared schema version."
            ),
        )
    try:
        return int(row[0])
    except (TypeError, ValueError) as exc:
        raise UnsupportedSchemaError(
            database_path,
            detail_message=(
                f"Database at {database_path} has an invalid schema version."
            ),
        ) from exc


def _database_path(connection: sqlite3.Connection) -> Path:
    rows = connection.execute("PRAGMA database_list").fetchall()
    for row in rows:
        if str(row[1]) == "main" and str(row[2]):
            return Path(str(row[2])).resolve()
    return Path(":memory:")


def _default_backup_path(database_path: Path, version: int) -> Path | None:
    if database_path == Path(":memory:"):
        return None
    return database_path.with_name(
        f"{database_path.name}.schema-v{version}.{uuid4().hex}.backup.sqlite3"
    )


def _backup_database(connection: sqlite3.Connection, backup_path: Path) -> None:
    source = _database_path(connection)
    if source == Path(":memory:"):
        raise ValueError("In-memory databases do not support migration snapshots.")
    destination = Path(os.path.abspath(Path(backup_path).expanduser()))
    _validate_backup_destination(source, destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _validate_backup_destination(source, destination)
    staging = destination.with_name(f".{destination.name}.{uuid4().hex}.staging")
    try:
        descriptor = os.open(
            staging,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        os.close(descriptor)
        source_connection = sqlite3.connect(
            f"{source.as_uri()}?mode=ro",
            uri=True,
        )
        try:
            backup = sqlite3.connect(staging)
            try:
                source_connection.execute("PRAGMA query_only = ON")
                source_connection.backup(backup)
                if backup.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    raise RuntimeError(
                        "Pre-migration SQLite backup failed quick_check."
                    )
                if backup.execute("PRAGMA foreign_key_check").fetchall():
                    raise RuntimeError(
                        "Pre-migration SQLite backup failed foreign_key_check."
                    )
            finally:
                backup.close()
        finally:
            source_connection.close()
        os.chmod(staging, 0o600)
        _validate_backup_destination(source, destination)
        os.link(staging, destination)
    finally:
        staging.unlink(missing_ok=True)


def _validate_backup_destination(source: Path, destination: Path) -> None:
    protected = {
        Path(os.path.abspath(source)),
        Path(os.path.abspath(f"{source}-wal")),
        Path(os.path.abspath(f"{source}-shm")),
    }
    if destination in protected:
        raise ValueError("A migration backup cannot replace the live SQLite database.")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(
            f"Refusing to replace an existing migration backup: {destination}"
        )
    for component in (destination.parent, *destination.parent.parents):
        if component.is_symlink() and not _trusted_system_alias(component):
            raise ValueError(
                f"Migration backup path contains a symbolic link: {component}"
            )


def _trusted_system_alias(path: Path) -> bool:
    if path not in {Path("/tmp"), Path("/var")}:
        return False
    try:
        return path.lstat().st_uid == 0
    except OSError:
        return False
