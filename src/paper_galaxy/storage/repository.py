"""Explicit SQLite repository operations for Phase 2."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from paper_galaxy.embeddings.models import (
    EmbeddingModelRecord,
    SemanticResultSource,
    VectorRecord,
)
from paper_galaxy.embeddings.ranking import VectorCandidate
from paper_galaxy.records import (
    DatabaseStats,
    ExtractionReport,
    IndexedChunk,
    IndexedDocument,
    SearchResult,
)
from paper_galaxy.storage.json import (
    StoredJSONError,
    load_json_list,
    load_json_object,
)
from paper_galaxy.storage.provenance import document_content_revision_sha256


class Repository:
    """Small repository wrapper around a SQLite connection."""

    def __init__(self, connection: sqlite3.Connection, database_path: Path) -> None:
        self.connection = connection
        self.database_path = database_path

    def upsert_corpus(self, corpus_id: str, root_path: str, now: str) -> None:
        self.connection.execute(
            """
            INSERT INTO corpora(id, root_path, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              root_path = excluded.root_path,
              updated_at = excluded.updated_at
            """,
            (corpus_id, root_path, now, now),
        )

    def create_scan_run(
        self, scan_run_id: str, corpus_id: str, corpus_path: str, now: str
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO scan_runs(
              id, corpus_id, corpus_path, started_at, status, owner_pid
            )
            VALUES (?, ?, ?, ?, 'running', ?)
            """,
            (scan_run_id, corpus_id, corpus_path, now, os.getpid()),
        )

    def finish_scan_run(
        self,
        scan_run_id: str,
        *,
        finished_at: str,
        files_found: int,
        documents_inserted: int,
        documents_updated: int,
        documents_unchanged: int,
        documents_missing: int,
        skipped_files: int,
        chunks_written: int,
        status: str = "completed",
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self.connection.execute(
            """
            UPDATE scan_runs
            SET finished_at = ?,
                files_found = ?,
                documents_inserted = ?,
                documents_updated = ?,
                documents_unchanged = ?,
                documents_missing = ?,
                skipped_files = ?,
                chunks_written = ?,
                status = ?,
                error_code = ?,
                error_message = ?
            WHERE id = ?
            """,
            (
                finished_at,
                files_found,
                documents_inserted,
                documents_updated,
                documents_unchanged,
                documents_missing,
                skipped_files,
                chunks_written,
                status,
                error_code,
                error_message,
                scan_run_id,
            ),
        )

    def get_document_by_relative_path(
        self, corpus_id: str, relative_path: str
    ) -> IndexedDocument | None:
        row = self.connection.execute(
            """
            SELECT *
            FROM documents
            WHERE corpus_id = ? AND relative_path = ?
            """,
            (corpus_id, relative_path),
        ).fetchone()
        return _document_from_row(row) if row is not None else None

    def list_documents(
        self,
        *,
        statuses: set[str] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[IndexedDocument]:
        status_sql, status_params = _status_filter(statuses)
        rows = self.connection.execute(
            f"""
            SELECT *
            FROM documents
            {status_sql}
            ORDER BY relative_path, id
            LIMIT ? OFFSET ?
            """,
            (*status_params, max(0, limit), max(0, offset)),
        ).fetchall()
        return [_document_from_row(row) for row in rows]

    def list_documents_with_text(
        self,
        *,
        statuses: set[str] | None = None,
        limit: int = 1000,
    ) -> list[tuple[IndexedDocument, str]]:
        status_sql, status_params = _status_filter(statuses, table_alias="d")
        rows = self.connection.execute(
            f"""
            SELECT d.*, dt.text
            FROM documents d
            JOIN document_texts dt ON dt.document_id = d.id
            {status_sql}
            ORDER BY d.relative_path, d.id
            LIMIT ?
            """,
            (*status_params, max(0, limit)),
        ).fetchall()
        return [(_document_from_row(row), str(row["text"])) for row in rows]

    def get_document(self, document_id: str) -> IndexedDocument | None:
        row = self.connection.execute(
            """
            SELECT *
            FROM documents
            WHERE id = ?
            """,
            (document_id,),
        ).fetchone()
        return _document_from_row(row) if row is not None else None

    def get_document_text(self, document_id: str) -> str | None:
        row = self.connection.execute(
            """
            SELECT text
            FROM document_texts
            WHERE document_id = ?
            """,
            (document_id,),
        ).fetchone()
        return str(row["text"]) if row is not None else None

    def get_document_chunks(
        self, document_id: str, *, limit: int = 20, offset: int = 0
    ) -> list[IndexedChunk]:
        rows = self.connection.execute(
            """
            SELECT id, document_id, chunk_index, text, char_count, text_sha256
            FROM chunks
            WHERE document_id = ?
            ORDER BY chunk_index
            LIMIT ? OFFSET ?
            """,
            (document_id, max(0, limit), max(0, offset)),
        ).fetchall()
        return [
            IndexedChunk(
                id=str(row["id"]),
                document_id=str(row["document_id"]),
                chunk_index=int(row["chunk_index"]),
                text=str(row["text"]),
                char_count=int(row["char_count"]),
                text_sha256=str(row["text_sha256"]),
            )
            for row in rows
        ]

    def count_document_chunks(self, document_id: str) -> int:
        return _scalar_int(
            self.connection,
            "SELECT COUNT(*) FROM chunks WHERE document_id = ?",
            (document_id,),
        )

    def list_chunks_with_documents(
        self,
        *,
        statuses: set[str] | None = None,
        limit: int = 1000,
    ) -> list[tuple[IndexedDocument, IndexedChunk]]:
        status_sql, status_params = _status_filter(statuses, table_alias="d")
        rows = self.connection.execute(
            f"""
            SELECT
              d.id AS document_id,
              d.corpus_id AS document_corpus_id,
              d.path AS document_path,
              d.relative_path AS document_relative_path,
              d.file_type AS document_file_type,
              d.title AS document_title,
              d.sha256 AS document_sha256,
              d.content_revision_sha256 AS document_content_revision_sha256,
              d.size_bytes AS document_size_bytes,
              d.mtime_ns AS document_mtime_ns,
              d.char_count AS document_char_count,
              d.status AS document_status,
              d.first_seen_at AS document_first_seen_at,
              d.last_seen_at AS document_last_seen_at,
              d.updated_at AS document_updated_at,
              c.id AS chunk_id,
              c.document_id AS chunk_document_id,
              c.chunk_index AS chunk_index,
              c.text AS chunk_text,
              c.char_count AS chunk_char_count,
              c.text_sha256 AS chunk_text_sha256
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            {status_sql}
            ORDER BY d.relative_path, c.chunk_index, c.id
            LIMIT ?
            """,
            (*status_params, max(0, limit)),
        ).fetchall()
        return [
            (
                _document_from_prefix(row, "document_"),
                IndexedChunk(
                    id=str(row["chunk_id"]),
                    document_id=str(row["chunk_document_id"]),
                    chunk_index=int(row["chunk_index"]),
                    text=str(row["chunk_text"]),
                    char_count=int(row["chunk_char_count"]),
                    text_sha256=str(row["chunk_text_sha256"]),
                ),
            )
            for row in rows
        ]

    def get_document_by_id_or_relative_path(
        self, document_id_or_path: str
    ) -> IndexedDocument | None:
        exact = self.connection.execute(
            "SELECT * FROM documents WHERE id = ?",
            (document_id_or_path,),
        ).fetchone()
        if exact is not None:
            return _document_from_row(exact)
        rows = self.connection.execute(
            """
            SELECT *
            FROM documents
            WHERE relative_path = ?
            ORDER BY id
            LIMIT 2
            """,
            (document_id_or_path,),
        ).fetchall()
        if len(rows) > 1:
            raise ValueError(
                "Relative document path is ambiguous across corpora; use its "
                "stable document ID."
            )
        return _document_from_row(rows[0]) if rows else None

    def upsert_embedding_model(self, model: EmbeddingModelRecord) -> None:
        self.connection.execute(
            """
            INSERT INTO embedding_models(
              id, name, provider, dimension, distance, config_json,
              model_fingerprint, fingerprint_algorithm, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              name = excluded.name,
              provider = excluded.provider,
              dimension = excluded.dimension,
              distance = excluded.distance,
              config_json = excluded.config_json,
              model_fingerprint = excluded.model_fingerprint,
              fingerprint_algorithm = excluded.fingerprint_algorithm
            """,
            (
                model.id,
                model.name,
                model.provider,
                model.dimension,
                model.distance,
                json.dumps(model.config, sort_keys=True),
                model.model_fingerprint,
                model.fingerprint_algorithm,
                model.created_at,
            ),
        )

    def get_embedding_model(self, model_id: str) -> EmbeddingModelRecord | None:
        row = self.connection.execute(
            """
            SELECT *
            FROM embedding_models
            WHERE id = ?
            """,
            (model_id,),
        ).fetchone()
        return _embedding_model_from_row(row) if row is not None else None

    def create_embedding_run(
        self, run_id: str, model_id: str, *, started_at: str, config: dict[str, object]
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO embedding_runs(
              id, model_id, started_at, status, config_json, owner_pid
            )
            VALUES (?, ?, ?, 'running', ?, ?)
            """,
            (
                run_id,
                model_id,
                started_at,
                json.dumps(config, sort_keys=True),
                os.getpid(),
            ),
        )

    def finish_embedding_run(
        self,
        run_id: str,
        *,
        finished_at: str,
        status: str,
        documents_seen: int,
        documents_embedded: int,
        documents_unchanged: int,
        chunks_seen: int,
        chunks_embedded: int,
        chunks_unchanged: int,
        sources_changed: int = 0,
        errors: int = 0,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self.connection.execute(
            """
            UPDATE embedding_runs
            SET finished_at = ?,
                status = ?,
                documents_seen = ?,
                documents_embedded = ?,
                documents_unchanged = ?,
                chunks_seen = ?,
                chunks_embedded = ?,
                chunks_unchanged = ?,
                sources_changed = ?,
                errors = ?,
                error_code = ?,
                error_message = ?
            WHERE id = ?
            """,
            (
                finished_at,
                status,
                documents_seen,
                documents_embedded,
                documents_unchanged,
                chunks_seen,
                chunks_embedded,
                chunks_unchanged,
                sources_changed,
                errors,
                error_code,
                error_message,
                run_id,
            ),
        )

    def upsert_vector(self, vector: VectorRecord) -> None:
        self.connection.execute(
            """
            INSERT INTO vectors(
              id, model_id, object_type, object_id, text_sha256,
              source_content_sha256, model_fingerprint, algorithm_version,
              dimension, dtype, vector, metadata_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(model_id, object_type, object_id) DO UPDATE SET
              id = excluded.id,
              text_sha256 = excluded.text_sha256,
              source_content_sha256 = excluded.source_content_sha256,
              model_fingerprint = excluded.model_fingerprint,
              algorithm_version = excluded.algorithm_version,
              dimension = excluded.dimension,
              dtype = excluded.dtype,
              vector = excluded.vector,
              metadata_json = excluded.metadata_json,
              updated_at = excluded.updated_at
            """,
            (
                vector.id,
                vector.model_id,
                vector.object_type,
                vector.object_id,
                vector.text_sha256,
                vector.source_content_sha256,
                vector.model_fingerprint,
                vector.algorithm_version,
                vector.dimension,
                vector.dtype,
                vector.vector,
                json.dumps(vector.metadata, sort_keys=True),
                vector.created_at,
                vector.updated_at,
            ),
        )
        self.connection.execute(
            """
            DELETE FROM vector_indexes
            WHERE model_id = ? AND object_type = ?
            """,
            (vector.model_id, vector.object_type),
        )

    def current_vector_source_ids(
        self,
        sources: Mapping[tuple[str, str], str],
    ) -> set[tuple[str, str]]:
        """Batch-check active source revisions immediately before vector writes."""

        current: set[tuple[str, str]] = set()
        for object_type in ("document", "chunk"):
            expected = {
                object_id: source_hash
                for (candidate_type, object_id), source_hash in sources.items()
                if candidate_type == object_type
            }
            if not expected:
                continue
            placeholders = ", ".join("?" for _ in expected)
            if object_type == "document":
                rows = self.connection.execute(
                    f"""
                    SELECT id, content_revision_sha256 AS source_hash
                    FROM documents
                    WHERE status = 'active' AND id IN ({placeholders})
                    """,
                    tuple(expected),
                ).fetchall()
            else:
                rows = self.connection.execute(
                    f"""
                    SELECT c.id, c.text_sha256 AS source_hash
                    FROM chunks c
                    JOIN documents d ON d.id = c.document_id
                    WHERE d.status = 'active' AND c.id IN ({placeholders})
                    """,
                    tuple(expected),
                ).fetchall()
            current.update(
                (object_type, object_id)
                for row in rows
                if (
                    (object_id := str(row["id"])) in expected
                    and str(row["source_hash"]) == expected[object_id]
                )
            )
        return current

    def get_vector(
        self, model_id: str, object_type: str, object_id: str
    ) -> VectorRecord | None:
        row = self.connection.execute(
            """
            SELECT *
            FROM vectors
            WHERE model_id = ? AND object_type = ? AND object_id = ?
            """,
            (model_id, object_type, object_id),
        ).fetchone()
        return _vector_from_row(row) if row is not None else None

    def list_vectors(self, model_id: str, object_type: str) -> list[VectorRecord]:
        rows = self.connection.execute(
            """
            SELECT *
            FROM vectors
            WHERE model_id = ? AND object_type = ?
            ORDER BY object_id
            """,
            (model_id, object_type),
        ).fetchall()
        return [_vector_from_row(row) for row in rows]

    def iter_eligible_vector_candidates(
        self,
        *,
        model_id: str,
        model_name: str,
        model_provider: str,
        model_dimension: int,
        model_distance: str,
        model_config: Mapping[str, Any],
        object_type: str,
        model_fingerprint: str,
        fingerprint_algorithm: str,
        algorithm_version: str,
        include_missing: bool = False,
        fetch_size: int = 256,
    ) -> Iterable[VectorCandidate]:
        """Stream active vectors whose stored provenance matches their source."""

        if object_type == "document":
            status_clause = (
                "d.status IN ('active', 'missing')"
                if include_missing
                else "d.status = 'active'"
            )
            sql = f"""
                SELECT
                  v.id AS vector_id,
                  v.object_id,
                  d.relative_path,
                  NULL AS chunk_index,
                  v.dimension,
                  v.vector,
                  v.source_content_sha256,
                  v.text_sha256,
                  v.updated_at,
                  v.model_fingerprint,
                  v.algorithm_version,
                  v.dtype,
                  v.metadata_json
                FROM vectors v
                JOIN embedding_models m ON m.id = v.model_id
                JOIN documents d ON d.id = v.object_id
                WHERE v.model_id = ?
                  AND v.object_type = 'document'
                  AND m.name = ?
                  AND m.provider = ?
                  AND m.dimension = ?
                  AND m.distance = ?
                  AND m.config_json = ?
                  AND {status_clause}
                  AND v.source_content_sha256 = d.content_revision_sha256
                  AND length(v.source_content_sha256) = 64
                  AND v.source_content_sha256 NOT GLOB '*[^0-9a-f]*'
                  AND length(v.text_sha256) = 64
                  AND v.text_sha256 NOT GLOB '*[^0-9a-f]*'
                  AND v.model_fingerprint = ?
                  AND m.model_fingerprint = ?
                  AND m.fingerprint_algorithm = ?
                  AND v.algorithm_version = ?
                  AND typeof(v.dimension) = 'integer'
                  AND typeof(m.dimension) = 'integer'
                  AND v.dimension > 0
                  AND v.dimension = m.dimension
                  AND v.dtype = 'float32'
                  AND typeof(v.vector) = 'blob'
                  AND length(v.vector) = v.dimension * 4
                ORDER BY d.relative_path, v.object_id
            """
        elif object_type == "chunk":
            sql = """
                SELECT
                  v.id AS vector_id,
                  v.object_id,
                  d.relative_path,
                  c.chunk_index,
                  v.dimension,
                  v.vector,
                  v.source_content_sha256,
                  v.text_sha256,
                  v.updated_at,
                  v.model_fingerprint,
                  v.algorithm_version,
                  v.dtype,
                  v.metadata_json
                FROM vectors v
                JOIN embedding_models m ON m.id = v.model_id
                JOIN chunks c ON c.id = v.object_id
                JOIN documents d ON d.id = c.document_id
                WHERE v.model_id = ?
                  AND v.object_type = 'chunk'
                  AND m.name = ?
                  AND m.provider = ?
                  AND m.dimension = ?
                  AND m.distance = ?
                  AND m.config_json = ?
                  AND d.status = 'active'
                  AND v.source_content_sha256 = c.text_sha256
                  AND length(v.source_content_sha256) = 64
                  AND v.source_content_sha256 NOT GLOB '*[^0-9a-f]*'
                  AND length(v.text_sha256) = 64
                  AND v.text_sha256 NOT GLOB '*[^0-9a-f]*'
                  AND v.model_fingerprint = ?
                  AND m.model_fingerprint = ?
                  AND m.fingerprint_algorithm = ?
                  AND v.algorithm_version = ?
                  AND typeof(v.dimension) = 'integer'
                  AND typeof(m.dimension) = 'integer'
                  AND v.dimension > 0
                  AND v.dimension = m.dimension
                  AND v.dtype = 'float32'
                  AND typeof(v.vector) = 'blob'
                  AND length(v.vector) = v.dimension * 4
                ORDER BY d.relative_path, c.chunk_index, v.object_id
            """
        else:
            return
        cursor = self.connection.execute(
            sql,
            (
                model_id,
                model_name,
                model_provider,
                model_dimension,
                model_distance,
                json.dumps(dict(model_config), sort_keys=True),
                model_fingerprint,
                model_fingerprint,
                fingerprint_algorithm,
                algorithm_version,
            ),
        )
        while rows := cursor.fetchmany(max(1, fetch_size)):
            for row in rows:
                try:
                    metadata = load_json_object(row["metadata_json"])
                except StoredJSONError:
                    continue
                yield VectorCandidate(
                    object_id=str(row["object_id"]),
                    relative_path=str(row["relative_path"]),
                    chunk_index=(
                        int(row["chunk_index"])
                        if row["chunk_index"] is not None
                        else None
                    ),
                    dimension=int(row["dimension"]),
                    blob=bytes(row["vector"]),
                    source_content_sha256=str(row["source_content_sha256"]),
                    vector_id=str(row["vector_id"]),
                    text_sha256=str(row["text_sha256"]),
                    updated_at=str(row["updated_at"]),
                    model_fingerprint=str(row["model_fingerprint"]),
                    algorithm_version=str(row["algorithm_version"]),
                    dtype=str(row["dtype"]),
                    metadata=metadata,
                )

    def get_semantic_result_sources(
        self,
        *,
        model_id: str,
        model_name: str,
        model_provider: str,
        model_dimension: int,
        model_distance: str,
        model_config: Mapping[str, Any],
        model_fingerprint: str,
        fingerprint_algorithm: str,
        object_type: str,
        object_ids: Iterable[str],
        include_missing: bool = False,
    ) -> dict[str, SemanticResultSource]:
        """Load display metadata and bounded-source text in one query."""

        ids = tuple(dict.fromkeys(str(object_id) for object_id in object_ids))
        if not ids:
            return {}
        placeholders = ", ".join("?" for _ in ids)
        if object_type == "document":
            status_clause = (
                "d.status IN ('active', 'missing')"
                if include_missing
                else "d.status = 'active'"
            )
            sql = f"""
                SELECT
                  d.id AS document_id,
                  d.corpus_id AS document_corpus_id,
                  d.path AS document_path,
                  d.relative_path AS document_relative_path,
                  d.file_type AS document_file_type,
                  d.title AS document_title,
                  d.sha256 AS document_sha256,
                  d.content_revision_sha256 AS document_content_revision_sha256,
                  d.size_bytes AS document_size_bytes,
                  d.mtime_ns AS document_mtime_ns,
                  d.char_count AS document_char_count,
                  d.status AS document_status,
                  d.first_seen_at AS document_first_seen_at,
                  d.last_seen_at AS document_last_seen_at,
                  d.updated_at AS document_updated_at,
                  dt.text AS source_text,
                  NULL AS chunk_index,
                  d.content_revision_sha256 AS current_source_sha256,
                  cv.id AS current_vector_id,
                  cv.text_sha256 AS current_vector_text_sha256,
                  cv.updated_at AS current_vector_updated_at,
                  cv.vector AS current_vector_blob,
                  cv.dimension AS current_vector_dimension,
                  cv.dtype AS current_vector_dtype,
                  cv.model_fingerprint AS current_vector_model_fingerprint,
                  cv.algorithm_version AS current_vector_algorithm_version
                FROM documents d
                JOIN document_texts dt ON dt.document_id = d.id
                JOIN vectors cv
                  ON cv.model_id = ?
                 AND cv.object_type = 'document'
                 AND cv.object_id = d.id
                 AND cv.source_content_sha256 = d.content_revision_sha256
                 AND typeof(cv.vector) = 'blob'
                 AND typeof(cv.dimension) = 'integer'
                JOIN embedding_models cm
                  ON cm.id = cv.model_id
                 AND cm.name = ?
                 AND cm.provider = ?
                 AND cm.dimension = ?
                 AND cm.distance = ?
                 AND cm.config_json = ?
                 AND cm.model_fingerprint = ?
                 AND cm.fingerprint_algorithm = ?
                WHERE d.id IN ({placeholders}) AND {status_clause}
            """
        elif object_type == "chunk":
            sql = f"""
                SELECT
                  c.id AS object_id,
                  d.id AS document_id,
                  d.corpus_id AS document_corpus_id,
                  d.path AS document_path,
                  d.relative_path AS document_relative_path,
                  d.file_type AS document_file_type,
                  d.title AS document_title,
                  d.sha256 AS document_sha256,
                  d.content_revision_sha256 AS document_content_revision_sha256,
                  d.size_bytes AS document_size_bytes,
                  d.mtime_ns AS document_mtime_ns,
                  d.char_count AS document_char_count,
                  d.status AS document_status,
                  d.first_seen_at AS document_first_seen_at,
                  d.last_seen_at AS document_last_seen_at,
                  d.updated_at AS document_updated_at,
                  c.text AS source_text,
                  c.chunk_index AS chunk_index,
                  c.text_sha256 AS current_source_sha256,
                  cv.id AS current_vector_id,
                  cv.text_sha256 AS current_vector_text_sha256,
                  cv.updated_at AS current_vector_updated_at,
                  cv.vector AS current_vector_blob,
                  cv.dimension AS current_vector_dimension,
                  cv.dtype AS current_vector_dtype,
                  cv.model_fingerprint AS current_vector_model_fingerprint,
                  cv.algorithm_version AS current_vector_algorithm_version
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                JOIN vectors cv
                  ON cv.model_id = ?
                 AND cv.object_type = 'chunk'
                 AND cv.object_id = c.id
                 AND cv.source_content_sha256 = c.text_sha256
                 AND typeof(cv.vector) = 'blob'
                 AND typeof(cv.dimension) = 'integer'
                JOIN embedding_models cm
                  ON cm.id = cv.model_id
                 AND cm.name = ?
                 AND cm.provider = ?
                 AND cm.dimension = ?
                 AND cm.distance = ?
                 AND cm.config_json = ?
                 AND cm.model_fingerprint = ?
                 AND cm.fingerprint_algorithm = ?
                WHERE c.id IN ({placeholders}) AND d.status = 'active'
            """
        else:
            return {}
        rows = self.connection.execute(
            sql,
            (
                model_id,
                model_name,
                model_provider,
                model_dimension,
                model_distance,
                json.dumps(dict(model_config), sort_keys=True),
                model_fingerprint,
                fingerprint_algorithm,
                *ids,
            ),
        ).fetchall()
        result: dict[str, SemanticResultSource] = {}
        for row in rows:
            document = _document_from_prefix(row, "document_")
            object_id = (
                document.id if object_type == "document" else str(row["object_id"])
            )
            result[object_id] = SemanticResultSource(
                document=document,
                text=str(row["source_text"]),
                chunk_index=(
                    int(row["chunk_index"]) if row["chunk_index"] is not None else None
                ),
                source_content_sha256=str(row["current_source_sha256"]),
                vector_id=str(row["current_vector_id"]),
                vector_text_sha256=str(row["current_vector_text_sha256"]),
                vector_updated_at=str(row["current_vector_updated_at"]),
                vector_blob=bytes(row["current_vector_blob"]),
                vector_dimension=int(row["current_vector_dimension"]),
                vector_dtype=str(row["current_vector_dtype"]),
                vector_model_fingerprint=str(row["current_vector_model_fingerprint"]),
                vector_algorithm_version=str(row["current_vector_algorithm_version"]),
            )
        return result

    def vector_stats(self) -> dict[str, object]:
        model_rows = self.connection.execute(
            """
            SELECT *
            FROM embedding_models
            ORDER BY created_at, name
            """
        ).fetchall()
        count_rows = self.connection.execute(
            """
            SELECT
              v.model_id,
              m.name AS model_name,
              m.provider,
              m.dimension,
              v.object_type,
              COUNT(*) AS vector_count,
              MIN(v.created_at) AS first_vector_at,
              MAX(v.updated_at) AS last_vector_at
            FROM vectors v
            JOIN embedding_models m ON m.id = v.model_id
            GROUP BY v.model_id, v.object_type
            ORDER BY m.name, v.object_type
            """
        ).fetchall()
        last_run = self.connection.execute(
            """
            SELECT r.*, m.name AS model_name
            FROM embedding_runs r
            LEFT JOIN embedding_models m ON m.id = r.model_id
            ORDER BY r.started_at DESC
            LIMIT 1
            """
        ).fetchone()
        index_rows = self.connection.execute(
            """
            SELECT *
            FROM vector_indexes
            ORDER BY created_at, index_path
            """
        ).fetchall()
        return {
            "database_path": str(self.database_path),
            "models": [
                {
                    "id": str(row["id"]),
                    "name": str(row["name"]),
                    "provider": str(row["provider"]),
                    "dimension": int(row["dimension"]),
                    "distance": str(row["distance"]),
                    "model_fingerprint": str(row["model_fingerprint"]),
                    "fingerprint_algorithm": str(row["fingerprint_algorithm"]),
                    "config": load_json_object(row["config_json"]),
                    "created_at": str(row["created_at"]),
                }
                for row in model_rows
            ],
            "vector_counts": [
                {
                    "model_id": str(row["model_id"]),
                    "model_name": str(row["model_name"]),
                    "provider": str(row["provider"]),
                    "dimension": int(row["dimension"]),
                    "object_type": str(row["object_type"]),
                    "vector_count": int(row["vector_count"]),
                    "first_vector_at": str(row["first_vector_at"]),
                    "last_vector_at": str(row["last_vector_at"]),
                }
                for row in count_rows
            ],
            "last_run": _embedding_run_payload(last_run),
            "vector_indexes": [
                {
                    "id": str(row["id"]),
                    "model_id": str(row["model_id"]),
                    "object_type": str(row["object_type"]),
                    "index_path": str(row["index_path"]),
                    "vector_count": int(row["vector_count"]),
                    "model_fingerprint": str(row["model_fingerprint"]),
                    "algorithm_version": str(row["algorithm_version"]),
                    "vector_set_sha256": str(row["vector_set_sha256"]),
                    "created_at": str(row["created_at"]),
                    "metadata": load_json_object(row["metadata_json"]),
                }
                for row in index_rows
            ],
        }

    def get_cluster_label_overrides(
        self, cluster_signatures: Iterable[str]
    ) -> dict[str, str]:
        """Return manual labels keyed by deterministic cluster signature."""

        signatures = tuple(sorted({signature for signature in cluster_signatures}))
        if not signatures:
            return {}
        placeholders = ", ".join("?" for _ in signatures)
        rows = self.connection.execute(
            f"""
            SELECT cluster_signature, label
            FROM cluster_label_overrides
            WHERE cluster_signature IN ({placeholders})
            """,
            signatures,
        ).fetchall()
        return {str(row["cluster_signature"]): str(row["label"]) for row in rows}

    def list_cluster_label_overrides(self) -> list[dict[str, object]]:
        """List locally stored cluster label overrides."""

        rows = self.connection.execute(
            """
            SELECT *
            FROM cluster_label_overrides
            ORDER BY updated_at DESC, cluster_signature
            """
        ).fetchall()
        return [_cluster_label_override_payload(row) for row in rows]

    def upsert_cluster_label_override(
        self,
        *,
        cluster_signature: str,
        label: str,
        source: str = "manual",
        metadata: Mapping[str, Any] | None = None,
        now: str | None = None,
    ) -> dict[str, object]:
        """Insert or update a local manual cluster label override."""

        timestamp = now or _utc_now()
        metadata_json = json.dumps(dict(metadata or {}), sort_keys=True)
        override_id = _cluster_label_override_id(cluster_signature)
        self.connection.execute(
            """
            INSERT INTO cluster_label_overrides(
              id, cluster_signature, label, source, metadata_json, created_at,
              updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cluster_signature) DO UPDATE SET
              label = excluded.label,
              source = excluded.source,
              metadata_json = excluded.metadata_json,
              updated_at = excluded.updated_at
            """,
            (
                override_id,
                cluster_signature,
                label,
                source,
                metadata_json,
                timestamp,
                timestamp,
            ),
        )
        row = self.connection.execute(
            """
            SELECT *
            FROM cluster_label_overrides
            WHERE cluster_signature = ?
            """,
            (cluster_signature,),
        ).fetchone()
        return _cluster_label_override_payload(row)

    def delete_cluster_label_override(self, cluster_signature: str) -> bool:
        """Delete a local cluster label override if it exists."""

        cursor = self.connection.execute(
            """
            DELETE FROM cluster_label_overrides
            WHERE cluster_signature = ?
            """,
            (cluster_signature,),
        )
        return cursor.rowcount > 0

    def save_map_run(
        self,
        *,
        run_id: str,
        name: str,
        status: str,
        similarity_mode: str,
        model_id: str | None,
        seed: int,
        requested_clusters: int | None,
        requested_neighbors: int,
        requested_limit: int,
        document_count: int,
        cluster_count: int,
        document_set_signature: str,
        warnings: Iterable[str],
        metadata: Mapping[str, Any] | None,
        points: Iterable[Mapping[str, Any]],
        clusters: Iterable[Mapping[str, Any]],
        now: str | None = None,
    ) -> dict[str, object]:
        """Persist a deterministic map snapshot without storing document text."""

        timestamp = now or _utc_now()
        metadata_json = json.dumps(dict(metadata or {}), sort_keys=True)
        warnings_json = json.dumps([str(warning) for warning in warnings])
        self.connection.execute(
            """
            INSERT INTO map_runs(
              id, name, created_at, status, similarity_mode, model_id, seed,
              requested_clusters, requested_neighbors, requested_limit,
              document_count, cluster_count, document_set_signature,
              warnings_json, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              name = excluded.name,
              status = excluded.status,
              similarity_mode = excluded.similarity_mode,
              model_id = excluded.model_id,
              seed = excluded.seed,
              requested_clusters = excluded.requested_clusters,
              requested_neighbors = excluded.requested_neighbors,
              requested_limit = excluded.requested_limit,
              document_count = excluded.document_count,
              cluster_count = excluded.cluster_count,
              document_set_signature = excluded.document_set_signature,
              warnings_json = excluded.warnings_json,
              metadata_json = excluded.metadata_json
            """,
            (
                run_id,
                name,
                timestamp,
                status,
                similarity_mode,
                model_id,
                seed,
                requested_clusters,
                requested_neighbors,
                requested_limit,
                document_count,
                cluster_count,
                document_set_signature,
                warnings_json,
                metadata_json,
            ),
        )
        self.connection.execute(
            "DELETE FROM map_run_points WHERE map_run_id = ?", (run_id,)
        )
        self.connection.executemany(
            """
            INSERT INTO map_run_points(
              map_run_id, document_id, x, y, cluster_id, cluster_label,
              cluster_signature, top_terms_json, nearest_neighbors_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    str(point.get("document_id", "")),
                    float(point.get("x", 0.0)),
                    float(point.get("y", 0.0)),
                    int(point.get("cluster_id", 0)),
                    str(point.get("cluster_label", "")),
                    str(point.get("cluster_signature", "")),
                    json.dumps(_json_list(point.get("top_terms")), sort_keys=True),
                    json.dumps(
                        _json_list(point.get("nearest_neighbors")), sort_keys=True
                    ),
                )
                for point in points
            ],
        )
        self.connection.execute(
            "DELETE FROM map_run_clusters WHERE map_run_id = ?", (run_id,)
        )
        self.connection.executemany(
            """
            INSERT INTO map_run_clusters(
              map_run_id, cluster_id, cluster_signature, display_label,
              generated_label, source, size, document_ids_json, top_terms_json,
              representatives_json, warnings_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    int(cluster.get("cluster_id", 0)),
                    str(cluster.get("cluster_signature", "")),
                    str(cluster.get("display_label", "")),
                    str(cluster.get("generated_label", "")),
                    str(cluster.get("source", "generated")),
                    int(cluster.get("size", 0)),
                    json.dumps(_json_list(cluster.get("document_ids")), sort_keys=True),
                    json.dumps(_json_list(cluster.get("top_terms")), sort_keys=True),
                    json.dumps(
                        _json_list(cluster.get("representatives")), sort_keys=True
                    ),
                    json.dumps(_json_list(cluster.get("warnings")), sort_keys=True),
                )
                for cluster in clusters
            ],
        )
        return self.get_map_run(run_id, include_payload=False) or {}

    def list_map_runs(self) -> list[dict[str, object]]:
        """List saved map runs newest first."""

        rows = self.connection.execute(
            """
            SELECT *
            FROM map_runs
            ORDER BY created_at DESC, name
            """
        ).fetchall()
        return [_map_run_payload(row) for row in rows]

    def get_map_run(
        self, run_id: str, *, include_payload: bool = True
    ) -> dict[str, object] | None:
        """Return a saved map run, optionally including points and clusters."""

        row = self.connection.execute(
            """
            SELECT *
            FROM map_runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        payload = _map_run_payload(row)
        if include_payload:
            payload["points"] = self.list_map_run_points(run_id)
            payload["clusters"] = self.list_map_run_clusters(run_id)
        return payload

    def list_map_run_points(self, run_id: str) -> list[dict[str, object]]:
        """Return saved map points for one run in stable document order."""

        rows = self.connection.execute(
            """
            SELECT *
            FROM map_run_points
            WHERE map_run_id = ?
            ORDER BY document_id
            """,
            (run_id,),
        ).fetchall()
        return [_map_run_point_payload(row) for row in rows]

    def list_map_run_clusters(self, run_id: str) -> list[dict[str, object]]:
        """Return saved clusters for one map run."""

        rows = self.connection.execute(
            """
            SELECT *
            FROM map_run_clusters
            WHERE map_run_id = ?
            ORDER BY cluster_id, cluster_signature
            """,
            (run_id,),
        ).fetchall()
        return [_map_run_cluster_payload(row) for row in rows]

    def delete_map_run(self, run_id: str) -> bool:
        """Delete a saved map run and its child rows."""

        cursor = self.connection.execute(
            """
            DELETE FROM map_runs
            WHERE id = ?
            """,
            (run_id,),
        )
        return cursor.rowcount > 0

    def upsert_zotero_source(self, source: Mapping[str, Any]) -> None:
        """Insert or update a local read-only Zotero source."""

        self.connection.execute(
            """
            INSERT INTO zotero_sources(
              id, source_type, local_api_url, data_dir, library_id, library_type,
              name, last_version, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              source_type = excluded.source_type,
              local_api_url = excluded.local_api_url,
              data_dir = excluded.data_dir,
              library_id = excluded.library_id,
              library_type = excluded.library_type,
              name = excluded.name,
              last_version = CASE
                WHEN zotero_sources.last_version IS NULL THEN excluded.last_version
                WHEN excluded.last_version IS NULL THEN zotero_sources.last_version
                WHEN excluded.last_version > zotero_sources.last_version
                  THEN excluded.last_version
                ELSE zotero_sources.last_version
              END,
              updated_at = excluded.updated_at
            """,
            (
                str(source["id"]),
                str(source.get("source_type", "local_api")),
                _optional_str(source.get("local_api_url")),
                _optional_str(source.get("data_dir")),
                _optional_str(source.get("library_id")),
                _optional_str(source.get("library_type")),
                str(source.get("name", "Zotero Local Library")),
                _optional_int(source.get("last_version")),
                str(source["created_at"]),
                str(source["updated_at"]),
            ),
        )

    def assert_zotero_source_version_fence(
        self,
        *,
        source_id: str,
        response_version: int,
    ) -> int | None:
        """Reject a snapshot older than this source's published library state."""

        selected = _nonnegative_version(
            response_version,
            label="response library version",
        )
        row = self.connection.execute(
            "SELECT last_version FROM zotero_sources WHERE id = ?",
            (source_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("Registered Zotero source disappeared during sync.")
        if row["last_version"] is None:
            return None
        known = _nonnegative_version(
            row["last_version"],
            label="stored source library version",
        )
        if known > selected:
            raise RuntimeError(
                "The local Zotero source version moved backward or a newer "
                "profile sync completed concurrently. Retry against the current "
                "library; restore an older library into a separate project."
            )
        return known

    def retire_unverified_zotero_source_claim(
        self,
        *,
        source_id: str,
        profile_id: str,
        run_id: str,
        corpus_id: str,
        retired_at: str,
        error_code: str,
        error_message: str,
    ) -> bool:
        """Retire an exclusively owned first-run locator after validation fails.

        This operation is deliberately strict. Any published cursor, imported
        object, active peer profile, queued job, or corpus document means the
        source is no longer an unverified claim. Failed run and removed profile
        rows remain as audit evidence, while the untrusted locator no longer
        blocks a later explicit first successful registration. The caller owns
        the surrounding transaction.
        """

        source = self.connection.execute(
            "SELECT last_version FROM zotero_sources WHERE id = ?",
            (source_id,),
        ).fetchone()
        if source is None or source["last_version"] is not None:
            return False
        profiles = self.connection.execute(
            """
            SELECT id, last_version, last_run_id
            FROM zotero_sync_profiles
            WHERE source_id = ?
            ORDER BY id
            """,
            (source_id,),
        ).fetchall()
        if (
            len(profiles) != 1
            or str(profiles[0]["id"]) != profile_id
            or profiles[0]["last_version"] is not None
            or profiles[0]["last_run_id"] is not None
        ):
            return False
        registered = self.connection.execute(
            """
            SELECT id FROM registered_sources
            WHERE zotero_source_id = ? AND removed_at IS NULL
            ORDER BY id
            """,
            (source_id,),
        ).fetchall()
        if [str(row["id"]) for row in registered] != [profile_id]:
            return False
        run = self.connection.execute(
            """
            SELECT status FROM zotero_import_runs
            WHERE id = ? AND source_id = ?
            """,
            (run_id, source_id),
        ).fetchone()
        if run is None or str(run["status"]) != "failed":
            return False
        peer_run_evidence = self.connection.execute(
            """
            SELECT 1
            FROM zotero_import_runs AS peer
            LEFT JOIN zotero_sync_run_details AS details
              ON details.run_id = peer.id
            WHERE peer.source_id = ? AND peer.id <> ?
              AND (
                peer.status IN ('running', 'completed', 'interrupted')
                OR details.response_version IS NOT NULL
              )
            LIMIT 1
            """,
            (source_id, run_id),
        ).fetchone()
        if peer_run_evidence is not None:
            return False
        guarded_counts = (
            ("zotero_items", "source_id", source_id),
            ("zotero_collections", "source_id", source_id),
            ("zotero_attachments", "source_id", source_id),
            ("zotero_child_items", "source_id", source_id),
            ("zotero_tombstones", "source_id", source_id),
            ("jobs", "source_id", profile_id),
            ("documents", "corpus_id", corpus_id),
            ("scan_runs", "corpus_id", corpus_id),
        )
        for table, column, value in guarded_counts:
            if (
                self.connection.execute(
                    f'SELECT 1 FROM "{table}" WHERE "{column}" = ? LIMIT 1',
                    (value,),
                ).fetchone()
                is not None
            ):
                return False
        self.connection.execute(
            "DELETE FROM zotero_profile_items WHERE profile_id = ?",
            (profile_id,),
        )
        self.connection.execute(
            "DELETE FROM zotero_sync_profiles WHERE id = ?",
            (profile_id,),
        )
        self.connection.execute(
            """
            UPDATE registered_sources
            SET removed_at = ?, updated_at = ?, last_error_code = ?,
                last_error_message = ?
            WHERE id = ? AND removed_at IS NULL
            """,
            (
                retired_at,
                retired_at,
                error_code,
                error_message,
                profile_id,
            ),
        )
        return True

    def create_zotero_import_run(
        self,
        run_id: str,
        source_id: str,
        *,
        started_at: str,
        config: Mapping[str, Any],
    ) -> None:
        """Create a Zotero import run record."""

        self.connection.execute(
            """
            INSERT INTO zotero_import_runs(
              id, source_id, started_at, status, config_json, owner_pid
            )
            VALUES (?, ?, ?, 'running', ?, ?)
            """,
            (
                run_id,
                source_id,
                started_at,
                json.dumps(dict(config), sort_keys=True),
                os.getpid(),
            ),
        )

    def ensure_registered_zotero_profile(
        self,
        *,
        profile_id: str,
        source_id: str,
        profile_signature: str,
        display_name: str,
        config: Mapping[str, Any],
        now: str,
    ) -> bool:
        """Register one canonical profile in the caller's open transaction.

        The importer's source row, profile registry row, sync cursor, and run
        audit must commit together. This narrow repository operation avoids a
        second connection committing a new locator identity halfway through
        preflight.
        """

        canonical_config = json.dumps(
            dict(config),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        by_id = self.connection.execute(
            "SELECT * FROM registered_sources WHERE id = ?",
            (profile_id,),
        ).fetchone()
        by_signature = self.connection.execute(
            """
            SELECT * FROM registered_sources
            WHERE kind = 'zotero_profile' AND profile_signature = ?
            """,
            (profile_signature,),
        ).fetchone()
        if by_id is not None and by_signature is not None:
            if str(by_id["id"]) != str(by_signature["id"]):
                raise RuntimeError("Registered Zotero profile identity is ambiguous.")
        existing = by_id if by_id is not None else by_signature
        if existing is None:
            self.connection.execute(
                """
                INSERT INTO registered_sources(
                  id, kind, display_name, root_path, zotero_source_id,
                  profile_signature, config_json, created_at, updated_at
                )
                VALUES (?, 'zotero_profile', ?, NULL, ?, ?, ?, ?, ?)
                """,
                (
                    profile_id,
                    display_name,
                    source_id,
                    profile_signature,
                    canonical_config,
                    now,
                    now,
                ),
            )
            return True
        if (
            str(existing["id"]) != profile_id
            or str(existing["kind"]) != "zotero_profile"
            or str(existing["zotero_source_id"]) != source_id
            or str(existing["profile_signature"]) != profile_signature
            or load_json_object(existing["config_json"]) != dict(config)
        ):
            raise RuntimeError("Registered Zotero profile identity is inconsistent.")
        if existing["removed_at"] is not None:
            self.connection.execute(
                """
                UPDATE registered_sources
                SET removed_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (now, profile_id),
            )
        return False

    def finish_zotero_import_run(
        self,
        run_id: str,
        *,
        finished_at: str,
        status: str,
        items_seen: int,
        items_imported: int,
        items_updated: int,
        items_unchanged: int,
        attachments_seen: int,
        attachments_resolved: int,
        pdfs_extracted: int,
        notes_imported: int,
        skipped: int,
        warnings: Iterable[str],
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Finish a Zotero import run summary."""

        self.connection.execute(
            """
            UPDATE zotero_import_runs
            SET finished_at = ?,
                status = ?,
                items_seen = ?,
                items_imported = ?,
                items_updated = ?,
                items_unchanged = ?,
                attachments_seen = ?,
                attachments_resolved = ?,
                pdfs_extracted = ?,
                notes_imported = ?,
                skipped = ?,
                warnings_json = ?,
                error_code = ?,
                error_message = ?
            WHERE id = ?
            """,
            (
                finished_at,
                status,
                items_seen,
                items_imported,
                items_updated,
                items_unchanged,
                attachments_seen,
                attachments_resolved,
                pdfs_extracted,
                notes_imported,
                skipped,
                json.dumps([str(warning) for warning in warnings], sort_keys=True),
                error_code,
                error_message,
                run_id,
            ),
        )

    def update_zotero_import_run_config(
        self,
        run_id: str,
        config: Mapping[str, Any],
    ) -> None:
        """Persist the resolved import profile before applying item writes."""

        self.connection.execute(
            "UPDATE zotero_import_runs SET config_json = ? WHERE id = ?",
            (json.dumps(dict(config), sort_keys=True), run_id),
        )

    def ensure_zotero_sync_profile(
        self,
        *,
        profile_id: str,
        source_id: str,
        profile_signature: str,
        now: str,
        materialization_signature: str | None = None,
    ) -> dict[str, object]:
        """Create/load a profile cursor without borrowing another filter's state."""

        registered = self.connection.execute(
            """
            SELECT kind, zotero_source_id, profile_signature
            FROM registered_sources
            WHERE id = ? AND removed_at IS NULL
            """,
            (profile_id,),
        ).fetchone()
        if (
            registered is None
            or str(registered["kind"]) != "zotero_profile"
            or str(registered["zotero_source_id"]) != source_id
            or str(registered["profile_signature"]) != profile_signature
        ):
            raise RuntimeError("Registered Zotero profile identity is inconsistent.")
        self.connection.execute(
            """
            INSERT INTO zotero_sync_profiles(
              id, source_id, profile_signature, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (
                profile_id,
                source_id,
                profile_signature,
                now,
                now,
            ),
        )
        row = self.connection.execute(
            """
            SELECT * FROM zotero_sync_profiles
            WHERE source_id = ? AND profile_signature = ?
            """,
            (source_id, profile_signature),
        ).fetchone()
        if row is None:
            raise RuntimeError("Zotero sync profile was not persisted.")
        state = _zotero_sync_profile_state(row)
        if materialization_signature is not None:
            state = self.prepare_zotero_sync_profile_materialization(
                profile_id=profile_id,
                materialization_signature=materialization_signature,
                now=now,
                explicit_full=False,
            )
        return state

    def prepare_zotero_sync_profile_materialization(
        self,
        *,
        profile_id: str,
        materialization_signature: str,
        now: str,
        explicit_full: bool = False,
    ) -> dict[str, object]:
        """Fence a content-affecting profile change before any remote reads.

        Changing the signature deliberately makes the next run a full sync and
        increments ``revision`` so a runner prepared against the old settings
        cannot publish its cursor. Legacy v9 profiles start with a NULL signature
        and therefore also require one explicit full materialization.
        """

        selected = _bounded_materialization_signature(materialization_signature)
        row = self.connection.execute(
            "SELECT * FROM zotero_sync_profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("Zotero sync profile was not persisted.")
        source_id = str(row["source_id"])
        signature_rows = self.connection.execute(
            """
            SELECT DISTINCT materialization_signature
            FROM zotero_sync_profiles
            WHERE source_id = ? AND materialization_signature IS NOT NULL
            ORDER BY materialization_signature
            """,
            (source_id,),
        ).fetchall()
        existing_signatures = {str(entry[0]) for entry in signature_rows}
        if len(existing_signatures) > 1 and not explicit_full:
            raise RuntimeError(
                "Zotero source has inconsistent materialization signatures; "
                "run an explicit full sync to repair it."
            )
        inherited = next(iter(existing_signatures), None)
        if inherited is not None and inherited != selected and not explicit_full:
            raise RuntimeError(
                "Zotero materialization settings differ from this source's active "
                "configuration; run an explicit full sync to change them."
            )
        target = selected if explicit_full or inherited is None else inherited
        if explicit_full:
            if existing_signatures != {selected}:
                affected_items = [
                    str(entry[0])
                    for entry in self.connection.execute(
                        """
                        SELECT DISTINCT membership.zotero_item_id
                        FROM zotero_profile_items membership
                        JOIN zotero_sync_profiles profile
                          ON profile.id = membership.profile_id
                        WHERE profile.source_id = ? AND membership.is_member = 1
                        ORDER BY membership.zotero_item_id
                        """,
                        (source_id,),
                    ).fetchall()
                ]
                self.connection.execute(
                    """
                    UPDATE zotero_sync_profiles
                    SET materialization_signature = ?, requires_full_sync = 1,
                        updated_at = ?, revision = revision + 1
                    WHERE source_id = ?
                    """,
                    (selected, now, source_id),
                )
                self.connection.execute(
                    """
                    UPDATE zotero_profile_items
                    SET is_member = 0, updated_at = ?
                    WHERE profile_id IN (
                      SELECT id FROM zotero_sync_profiles WHERE source_id = ?
                    )
                    """,
                    (now, source_id),
                )
                self.reconcile_zotero_document_activity(
                    affected_items,
                    now=now,
                )
            else:
                self.connection.execute(
                    """
                    UPDATE zotero_sync_profiles
                    SET requires_full_sync = 1, updated_at = ?,
                        revision = revision + 1
                    WHERE id = ?
                    """,
                    (now, profile_id),
                )
        else:
            self.connection.execute(
                """
                UPDATE zotero_sync_profiles
                SET materialization_signature = ?, requires_full_sync = 1,
                    updated_at = ?, revision = revision + 1
                WHERE source_id = ? AND materialization_signature IS NULL
                """,
                (target, now, source_id),
            )
        row = self.connection.execute(
            "SELECT * FROM zotero_sync_profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("Zotero sync profile disappeared.")
        if str(row["materialization_signature"]) != target:
            raise RuntimeError("Zotero profile materialization changed concurrently.")
        return _zotero_sync_profile_state(row)

    def complete_zotero_sync_profile(
        self,
        *,
        profile_id: str,
        expected_last_version: int | None,
        expected_revision: int,
        last_version: int,
        run_id: str,
        now: str,
        expected_materialization_signature: str,
    ) -> None:
        """Publish one fully applied sync cursor in the caller's final transaction."""

        cursor = self.connection.execute(
            """
            UPDATE zotero_sync_profiles
            SET last_version = ?, requires_full_sync = 0, last_run_id = ?,
                last_sync_at = ?, updated_at = ?, revision = revision + 1
            WHERE id = ?
              AND revision = ?
              AND last_version IS ?
              AND materialization_signature IS ?
            """,
            (
                last_version,
                run_id,
                now,
                now,
                profile_id,
                expected_revision,
                expected_last_version,
                _bounded_materialization_signature(expected_materialization_signature),
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("Zotero profile cursor changed concurrently.")

    def assert_zotero_sync_profile_fence(
        self,
        *,
        profile_id: str,
        expected_revision: int,
        expected_last_version: int | None,
        expected_materialization_signature: str,
    ) -> None:
        """Reject a stale runner inside the same transaction as item writes."""

        row = self.connection.execute(
            """
            SELECT 1
            FROM zotero_sync_profiles profile
            JOIN registered_sources source ON source.id = profile.id
            WHERE profile.id = ?
              AND profile.revision = ?
              AND profile.last_version IS ?
              AND profile.materialization_signature = ?
              AND source.kind = 'zotero_profile'
              AND source.removed_at IS NULL
            """,
            (
                profile_id,
                _nonnegative_version(expected_revision, label="profile revision"),
                (
                    None
                    if expected_last_version is None
                    else _nonnegative_version(
                        expected_last_version, label="profile cursor"
                    )
                ),
                _bounded_materialization_signature(expected_materialization_signature),
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError("Zotero profile sync fence changed concurrently.")

    def assert_zotero_sync_profile_preparation_fence(
        self,
        *,
        profile_id: str,
        expected_revision: int,
        expected_last_version: int | None,
        expected_materialization_signature: str | None,
    ) -> None:
        """Fence deferred generation preparation against profile races."""

        signature = (
            None
            if expected_materialization_signature is None
            else _bounded_materialization_signature(expected_materialization_signature)
        )
        row = self.connection.execute(
            """
            SELECT 1
            FROM zotero_sync_profiles AS profile
            JOIN registered_sources AS source ON source.id = profile.id
            WHERE profile.id = ?
              AND profile.revision = ?
              AND profile.last_version IS ?
              AND profile.materialization_signature IS ?
              AND source.kind = 'zotero_profile'
              AND source.removed_at IS NULL
            """,
            (
                profile_id,
                _nonnegative_version(expected_revision, label="profile revision"),
                (
                    None
                    if expected_last_version is None
                    else _nonnegative_version(
                        expected_last_version,
                        label="profile cursor",
                    )
                ),
                signature,
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError(
                "Zotero profile generation changed concurrently before preparation."
            )

    def upsert_zotero_profile_item(
        self,
        *,
        profile_id: str,
        zotero_item_id: str,
        observed_version: int,
        expected_profile_revision: int,
        expected_materialization_signature: str,
        now: str,
    ) -> bool:
        """Record one profile match only when profile and active item share a source."""

        cursor = self.connection.execute(
            """
            INSERT INTO zotero_profile_items(
              profile_id, zotero_item_id, is_member, observed_version,
              first_matched_at, updated_at
            )
            SELECT profile.id, item.id, 1, ?, ?, ?
            FROM zotero_sync_profiles profile
            JOIN registered_sources source ON source.id = profile.id
            JOIN zotero_items item ON item.id = ?
            WHERE profile.id = ?
              AND profile.source_id = item.source_id
              AND profile.revision = ?
              AND profile.materialization_signature = ?
              AND source.kind = 'zotero_profile'
              AND source.removed_at IS NULL
              AND item.deleted_at IS NULL
            ON CONFLICT(profile_id, zotero_item_id) DO UPDATE SET
              is_member = 1,
              observed_version = excluded.observed_version,
              first_matched_at = COALESCE(
                zotero_profile_items.first_matched_at,
                excluded.first_matched_at
              ),
              updated_at = excluded.updated_at
            WHERE excluded.observed_version >= zotero_profile_items.observed_version
            """,
            (
                _nonnegative_version(observed_version, label="observed version"),
                now,
                now,
                zotero_item_id,
                profile_id,
                expected_profile_revision,
                _bounded_materialization_signature(expected_materialization_signature),
            ),
        )
        return cursor.rowcount > 0

    def remove_zotero_profile_item(
        self,
        *,
        profile_id: str,
        zotero_item_id: str,
        observed_version: int,
        expected_profile_revision: int,
        expected_materialization_signature: str,
        now: str,
    ) -> bool:
        """Remove one profile match while preserving the shared Zotero item."""

        cursor = self.connection.execute(
            """
            INSERT INTO zotero_profile_items(
              profile_id, zotero_item_id, is_member, observed_version,
              first_matched_at, updated_at
            )
            SELECT profile.id, item.id, 0, ?, NULL, ?
            FROM zotero_sync_profiles profile
            JOIN registered_sources source ON source.id = profile.id
            JOIN zotero_items item ON item.id = ?
            WHERE profile.id = ?
              AND profile.source_id = item.source_id
              AND profile.revision = ?
              AND profile.materialization_signature = ?
              AND source.kind = 'zotero_profile'
              AND source.removed_at IS NULL
            ON CONFLICT(profile_id, zotero_item_id) DO UPDATE SET
              is_member = 0,
              observed_version = excluded.observed_version,
              updated_at = excluded.updated_at
            WHERE excluded.observed_version >= zotero_profile_items.observed_version
            """,
            (
                _nonnegative_version(observed_version, label="observed version"),
                now,
                zotero_item_id,
                profile_id,
                expected_profile_revision,
                _bounded_materialization_signature(expected_materialization_signature),
            ),
        )
        return cursor.rowcount > 0

    def list_zotero_profile_item_ids(self, profile_id: str) -> set[str]:
        """Return the stable membership set used to reconcile a full profile sync."""

        rows = self.connection.execute(
            """
            SELECT zotero_item_id
            FROM zotero_profile_items
            WHERE profile_id = ? AND is_member = 1
            ORDER BY zotero_item_id
            """,
            (profile_id,),
        ).fetchall()
        return {str(row["zotero_item_id"]) for row in rows}

    def remove_zotero_item_memberships(
        self, zotero_item_id: str, *, observed_version: int, now: str
    ) -> int:
        """Remove all profile memberships for a remotely deleted parent item."""

        return self.connection.execute(
            """
            UPDATE zotero_profile_items
            SET is_member = 0, observed_version = ?, updated_at = ?
            WHERE zotero_item_id = ? AND observed_version <= ?
            """,
            (
                _nonnegative_version(observed_version, label="observed version"),
                now,
                zotero_item_id,
                observed_version,
            ),
        ).rowcount

    def reconcile_zotero_document_activity(
        self,
        zotero_item_ids: Iterable[str],
        *,
        now: str,
    ) -> dict[str, int]:
        """Apply union-of-active-profile visibility to linked local documents."""

        item_ids = tuple(dict.fromkeys(str(value) for value in zotero_item_ids))
        changed = {"active": 0, "unindexed": 0, "missing": 0}
        for item_id in item_ids:
            row = self.connection.execute(
                """
                SELECT item.deleted_at,
                       EXISTS (
                         SELECT 1
                         FROM zotero_profile_items membership
                         JOIN zotero_sync_profiles profile
                           ON profile.id = membership.profile_id
                         JOIN registered_sources source
                           ON source.id = membership.profile_id
                         WHERE membership.zotero_item_id = item.id
                           AND membership.is_member = 1
                           AND profile.source_id = item.source_id
                           AND profile.materialization_signature IS NOT NULL
                           AND source.kind = 'zotero_profile'
                           AND source.removed_at IS NULL
                       ) AS has_membership
                FROM zotero_items item
                WHERE item.id = ?
                """,
                (item_id,),
            ).fetchone()
            if row is None:
                continue
            target = (
                "missing"
                if row["deleted_at"] is not None
                else "active"
                if bool(row["has_membership"])
                else "unindexed"
            )
            documents = self.connection.execute(
                """
                SELECT d.id, d.status
                FROM zotero_document_links link
                JOIN documents d ON d.id = link.document_id
                WHERE link.zotero_item_id = ?
                ORDER BY d.id
                """,
                (item_id,),
            ).fetchall()
            for document in documents:
                if str(document["status"]) == target:
                    continue
                document_id = str(document["id"])
                if target != "active":
                    self._delete_vectors_for_document(document_id)
                self.connection.execute(
                    "UPDATE documents SET status = ?, updated_at = ? WHERE id = ?",
                    (target, now, document_id),
                )
                changed[target] += 1
        return changed

    def record_zotero_sync_run_details(
        self,
        *,
        run_id: str,
        profile_id: str,
        full_sync: bool,
        previous_version: int | None,
        response_version: int | None,
        committed_version: int | None,
        changed_parents: int,
        changed_children: int,
        deleted_records: int,
        metadata_only_documents: int,
        pdf_failures: int,
        duration_ms: int,
    ) -> None:
        """Persist typed, path-free incremental sync audit counters."""

        self.connection.execute(
            """
            INSERT INTO zotero_sync_run_details(
              run_id, profile_id, full_sync, previous_version, response_version,
              committed_version, changed_parents, changed_children,
              deleted_records, metadata_only_documents, pdf_failures, duration_ms
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
              profile_id = excluded.profile_id,
              full_sync = excluded.full_sync,
              previous_version = excluded.previous_version,
              response_version = excluded.response_version,
              committed_version = excluded.committed_version,
              changed_parents = excluded.changed_parents,
              changed_children = excluded.changed_children,
              deleted_records = excluded.deleted_records,
              metadata_only_documents = excluded.metadata_only_documents,
              pdf_failures = excluded.pdf_failures,
              duration_ms = excluded.duration_ms
            """,
            (
                run_id,
                profile_id,
                int(full_sync),
                previous_version,
                response_version,
                committed_version,
                changed_parents,
                changed_children,
                deleted_records,
                metadata_only_documents,
                pdf_failures,
                duration_ms,
            ),
        )

    def list_zotero_child_payloads(
        self, source_id: str, parent_keys: Iterable[str]
    ) -> list[dict[str, Any]]:
        """Load active cached child JSON for selected parent keys in one query."""

        keys = tuple(dict.fromkeys(parent_keys))
        if not keys:
            return []
        placeholders = ",".join("?" for _ in keys)
        rows = self.connection.execute(
            f"""
            SELECT data_json
            FROM zotero_child_items
            WHERE source_id = ? AND parent_key IN ({placeholders})
              AND deleted_at IS NULL
            ORDER BY parent_key, item_type, zotero_key
            """,
            (source_id, *keys),
        ).fetchall()
        return [load_json_object(row["data_json"]) for row in rows]

    def parent_keys_for_zotero_children(
        self, source_id: str, child_keys: Iterable[str]
    ) -> dict[str, str]:
        """Resolve cached child keys to parents without scanning raw JSON in Python."""

        keys = tuple(dict.fromkeys(child_keys))
        if not keys:
            return {}
        placeholders = ",".join("?" for _ in keys)
        rows = self.connection.execute(
            f"""
            SELECT zotero_key, parent_key
            FROM zotero_child_items
            WHERE source_id = ? AND zotero_key IN ({placeholders})
            """,
            (source_id, *keys),
        ).fetchall()
        return {str(row["zotero_key"]): str(row["parent_key"]) for row in rows}

    def parent_keys_for_zotero_collections(
        self, source_id: str, collection_keys: Iterable[str]
    ) -> set[str]:
        """Return parents affected by collection-only rename/delete changes."""

        keys = tuple(dict.fromkeys(str(key) for key in collection_keys))
        if not keys:
            return set()
        placeholders = ",".join("?" for _ in keys)
        rows = self.connection.execute(
            f"""
            SELECT DISTINCT item.zotero_key
            FROM zotero_collections collection
            JOIN zotero_item_collections membership
              ON membership.collection_id = collection.id
            JOIN zotero_items item ON item.id = membership.zotero_item_id
            WHERE collection.source_id = ?
              AND collection.zotero_key IN ({placeholders})
              AND item.source_id = collection.source_id
            ORDER BY item.zotero_key
            """,
            (source_id, *keys),
        ).fetchall()
        return {str(row["zotero_key"]) for row in rows}

    def verified_child_deletions(
        self,
        source_id: str,
        child_keys: Iterable[str],
        *,
        library_version: int,
    ) -> dict[str, str]:
        """Return only cached children not newer than a deletion snapshot."""

        keys = tuple(dict.fromkeys(child_keys))
        if not keys:
            return {}
        placeholders = ",".join("?" for _ in keys)
        rows = self.connection.execute(
            f"""
            SELECT zotero_key, parent_key
            FROM zotero_child_items
            WHERE source_id = ? AND zotero_key IN ({placeholders})
              AND (version IS NULL OR version <= ?)
            """,
            (source_id, *keys, library_version),
        ).fetchall()
        return {str(row["zotero_key"]): str(row["parent_key"]) for row in rows}

    def upsert_zotero_child_item(
        self,
        *,
        source_id: str,
        zotero_key: str,
        parent_key: str,
        item_type: str,
        version: int | None,
        data: Mapping[str, Any],
        now: str,
    ) -> bool:
        """Persist one normalized child cache row with monotonic version checks."""

        if not self._zotero_tombstone_allows_version(
            source_id=source_id,
            object_type="item",
            zotero_key=zotero_key,
            incoming_version=version,
        ):
            return False
        canonical = json.dumps(dict(data), sort_keys=True)
        cursor = self.connection.execute(
            """
            INSERT INTO zotero_child_items(
              source_id, zotero_key, parent_key, item_type, version, data_json,
              created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, zotero_key) DO UPDATE SET
              parent_key = excluded.parent_key,
              item_type = excluded.item_type,
              version = excluded.version,
              data_json = excluded.data_json,
              deleted_at = NULL,
              deleted_version = NULL,
              updated_at = excluded.updated_at
            WHERE (
                    zotero_child_items.version IS NULL
                AND excluded.version IS NOT NULL
              )
               OR (
                    zotero_child_items.version IS NOT NULL
                AND excluded.version > zotero_child_items.version
              )
               OR (
                    (
                         excluded.version = zotero_child_items.version
                      OR (
                           excluded.version IS NULL
                       AND zotero_child_items.version IS NULL
                      )
                    )
                AND excluded.data_json = zotero_child_items.data_json
              )
            """,
            (
                source_id,
                zotero_key,
                parent_key,
                item_type,
                version,
                canonical,
                now,
                now,
            ),
        )
        return cursor.rowcount > 0

    def record_zotero_tombstone(
        self,
        *,
        source_id: str,
        object_type: str,
        zotero_key: str,
        library_version: int,
        deleted_at: str,
    ) -> None:
        """Record a remote deletion without storing private raw payloads."""

        if object_type == "item":
            row = self.connection.execute(
                """
                SELECT MAX(version)
                FROM (
                  SELECT version FROM zotero_items
                  WHERE source_id = ? AND zotero_key = ?
                  UNION ALL
                  SELECT version FROM zotero_child_items
                  WHERE source_id = ? AND zotero_key = ?
                )
                """,
                (source_id, zotero_key, source_id, zotero_key),
            ).fetchone()
            if row is not None and row[0] is not None and int(row[0]) > library_version:
                return
        elif object_type == "collection":
            row = self.connection.execute(
                """
                SELECT version FROM zotero_collections
                WHERE source_id = ? AND zotero_key = ?
                """,
                (source_id, zotero_key),
            ).fetchone()
            if row is not None and row[0] is not None and int(row[0]) > library_version:
                return
        self.connection.execute(
            """
            INSERT INTO zotero_tombstones(
              source_id, object_type, zotero_key, library_version, deleted_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source_id, object_type, zotero_key) DO UPDATE SET
              library_version = MAX(
                zotero_tombstones.library_version, excluded.library_version
              ),
              deleted_at = CASE
                WHEN excluded.library_version >= zotero_tombstones.library_version
                  THEN excluded.deleted_at
                ELSE zotero_tombstones.deleted_at
              END
            """,
            (source_id, object_type, zotero_key, library_version, deleted_at),
        )

    def clear_zotero_tombstone(
        self,
        *,
        source_id: str,
        object_type: str,
        zotero_key: str,
        incoming_version: int | None = None,
    ) -> bool:
        """Clear only a tombstone strictly older than the materialized object."""

        selected_version = incoming_version
        if selected_version is None:
            selected_version = self._persisted_zotero_object_version(
                source_id=source_id,
                object_type=object_type,
                zotero_key=zotero_key,
            )
        if selected_version is None:
            return False
        version = _nonnegative_version(selected_version, label="incoming version")
        cursor = self.connection.execute(
            """
            DELETE FROM zotero_tombstones
            WHERE source_id = ? AND object_type = ? AND zotero_key = ?
              AND library_version < ?
            """,
            (source_id, object_type, zotero_key, version),
        )
        return cursor.rowcount > 0

    def _zotero_tombstone_allows_version(
        self,
        *,
        source_id: str,
        object_type: str,
        zotero_key: str,
        incoming_version: int | None,
    ) -> bool:
        row = self.connection.execute(
            """
            SELECT library_version
            FROM zotero_tombstones
            WHERE source_id = ? AND object_type = ? AND zotero_key = ?
            """,
            (source_id, object_type, zotero_key),
        ).fetchone()
        if row is None:
            return True
        if incoming_version is None:
            return False
        return _nonnegative_version(incoming_version, label="incoming version") > int(
            row["library_version"]
        )

    def _persisted_zotero_object_version(
        self, *, source_id: str, object_type: str, zotero_key: str
    ) -> int | None:
        if object_type == "collection":
            row = self.connection.execute(
                """
                SELECT version
                FROM zotero_collections
                WHERE source_id = ? AND zotero_key = ?
                """,
                (source_id, zotero_key),
            ).fetchone()
            return _optional_int(row["version"]) if row is not None else None
        if object_type != "item":
            return None
        row = self.connection.execute(
            """
            SELECT MAX(version)
            FROM (
              SELECT version FROM zotero_items
              WHERE source_id = ? AND zotero_key = ?
              UNION ALL
              SELECT version FROM zotero_child_items
              WHERE source_id = ? AND zotero_key = ?
              UNION ALL
              SELECT version FROM zotero_attachments
              WHERE source_id = ? AND zotero_key = ?
            )
            """,
            (
                source_id,
                zotero_key,
                source_id,
                zotero_key,
                source_id,
                zotero_key,
            ),
        ).fetchone()
        return _optional_int(row[0]) if row is not None else None

    def mark_zotero_parent_deleted(
        self,
        *,
        source_id: str,
        zotero_key: str,
        library_version: int,
        deleted_at: str,
    ) -> bool:
        """Tombstone a parent and remove its document/vector state from live reads."""

        row = self.connection.execute(
            "SELECT id FROM zotero_items WHERE source_id = ? AND zotero_key = ?",
            (source_id, zotero_key),
        ).fetchone()
        if row is None:
            return False
        item_id = str(row["id"])
        document_rows = self.connection.execute(
            "SELECT document_id FROM zotero_document_links WHERE zotero_item_id = ?",
            (item_id,),
        ).fetchall()
        self.connection.execute(
            """
            UPDATE zotero_items
            SET deleted_at = ?, deleted_version = ?, updated_at = ?
            WHERE id = ?
              AND (version IS NULL OR version <= ?)
              AND (deleted_version IS NULL OR deleted_version <= ?)
            """,
            (
                deleted_at,
                library_version,
                deleted_at,
                item_id,
                library_version,
                library_version,
            ),
        )
        if self.connection.execute("SELECT changes()").fetchone()[0] != 1:
            return False
        self.connection.execute(
            """
            UPDATE zotero_child_items
            SET deleted_at = ?, deleted_version = ?, updated_at = ?
            WHERE source_id = ? AND parent_key = ?
              AND (version IS NULL OR version <= ?)
              AND (deleted_version IS NULL OR deleted_version <= ?)
            """,
            (
                deleted_at,
                library_version,
                deleted_at,
                source_id,
                zotero_key,
                library_version,
                library_version,
            ),
        )
        self.connection.execute(
            """
            UPDATE zotero_attachments
            SET deleted_at = ?, deleted_version = ?, updated_at = ?
            WHERE parent_zotero_item_id = ?
              AND (version IS NULL OR version <= ?)
              AND (deleted_version IS NULL OR deleted_version <= ?)
            """,
            (
                deleted_at,
                library_version,
                deleted_at,
                item_id,
                library_version,
                library_version,
            ),
        )
        self.remove_zotero_item_memberships(
            item_id,
            observed_version=library_version,
            now=deleted_at,
        )
        for document_row in document_rows:
            document_id = str(document_row["document_id"])
            self._delete_vectors_for_document(document_id)
            self.connection.execute(
                "UPDATE documents SET status = 'missing', updated_at = ? WHERE id = ?",
                (deleted_at, document_id),
            )
        return True

    def mark_zotero_child_deleted(
        self,
        *,
        source_id: str,
        zotero_key: str,
        library_version: int,
        deleted_at: str,
    ) -> str | None:
        """Tombstone a cached child and return the parent that needs rebuilding."""

        row = self.connection.execute(
            """
            SELECT parent_key FROM zotero_child_items
            WHERE source_id = ? AND zotero_key = ?
            """,
            (source_id, zotero_key),
        ).fetchone()
        if row is None:
            return None
        self.connection.execute(
            """
            UPDATE zotero_child_items
            SET deleted_at = ?, deleted_version = ?, updated_at = ?
            WHERE source_id = ? AND zotero_key = ?
              AND (version IS NULL OR version <= ?)
              AND (deleted_version IS NULL OR deleted_version <= ?)
            """,
            (
                deleted_at,
                library_version,
                deleted_at,
                source_id,
                zotero_key,
                library_version,
                library_version,
            ),
        )
        if self.connection.execute("SELECT changes()").fetchone()[0] != 1:
            return None
        self.connection.execute(
            """
            UPDATE zotero_attachments
            SET deleted_at = ?, deleted_version = ?, updated_at = ?
            WHERE source_id = ? AND zotero_key = ?
              AND (version IS NULL OR version <= ?)
              AND (deleted_version IS NULL OR deleted_version <= ?)
            """,
            (
                deleted_at,
                library_version,
                deleted_at,
                source_id,
                zotero_key,
                library_version,
                library_version,
            ),
        )
        return str(row["parent_key"])

    def mark_zotero_collection_deleted(
        self,
        *,
        source_id: str,
        zotero_key: str,
        library_version: int,
        deleted_at: str,
    ) -> bool:
        cursor = self.connection.execute(
            """
            UPDATE zotero_collections
            SET deleted_at = ?, deleted_version = ?
            WHERE source_id = ? AND zotero_key = ?
              AND (version IS NULL OR version <= ?)
              AND (deleted_version IS NULL OR deleted_version <= ?)
            """,
            (
                deleted_at,
                library_version,
                source_id,
                zotero_key,
                library_version,
                library_version,
            ),
        )
        return cursor.rowcount > 0

    def upsert_zotero_collection(self, collection: Mapping[str, Any]) -> bool:
        """Insert/update a collection unless its versioned payload regresses."""

        source_id = str(collection["source_id"])
        zotero_key = str(collection["zotero_key"])
        version = _optional_int(collection.get("version"))
        if not self._zotero_tombstone_allows_version(
            source_id=source_id,
            object_type="collection",
            zotero_key=zotero_key,
            incoming_version=version,
        ):
            return False
        cursor = self.connection.execute(
            """
            INSERT INTO zotero_collections(
              id, source_id, zotero_key, parent_key, name, path, version, data_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, zotero_key) DO UPDATE SET
              id = excluded.id,
              parent_key = excluded.parent_key,
              name = excluded.name,
              path = excluded.path,
              version = excluded.version,
              data_json = excluded.data_json,
              deleted_at = NULL,
              deleted_version = NULL
            WHERE (
                    zotero_collections.version IS NULL
                AND excluded.version IS NOT NULL
              )
               OR (
                    zotero_collections.version IS NOT NULL
                AND excluded.version > zotero_collections.version
              )
               OR (
                    (
                         excluded.version = zotero_collections.version
                      OR (
                           excluded.version IS NULL
                       AND zotero_collections.version IS NULL
                      )
                    )
                AND excluded.data_json = zotero_collections.data_json
              )
            """,
            (
                str(collection["id"]),
                source_id,
                zotero_key,
                _optional_str(collection.get("parent_key")),
                str(collection.get("name", "")),
                _optional_str(collection.get("path")),
                version,
                json.dumps(dict(collection.get("data", {})), sort_keys=True),
            ),
        )
        return cursor.rowcount > 0

    def upsert_zotero_item(self, item: Mapping[str, Any]) -> bool:
        """Insert/update one item unless its incoming version is older."""

        source_id = str(item["source_id"])
        zotero_key = str(item["zotero_key"])
        version = _optional_int(item.get("version"))
        if not self._zotero_tombstone_allows_version(
            source_id=source_id,
            object_type="item",
            zotero_key=zotero_key,
            incoming_version=version,
        ):
            return False
        cursor = self.connection.execute(
            """
            INSERT INTO zotero_items(
              id, source_id, zotero_key, version, item_type, title, year, date,
              date_added, date_modified, publication_title, doi, url,
              abstract_note, extra, reading_status, data_json, created_at,
              updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, zotero_key) DO UPDATE SET
              id = excluded.id,
              version = excluded.version,
              item_type = excluded.item_type,
              title = excluded.title,
              year = excluded.year,
              date = excluded.date,
              date_added = excluded.date_added,
              date_modified = excluded.date_modified,
              publication_title = excluded.publication_title,
              doi = excluded.doi,
              url = excluded.url,
              abstract_note = excluded.abstract_note,
              extra = excluded.extra,
              reading_status = excluded.reading_status,
              data_json = excluded.data_json,
              deleted_at = NULL,
              deleted_version = NULL,
              updated_at = excluded.updated_at
            WHERE zotero_items.version IS NULL
              AND excluded.version IS NOT NULL
               OR (
                    zotero_items.version IS NOT NULL
                AND excluded.version > zotero_items.version
              )
               OR (
                    (
                         excluded.version = zotero_items.version
                      OR (
                           excluded.version IS NULL
                       AND zotero_items.version IS NULL
                      )
                    )
                AND excluded.data_json = zotero_items.data_json
              )
            """,
            (
                str(item["id"]),
                source_id,
                zotero_key,
                version,
                str(item.get("item_type", "")),
                str(item.get("title", "")),
                _optional_str(item.get("year")),
                _optional_str(item.get("date")),
                _optional_str(item.get("date_added")),
                _optional_str(item.get("date_modified")),
                _optional_str(item.get("publication_title")),
                _optional_str(item.get("doi")),
                _optional_str(item.get("url")),
                _optional_str(item.get("abstract_note")),
                _optional_str(item.get("extra")),
                str(item.get("reading_status", "unknown")),
                json.dumps(dict(item.get("data", {})), sort_keys=True),
                str(item["created_at"]),
                str(item["updated_at"]),
            ),
        )
        return cursor.rowcount > 0

    def get_zotero_item_sync_state(
        self, source_id: str, zotero_key: str
    ) -> dict[str, object] | None:
        """Return private version state used to prevent regressive sync writes."""

        row = self.connection.execute(
            """
            SELECT version, data_json, child_manifest_json
            FROM zotero_items
            WHERE source_id = ? AND zotero_key = ?
            """,
            (source_id, zotero_key),
        ).fetchone()
        if row is None:
            return None
        return {
            "version": _optional_int(row["version"]),
            "data": load_json_object(row["data_json"]),
            "child_manifest": (
                None
                if row["child_manifest_json"] is None
                else load_json_list(row["child_manifest_json"])
            ),
        }

    def update_zotero_child_manifest(
        self,
        zotero_item_id: str,
        manifest: Iterable[Mapping[str, Any]],
    ) -> None:
        """Replace the private child-version manifest after derived writes succeed."""

        payload = [dict(entry) for entry in manifest]
        self.connection.execute(
            """
            UPDATE zotero_items
            SET child_manifest_json = ?
            WHERE id = ?
            """,
            (json.dumps(payload, sort_keys=True), zotero_item_id),
        )

    def get_zotero_item_by_source_key(
        self, source_id: str, zotero_key: str
    ) -> dict[str, object] | None:
        """Return one Zotero item by source/key."""

        row = self.connection.execute(
            """
            SELECT *
            FROM zotero_items
            WHERE source_id = ? AND zotero_key = ?
            """,
            (source_id, zotero_key),
        ).fetchone()
        return self._zotero_item_payload(row) if row is not None else None

    def replace_zotero_creators(
        self, zotero_item_id: str, creators: Iterable[Mapping[str, Any]]
    ) -> None:
        """Replace creators for a Zotero item."""

        self.connection.execute(
            "DELETE FROM zotero_creators WHERE zotero_item_id = ?",
            (zotero_item_id,),
        )
        self.connection.executemany(
            """
            INSERT INTO zotero_creators(
              id, zotero_item_id, creator_type, first_name, last_name, name,
              order_index
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    _zotero_child_row_id(zotero_item_id, "creator", index),
                    zotero_item_id,
                    str(creator.get("creator_type", "author")),
                    _optional_str(creator.get("first_name")),
                    _optional_str(creator.get("last_name")),
                    _optional_str(creator.get("name")),
                    index,
                )
                for index, creator in enumerate(creators)
            ],
        )

    def replace_zotero_item_tags(
        self, zotero_item_id: str, tags: Iterable[Mapping[str, Any]]
    ) -> None:
        """Replace tags for a Zotero item."""

        self.connection.execute(
            "DELETE FROM zotero_item_tags WHERE zotero_item_id = ?",
            (zotero_item_id,),
        )
        self.connection.executemany(
            """
            INSERT INTO zotero_item_tags(zotero_item_id, tag, tag_type)
            VALUES (?, ?, ?)
            """,
            [
                (
                    zotero_item_id,
                    str(tag.get("tag", "")),
                    _optional_int(tag.get("type")),
                )
                for tag in tags
                if str(tag.get("tag", "")).strip()
            ],
        )

    def replace_zotero_item_collections(
        self, zotero_item_id: str, collection_ids: Iterable[str]
    ) -> None:
        """Replace collection memberships for a Zotero item."""

        self.connection.execute(
            "DELETE FROM zotero_item_collections WHERE zotero_item_id = ?",
            (zotero_item_id,),
        )
        self.connection.executemany(
            """
            INSERT OR IGNORE INTO zotero_item_collections(
              zotero_item_id, collection_id
            )
            VALUES (?, ?)
            """,
            [(zotero_item_id, collection_id) for collection_id in collection_ids],
        )

    def upsert_zotero_attachment(self, attachment: Mapping[str, Any]) -> bool:
        """Insert/update an attachment unless its versioned payload regresses."""

        source_id = str(attachment["source_id"])
        zotero_key = str(attachment["zotero_key"])
        version = _optional_int(attachment.get("version"))
        if not self._zotero_tombstone_allows_version(
            source_id=source_id,
            object_type="item",
            zotero_key=zotero_key,
            incoming_version=version,
        ):
            return False
        cursor = self.connection.execute(
            """
            INSERT INTO zotero_attachments(
              id, source_id, parent_zotero_item_id, zotero_key, title, filename,
              content_type, link_mode, zotero_path, resolved_path, path_status,
              version, data_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, zotero_key) DO UPDATE SET
              id = excluded.id,
              parent_zotero_item_id = excluded.parent_zotero_item_id,
              title = excluded.title,
              filename = excluded.filename,
              content_type = excluded.content_type,
              link_mode = excluded.link_mode,
              zotero_path = excluded.zotero_path,
              resolved_path = excluded.resolved_path,
              path_status = excluded.path_status,
              version = excluded.version,
              data_json = excluded.data_json,
              deleted_at = NULL,
              deleted_version = NULL,
              updated_at = excluded.updated_at
            WHERE (
                    zotero_attachments.version IS NULL
                AND excluded.version IS NOT NULL
              )
               OR (
                    zotero_attachments.version IS NOT NULL
                AND excluded.version > zotero_attachments.version
              )
               OR (
                    (
                         excluded.version = zotero_attachments.version
                      OR (
                           excluded.version IS NULL
                       AND zotero_attachments.version IS NULL
                      )
                    )
                AND excluded.data_json = zotero_attachments.data_json
              )
            """,
            (
                str(attachment["id"]),
                source_id,
                _optional_str(attachment.get("parent_zotero_item_id")),
                zotero_key,
                _optional_str(attachment.get("title")),
                _optional_str(attachment.get("filename")),
                _optional_str(attachment.get("content_type")),
                _optional_str(attachment.get("link_mode")),
                _optional_str(attachment.get("zotero_path")),
                _optional_str(attachment.get("resolved_path")),
                str(attachment.get("path_status", "unsupported")),
                version,
                json.dumps(dict(attachment.get("data", {})), sort_keys=True),
                str(attachment["created_at"]),
                str(attachment["updated_at"]),
            ),
        )
        return cursor.rowcount > 0

    def retain_zotero_attachments(
        self,
        zotero_item_id: str,
        attachment_ids: Iterable[str],
    ) -> int:
        """Remove derived attachment rows absent from an authoritative assembly.

        The child cache and tombstones remain the remote audit source. These
        rows are materialized API detail, so deleting an option-excluded row is
        deliberately not represented as a remote Zotero deletion.
        """

        keep = tuple(dict.fromkeys(str(value) for value in attachment_ids))
        if not keep:
            return self.connection.execute(
                "DELETE FROM zotero_attachments WHERE parent_zotero_item_id = ?",
                (zotero_item_id,),
            ).rowcount
        placeholders = ",".join("?" for _ in keep)
        return self.connection.execute(
            f"""
            DELETE FROM zotero_attachments
            WHERE parent_zotero_item_id = ?
              AND id NOT IN ({placeholders})
            """,
            (zotero_item_id, *keep),
        ).rowcount

    def upsert_zotero_document_link(
        self,
        *,
        document_id: str,
        zotero_item_id: str,
        attachment_id: str | None,
        role: str,
    ) -> None:
        """Link a Paper Galaxy document to a Zotero item."""

        self.connection.execute(
            """
            INSERT INTO zotero_document_links(
              document_id, zotero_item_id, attachment_id, role
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(document_id, zotero_item_id, role) DO UPDATE SET
              attachment_id = excluded.attachment_id
            """,
            (document_id, zotero_item_id, attachment_id, role),
        )

    def list_zotero_items(
        self,
        *,
        limit: int = 100,
        status: str = "all",
        collection: str | None = None,
        tag: str | None = None,
        q: str | None = None,
    ) -> list[dict[str, object]]:
        """List imported Zotero items with lightweight related metadata."""

        where, params = _zotero_filter_clauses(
            status=status,
            collection=collection,
            tag=tag,
            q=q,
        )
        rows = self.connection.execute(
            f"""
            SELECT DISTINCT zi.*
            FROM zotero_items zi
            LEFT JOIN zotero_item_tags zit ON zit.zotero_item_id = zi.id
            LEFT JOIN zotero_item_collections zic ON zic.zotero_item_id = zi.id
            LEFT JOIN zotero_collections zc ON zc.id = zic.collection_id
            {where}
            ORDER BY zi.date_modified DESC, zi.title
            LIMIT ?
            """,
            (*params, max(0, limit)),
        ).fetchall()
        return [self._zotero_item_detail_from_row(row) for row in rows]

    def get_zotero_item_detail(self, zotero_item_id: str) -> dict[str, object] | None:
        """Return imported Zotero item details without full document text."""

        row = self.connection.execute(
            "SELECT * FROM zotero_items WHERE id = ?",
            (zotero_item_id,),
        ).fetchone()
        if row is None or row["deleted_at"] is not None:
            return None
        return self._zotero_item_detail_from_row(row)

    def list_zotero_documents_with_text(
        self,
        *,
        status: str = "all",
        collection: str | None = None,
        tag: str | None = None,
        limit: int = 1000,
    ) -> list[tuple[IndexedDocument, str, dict[str, object]]]:
        """Return active Paper Galaxy documents linked to Zotero items."""

        where, params = _zotero_filter_clauses(
            status=status,
            collection=collection,
            tag=tag,
            q=None,
        )
        rows = self.connection.execute(
            f"""
            SELECT DISTINCT
              d.*,
              dt.text,
              zi.id AS zotero_item_id,
              zi.zotero_key,
              zi.reading_status,
              zi.title AS zotero_title,
              zi.item_type AS zotero_item_type,
              zi.publication_title AS zotero_publication_title,
              zi.year AS zotero_year,
              zi.doi AS zotero_doi,
              zi.url AS zotero_url,
              zi.date_modified AS zotero_date_modified
            FROM zotero_items zi
            JOIN zotero_document_links zdl ON zdl.zotero_item_id = zi.id
            JOIN documents d ON d.id = zdl.document_id
            JOIN document_texts dt ON dt.document_id = d.id
            LEFT JOIN zotero_item_tags zit ON zit.zotero_item_id = zi.id
            LEFT JOIN zotero_item_collections zic ON zic.zotero_item_id = zi.id
            LEFT JOIN zotero_collections zc ON zc.id = zic.collection_id
            {where}
              AND d.status = 'active'
            ORDER BY zi.title
            LIMIT ?
            """,
            (*params, max(0, limit)),
        ).fetchall()
        return [
            (
                _document_from_row(row),
                str(row["text"]),
                self._zotero_reading_meta(row),
            )
            for row in rows
        ]

    def zotero_stats(self) -> dict[str, object]:
        """Return imported Zotero counts and last-run metadata."""

        reading_rows = self.connection.execute(
            """
            SELECT reading_status, COUNT(*) AS count
            FROM zotero_items
            WHERE deleted_at IS NULL
            GROUP BY reading_status
            ORDER BY reading_status
            """
        ).fetchall()
        last_run = self.zotero_import_status()
        return {
            "source_count": _scalar_int(
                self.connection,
                "SELECT COUNT(*) FROM zotero_sources",
            ),
            "imported_item_count": _scalar_int(
                self.connection,
                "SELECT COUNT(*) FROM zotero_items WHERE deleted_at IS NULL",
            ),
            "imported_document_count": _scalar_int(
                self.connection,
                """
                SELECT COUNT(DISTINCT zdl.document_id)
                FROM zotero_document_links zdl
                JOIN zotero_items zi ON zi.id = zdl.zotero_item_id
                JOIN documents d ON d.id = zdl.document_id
                WHERE zi.deleted_at IS NULL AND d.status = 'active'
                """,
            ),
            "attachment_count": _scalar_int(
                self.connection,
                "SELECT COUNT(*) FROM zotero_attachments WHERE deleted_at IS NULL",
            ),
            "missing_attachment_count": _scalar_int(
                self.connection,
                """
                SELECT COUNT(*)
                FROM zotero_attachments
                WHERE deleted_at IS NULL
                  AND path_status IN ('missing', 'no_local_file', 'unsupported')
                """,
            ),
            "reading_status_counts": {
                str(row["reading_status"]): int(row["count"]) for row in reading_rows
            },
            "last_import_run": last_run,
            "warnings": _json_list(last_run.get("warnings") if last_run else []),
        }

    def zotero_import_status(self) -> dict[str, object] | None:
        """Return the latest Zotero import run, if any."""

        row = self.connection.execute(
            """
            SELECT zir.*, zsd.profile_id, zsd.full_sync,
                   zsd.previous_version, zsd.response_version,
                   zsd.committed_version, zsd.changed_parents,
                   zsd.changed_children, zsd.deleted_records,
                   zsd.metadata_only_documents, zsd.pdf_failures,
                   zsd.duration_ms
            FROM zotero_import_runs zir
            LEFT JOIN zotero_sync_run_details zsd ON zsd.run_id = zir.id
            ORDER BY zir.started_at DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return {
            "id": str(row["id"]),
            "source_id": str(row["source_id"]),
            "started_at": str(row["started_at"]),
            "finished_at": _optional_str(row["finished_at"]),
            "status": str(row["status"]),
            "items_seen": int(row["items_seen"]),
            "items_imported": int(row["items_imported"]),
            "items_updated": int(row["items_updated"]),
            "items_unchanged": int(row["items_unchanged"]),
            "attachments_seen": int(row["attachments_seen"]),
            "attachments_resolved": int(row["attachments_resolved"]),
            "pdfs_extracted": int(row["pdfs_extracted"]),
            "notes_imported": int(row["notes_imported"]),
            "skipped": int(row["skipped"]),
            "warnings": _json_list(row["warnings_json"]),
            "config": _json_object(row["config_json"]),
            "profile_id": _optional_str(row["profile_id"]),
            "full_sync": bool(row["full_sync"])
            if row["full_sync"] is not None
            else None,
            "previous_version": _optional_int(row["previous_version"]),
            "response_version": _optional_int(row["response_version"]),
            "committed_version": _optional_int(row["committed_version"]),
            "changed_parents": int(row["changed_parents"] or 0),
            "changed_children": int(row["changed_children"] or 0),
            "deleted_records": int(row["deleted_records"] or 0),
            "metadata_only_documents": int(row["metadata_only_documents"] or 0),
            "pdf_failures": int(row["pdf_failures"] or 0),
            "duration_ms": int(row["duration_ms"] or 0),
        }

    def zotero_dangling_counts(self) -> dict[str, int]:
        """Return Zotero-specific dangling reference counts."""

        return {
            "zotero_links_without_documents": _scalar_int(
                self.connection,
                """
                SELECT COUNT(*)
                FROM zotero_document_links zdl
                LEFT JOIN documents d ON d.id = zdl.document_id
                WHERE d.id IS NULL
                """,
            ),
            "zotero_links_without_items": _scalar_int(
                self.connection,
                """
                SELECT COUNT(*)
                FROM zotero_document_links zdl
                LEFT JOIN zotero_items zi ON zi.id = zdl.zotero_item_id
                WHERE zi.id IS NULL
                """,
            ),
            "zotero_attachments_without_items": _scalar_int(
                self.connection,
                """
                SELECT COUNT(*)
                FROM zotero_attachments za
                LEFT JOIN zotero_items zi ON zi.id = za.parent_zotero_item_id
                WHERE za.parent_zotero_item_id IS NOT NULL AND zi.id IS NULL
                """,
            ),
        }

    def _zotero_item_detail_from_row(self, row: sqlite3.Row) -> dict[str, object]:
        payload = self._zotero_item_payload(row)
        item_id = str(row["id"])
        payload["creators"] = self._zotero_creators(item_id)
        payload["tags"] = self._zotero_tags(item_id)
        payload["collections"] = self._zotero_collections(item_id)
        payload["attachments"] = self._zotero_attachments(item_id)
        payload["document_links"] = self._zotero_document_links(item_id)
        return payload

    def _zotero_item_payload(self, row: sqlite3.Row) -> dict[str, object]:
        return {
            "id": str(row["id"]),
            "source_id": str(row["source_id"]),
            "zotero_key": str(row["zotero_key"]),
            "version": _optional_int(row["version"]),
            "item_type": str(row["item_type"]),
            "title": str(row["title"]),
            "year": _optional_str(row["year"]),
            "date": _optional_str(row["date"]),
            "date_added": _optional_str(row["date_added"]),
            "date_modified": _optional_str(row["date_modified"]),
            "publication_title": _optional_str(row["publication_title"]),
            "doi": _optional_str(row["doi"]),
            "url": _optional_str(row["url"]),
            "abstract_note": _optional_str(row["abstract_note"]),
            "extra": _optional_str(row["extra"]),
            "reading_status": str(row["reading_status"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "zotero_uri": _zotero_select_uri(str(row["zotero_key"])),
        }

    def _zotero_reading_meta(self, row: sqlite3.Row) -> dict[str, object]:
        item_id = str(row["zotero_item_id"])
        creators = [
            " ".join(
                str(part)
                for part in (
                    creator.get("first_name"),
                    creator.get("last_name"),
                    creator.get("name"),
                )
                if part is not None and str(part)
            ).strip()
            for creator in self._zotero_creators(item_id)
        ]
        collections = self._zotero_collections(item_id)
        attachments = self._zotero_attachments(item_id)
        attachment_counts = Counter(str(item["path_status"]) for item in attachments)
        primary_status = attachments[0]["path_status"] if attachments else None
        return {
            "zotero_item_id": item_id,
            "zotero_key": str(row["zotero_key"]),
            "reading_status": str(row["reading_status"]),
            "title": str(row["zotero_title"]),
            "item_type": _optional_str(row["zotero_item_type"]),
            "creators": "; ".join(name for name in creators if name),
            "year": _optional_str(row["zotero_year"]),
            "publication_title": _optional_str(row["zotero_publication_title"]),
            "publication": " ".join(
                part
                for part in (
                    _optional_str(row["zotero_publication_title"]),
                    _optional_str(row["zotero_year"]),
                )
                if part
            ),
            "doi": _optional_str(row["zotero_doi"]),
            "url": _optional_str(row["zotero_url"]),
            "date_modified": _optional_str(row["zotero_date_modified"]),
            "tags": "; ".join(str(tag["tag"]) for tag in self._zotero_tags(item_id)),
            "collections": "; ".join(
                str(collection.get("path") or collection.get("name") or "")
                for collection in collections
            ),
            "attachment_status": "; ".join(
                str(attachment["path_status"]) for attachment in attachments
            ),
            "attachment_status_counts": dict(attachment_counts),
            "primary_attachment_status": primary_status,
            "pdf_text_status": (
                "extracted" if str(row["file_type"]) == "pdf" else "metadata_only"
            ),
            "zotero_uri": _zotero_select_uri(str(row["zotero_key"])),
        }

    def _zotero_creators(self, item_id: str) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            SELECT *
            FROM zotero_creators
            WHERE zotero_item_id = ?
            ORDER BY order_index
            """,
            (item_id,),
        ).fetchall()
        return [
            {
                "creator_type": str(row["creator_type"]),
                "first_name": _optional_str(row["first_name"]),
                "last_name": _optional_str(row["last_name"]),
                "name": _optional_str(row["name"]),
                "order_index": int(row["order_index"]),
            }
            for row in rows
        ]

    def _zotero_tags(self, item_id: str) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            SELECT tag, tag_type
            FROM zotero_item_tags
            WHERE zotero_item_id = ?
            ORDER BY tag COLLATE NOCASE
            """,
            (item_id,),
        ).fetchall()
        return [
            {"tag": str(row["tag"]), "type": _optional_int(row["tag_type"])}
            for row in rows
        ]

    def _zotero_collections(self, item_id: str) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            SELECT zc.*
            FROM zotero_collections zc
            JOIN zotero_item_collections zic ON zic.collection_id = zc.id
            WHERE zic.zotero_item_id = ? AND zc.deleted_at IS NULL
            ORDER BY COALESCE(zc.path, zc.name) COLLATE NOCASE
            """,
            (item_id,),
        ).fetchall()
        return [
            {
                "id": str(row["id"]),
                "zotero_key": str(row["zotero_key"]),
                "name": str(row["name"]),
                "path": _optional_str(row["path"]),
                "parent_key": _optional_str(row["parent_key"]),
            }
            for row in rows
        ]

    def _zotero_attachments(self, item_id: str) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            SELECT *
            FROM zotero_attachments
            WHERE parent_zotero_item_id = ? AND deleted_at IS NULL
            ORDER BY title COLLATE NOCASE
            """,
            (item_id,),
        ).fetchall()
        return [
            {
                "id": str(row["id"]),
                "zotero_key": str(row["zotero_key"]),
                "title": _optional_str(row["title"]),
                "filename": _optional_str(row["filename"]),
                "content_type": _optional_str(row["content_type"]),
                "link_mode": _optional_str(row["link_mode"]),
                "path_status": str(row["path_status"]),
            }
            for row in rows
        ]

    def _zotero_document_links(self, item_id: str) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            SELECT zdl.*, d.title, d.relative_path, d.file_type, d.status
            FROM zotero_document_links zdl
            LEFT JOIN documents d ON d.id = zdl.document_id
            WHERE zdl.zotero_item_id = ?
            ORDER BY zdl.role, zdl.document_id
            """,
            (item_id,),
        ).fetchall()
        return [
            {
                "document_id": str(row["document_id"]),
                "attachment_id": _optional_str(row["attachment_id"]),
                "role": str(row["role"]),
                "title": _optional_str(row["title"]),
                "relative_path": _optional_str(row["relative_path"]),
                "file_type": _optional_str(row["file_type"]),
                "status": _optional_str(row["status"]),
            }
            for row in rows
        ]

    def count_rows(self, table_name: str) -> int:
        """Return a table row count for known internal tables."""

        if table_name not in _KNOWN_COUNT_TABLES:
            raise ValueError(f"Unsupported table name: {table_name}")
        return _scalar_int(self.connection, f"SELECT COUNT(*) FROM {table_name}")

    def dangling_row_counts(self) -> dict[str, int]:
        """Return cheap referential-integrity checks for validation."""

        counts = {
            "chunks_without_documents": _scalar_int(
                self.connection,
                """
                SELECT COUNT(*)
                FROM chunks c
                LEFT JOIN documents d ON d.id = c.document_id
                WHERE d.id IS NULL
                """,
            ),
            "texts_without_documents": _scalar_int(
                self.connection,
                """
                SELECT COUNT(*)
                FROM document_texts dt
                LEFT JOIN documents d ON d.id = dt.document_id
                WHERE d.id IS NULL
                """,
            ),
            "vectors_without_documents": _scalar_int(
                self.connection,
                """
                SELECT COUNT(*)
                FROM vectors v
                LEFT JOIN documents d ON d.id = v.object_id
                WHERE v.object_type = 'document' AND d.id IS NULL
                """,
            ),
            "map_points_without_documents": _scalar_int(
                self.connection,
                """
                SELECT COUNT(*)
                FROM map_run_points p
                LEFT JOIN documents d ON d.id = p.document_id
                WHERE d.id IS NULL
                """,
            ),
        }
        counts.update(self.zotero_dangling_counts())
        return counts

    def touch_document(self, document_id: str, now: str) -> None:
        self.connection.execute(
            """
            UPDATE documents
            SET last_seen_at = ?,
                status = 'active'
            WHERE id = ?
            """,
            (now, document_id),
        )
        self.connection.execute(
            """
            INSERT INTO documents_fts(document_id, title, relative_path, text)
            SELECT d.id, d.title, d.relative_path, dt.text
            FROM documents d
            JOIN document_texts dt ON dt.document_id = d.id
            WHERE d.id = ?
              AND NOT EXISTS (
                SELECT 1
                FROM documents_fts
                WHERE document_id = d.id
              )
            """,
            (document_id,),
        )

    def mark_document_unindexed(
        self,
        document_id: str,
        *,
        path: str,
        sha256: str,
        size_bytes: int,
        mtime_ns: int,
        now: str,
    ) -> None:
        self._delete_vectors_for_document(document_id)
        self.connection.execute(
            """
            UPDATE documents
            SET path = ?,
                sha256 = ?,
                size_bytes = ?,
                mtime_ns = ?,
                status = 'unindexed',
                last_seen_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (path, sha256, size_bytes, mtime_ns, now, now, document_id),
        )

    def upsert_document(
        self, document: IndexedDocument, text: str, chunks: list[IndexedChunk]
    ) -> None:
        content_revision = document_content_revision_sha256(
            title=document.title,
            relative_path=document.relative_path,
            text=text,
        )
        self._delete_vectors_for_document(document.id)
        self.connection.execute(
            """
            INSERT INTO documents(
              id, corpus_id, path, relative_path, file_type, title, sha256,
              content_revision_sha256, size_bytes, mtime_ns, char_count,
              status, first_seen_at, last_seen_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              path = excluded.path,
              file_type = excluded.file_type,
              title = excluded.title,
              sha256 = excluded.sha256,
              content_revision_sha256 = excluded.content_revision_sha256,
              size_bytes = excluded.size_bytes,
              mtime_ns = excluded.mtime_ns,
              char_count = excluded.char_count,
              status = excluded.status,
              last_seen_at = excluded.last_seen_at,
              updated_at = excluded.updated_at
            """,
            (
                document.id,
                document.corpus_id,
                document.path,
                document.relative_path,
                document.file_type,
                document.title,
                document.sha256,
                content_revision,
                document.size_bytes,
                document.mtime_ns,
                document.char_count,
                document.status,
                document.first_seen_at,
                document.last_seen_at,
                document.updated_at,
            ),
        )
        self.connection.execute(
            """
            INSERT INTO document_texts(document_id, text)
            VALUES (?, ?)
            ON CONFLICT(document_id) DO UPDATE SET text = excluded.text
            """,
            (document.id, text),
        )
        self.connection.execute(
            "DELETE FROM chunks WHERE document_id = ?", (document.id,)
        )
        self.connection.executemany(
            """
            INSERT INTO chunks(
              id, document_id, chunk_index, text, char_count, text_sha256
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    chunk.id,
                    chunk.document_id,
                    chunk.chunk_index,
                    chunk.text,
                    chunk.char_count,
                    chunk.text_sha256,
                )
                for chunk in chunks
            ],
        )
        self.connection.execute(
            "DELETE FROM documents_fts WHERE document_id = ?",
            (document.id,),
        )
        self.connection.execute(
            """
            INSERT INTO documents_fts(document_id, title, relative_path, text)
            VALUES (?, ?, ?, ?)
            """,
            (document.id, document.title, document.relative_path, text),
        )

    def _delete_vectors_for_document(self, document_id: str) -> None:
        document_vectors = self.connection.execute(
            """
            DELETE FROM vectors
            WHERE object_type = 'document' AND object_id = ?
            """,
            (document_id,),
        ).rowcount
        chunk_vectors = self.connection.execute(
            """
            DELETE FROM vectors
            WHERE object_type = 'chunk'
              AND object_id IN (
                SELECT id FROM chunks WHERE document_id = ?
              )
            """,
            (document_id,),
        ).rowcount
        if document_vectors:
            self.connection.execute(
                "DELETE FROM vector_indexes WHERE object_type = 'document'"
            )
        if chunk_vectors:
            self.connection.execute(
                "DELETE FROM vector_indexes WHERE object_type = 'chunk'"
            )

    def record_skipped_file(
        self,
        *,
        scan_run_id: str,
        corpus_id: str,
        relative_path: str,
        reason: str,
        created_at: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO skipped_files(
              id, scan_run_id, corpus_id, relative_path, reason, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                _skipped_id(scan_run_id, relative_path, reason),
                scan_run_id,
                corpus_id,
                relative_path,
                reason,
                created_at,
            ),
        )

    def record_extraction_report(self, report: ExtractionReport) -> None:
        """Persist compact extraction diagnostics for one file."""

        self.connection.execute(
            """
            INSERT INTO extraction_reports(
              id, scan_run_id, document_id, corpus_id, relative_path, file_type,
              method, status, char_count, warnings_json, metadata_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report.id,
                report.scan_run_id,
                report.document_id,
                report.corpus_id,
                report.relative_path,
                report.file_type,
                report.method,
                report.status,
                report.char_count,
                json.dumps(list(report.warnings), sort_keys=True),
                json.dumps(report.metadata, sort_keys=True, default=str),
                report.created_at,
            ),
        )

    def latest_extraction_fingerprint(
        self, corpus_id: str, relative_path: str
    ) -> str | None:
        """Return the latest stored extraction fingerprint for a corpus path."""

        row = self.connection.execute(
            """
            SELECT metadata_json
            FROM extraction_reports
            WHERE corpus_id = ? AND relative_path = ?
            ORDER BY created_at DESC, rowid DESC
            LIMIT 1
            """,
            (corpus_id, relative_path),
        ).fetchone()
        if row is None:
            return None
        metadata = load_json_object(row["metadata_json"])
        fingerprint = metadata.get("extraction_fingerprint")
        return str(fingerprint) if fingerprint else None

    def list_extraction_reports(self, scan_run_id: str) -> list[ExtractionReport]:
        """List extraction reports for one scan run in stable path order."""

        rows = self.connection.execute(
            """
            SELECT *
            FROM extraction_reports
            WHERE scan_run_id = ?
            ORDER BY relative_path
            """,
            (scan_run_id,),
        ).fetchall()
        return [_extraction_report_from_row(row) for row in rows]

    def mark_missing_documents(
        self, corpus_id: str, seen_document_ids: set[str], now: str
    ) -> int:
        active_rows = self.connection.execute(
            """
            SELECT id
            FROM documents
            WHERE corpus_id = ? AND status = 'active'
            """,
            (corpus_id,),
        ).fetchall()
        missing_ids = [
            str(row["id"])
            for row in active_rows
            if str(row["id"]) not in seen_document_ids
        ]
        for document_id in missing_ids:
            self._delete_vectors_for_document(document_id)
            self.connection.execute(
                """
                UPDATE documents
                SET status = 'missing',
                    updated_at = ?
                WHERE id = ?
                """,
                (now, document_id),
            )
        return len(missing_ids)

    def search_documents(
        self, query: str, *, limit: int = 10, include_missing: bool = False
    ) -> list[SearchResult]:
        fts_query = query
        try:
            return self._search_with_query(
                fts_query, limit=limit, include_missing=include_missing
            )
        except sqlite3.OperationalError:
            safe_query = _safe_fts_query(query)
            if not safe_query:
                return []
            return self._search_with_query(
                safe_query, limit=limit, include_missing=include_missing
            )

    def _search_with_query(
        self, query: str, *, limit: int, include_missing: bool
    ) -> list[SearchResult]:
        rows = self.connection.execute(
            """
            SELECT
              d.id,
              d.title,
              d.relative_path,
              d.file_type,
              d.char_count,
              d.updated_at,
              snippet(documents_fts, 3, '[', ']', ' ... ', 16) AS snippet,
              bm25(documents_fts) AS score
            FROM documents_fts
            JOIN documents d ON d.id = documents_fts.document_id
            WHERE documents_fts MATCH ?
              AND (
                d.status = 'active'
                OR (? AND d.status = 'missing')
              )
            ORDER BY bm25(documents_fts), d.relative_path
            LIMIT ?
            """,
            (query, int(include_missing), max(0, limit)),
        ).fetchall()
        return [
            SearchResult(
                rank=index + 1,
                document_id=str(row["id"]),
                title=str(row["title"]),
                relative_path=str(row["relative_path"]),
                file_type=str(row["file_type"]),
                char_count=int(row["char_count"]),
                updated_at=str(row["updated_at"]),
                snippet=str(row["snippet"] or ""),
                score=float(row["score"]),
            )
            for index, row in enumerate(rows)
        ]

    def get_stats(self) -> DatabaseStats:
        documents = _scalar_int(self.connection, "SELECT COUNT(*) FROM documents")
        active = _scalar_int(
            self.connection,
            "SELECT COUNT(*) FROM documents WHERE status = 'active'",
        )
        missing = _scalar_int(
            self.connection,
            "SELECT COUNT(*) FROM documents WHERE status = 'missing'",
        )
        unindexed = _scalar_int(
            self.connection,
            "SELECT COUNT(*) FROM documents WHERE status = 'unindexed'",
        )
        chunks = _scalar_int(self.connection, "SELECT COUNT(*) FROM chunks")
        scan_runs = _scalar_int(self.connection, "SELECT COUNT(*) FROM scan_runs")
        total_chars = _scalar_int(
            self.connection,
            """
            SELECT COALESCE(SUM(char_count), 0)
            FROM documents
            WHERE status = 'active'
            """,
        )
        last_scan = self.connection.execute(
            """
            SELECT COALESCE(finished_at, started_at) AS scan_time
            FROM scan_runs
            ORDER BY started_at DESC
            LIMIT 1
            """
        ).fetchone()
        return DatabaseStats(
            database_path=self.database_path,
            documents=documents,
            active_documents=active,
            missing_documents=missing,
            unindexed_documents=unindexed,
            chunks=chunks,
            scan_runs=scan_runs,
            last_scan_time=str(last_scan["scan_time"]) if last_scan else None,
            total_indexed_characters=total_chars,
        )


_KNOWN_COUNT_TABLES = {
    "documents",
    "document_texts",
    "chunks",
    "scan_runs",
    "skipped_files",
    "extraction_reports",
    "embedding_models",
    "vectors",
    "embedding_runs",
    "vector_indexes",
    "cluster_label_overrides",
    "map_runs",
    "map_run_points",
    "map_run_clusters",
    "zotero_sources",
    "zotero_import_runs",
    "zotero_items",
    "zotero_creators",
    "zotero_collections",
    "zotero_item_collections",
    "zotero_item_tags",
    "zotero_attachments",
    "zotero_document_links",
    "zotero_sync_profiles",
    "zotero_profile_items",
    "zotero_sync_run_details",
    "zotero_child_items",
    "zotero_tombstones",
}


def _zotero_sync_profile_state(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "source_id": str(row["source_id"]),
        "profile_signature": str(row["profile_signature"]),
        "materialization_signature": _optional_str(row["materialization_signature"]),
        "last_version": _optional_int(row["last_version"]),
        "requires_full_sync": bool(row["requires_full_sync"]),
        "revision": int(row["revision"]),
        "last_run_id": _optional_str(row["last_run_id"]),
    }


def _bounded_materialization_signature(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(
            "Zotero materialization signature must be a lowercase SHA-256 digest."
        )
    return value


def _nonnegative_version(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"Zotero {label} must be a non-negative integer.")
    return value


def _document_from_row(row: sqlite3.Row) -> IndexedDocument:
    return IndexedDocument(
        id=str(row["id"]),
        corpus_id=str(row["corpus_id"]),
        path=str(row["path"]),
        relative_path=str(row["relative_path"]),
        file_type=str(row["file_type"]),
        title=str(row["title"]),
        sha256=str(row["sha256"]),
        size_bytes=int(row["size_bytes"]),
        mtime_ns=int(row["mtime_ns"]),
        char_count=int(row["char_count"]),
        status=str(row["status"]),
        first_seen_at=str(row["first_seen_at"]),
        last_seen_at=str(row["last_seen_at"]),
        updated_at=str(row["updated_at"]),
        content_revision_sha256=str(row["content_revision_sha256"]),
    )


def _document_from_prefix(row: sqlite3.Row, prefix: str) -> IndexedDocument:
    return IndexedDocument(
        id=str(row[f"{prefix}id"]),
        corpus_id=str(row[f"{prefix}corpus_id"]),
        path=str(row[f"{prefix}path"]),
        relative_path=str(row[f"{prefix}relative_path"]),
        file_type=str(row[f"{prefix}file_type"]),
        title=str(row[f"{prefix}title"]),
        sha256=str(row[f"{prefix}sha256"]),
        size_bytes=int(row[f"{prefix}size_bytes"]),
        mtime_ns=int(row[f"{prefix}mtime_ns"]),
        char_count=int(row[f"{prefix}char_count"]),
        status=str(row[f"{prefix}status"]),
        first_seen_at=str(row[f"{prefix}first_seen_at"]),
        last_seen_at=str(row[f"{prefix}last_seen_at"]),
        updated_at=str(row[f"{prefix}updated_at"]),
        content_revision_sha256=str(row[f"{prefix}content_revision_sha256"]),
    )


def _embedding_model_from_row(row: sqlite3.Row) -> EmbeddingModelRecord:
    return EmbeddingModelRecord(
        id=str(row["id"]),
        name=str(row["name"]),
        provider=str(row["provider"]),
        dimension=int(row["dimension"]),
        distance=str(row["distance"]),
        config=load_json_object(row["config_json"]),
        model_fingerprint=str(row["model_fingerprint"]),
        fingerprint_algorithm=str(row["fingerprint_algorithm"]),
        created_at=str(row["created_at"]),
    )


def _vector_from_row(row: sqlite3.Row) -> VectorRecord:
    return VectorRecord(
        id=str(row["id"]),
        model_id=str(row["model_id"]),
        object_type=str(row["object_type"]),
        object_id=str(row["object_id"]),
        text_sha256=str(row["text_sha256"]),
        source_content_sha256=str(row["source_content_sha256"]),
        model_fingerprint=str(row["model_fingerprint"]),
        algorithm_version=str(row["algorithm_version"]),
        dimension=int(row["dimension"]),
        dtype=str(row["dtype"]),
        vector=bytes(row["vector"]),
        metadata=load_json_object(row["metadata_json"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _embedding_run_payload(row: sqlite3.Row | None) -> dict[str, object] | None:
    if row is None:
        return None
    return {
        "id": str(row["id"]),
        "model_id": str(row["model_id"]),
        "model_name": str(row["model_name"] or ""),
        "started_at": str(row["started_at"]),
        "finished_at": str(row["finished_at"]) if row["finished_at"] else None,
        "status": str(row["status"]),
        "documents_seen": int(row["documents_seen"]),
        "documents_embedded": int(row["documents_embedded"]),
        "documents_unchanged": int(row["documents_unchanged"]),
        "chunks_seen": int(row["chunks_seen"]),
        "chunks_embedded": int(row["chunks_embedded"]),
        "chunks_unchanged": int(row["chunks_unchanged"]),
        "sources_changed": int(row["sources_changed"]),
        "errors": int(row["errors"]),
        "config": load_json_object(row["config_json"]),
    }


def _cluster_label_override_payload(row: sqlite3.Row | None) -> dict[str, object]:
    if row is None:
        return {}
    return {
        "id": str(row["id"]),
        "cluster_signature": str(row["cluster_signature"]),
        "label": str(row["label"]),
        "source": str(row["source"]),
        "metadata": load_json_object(row["metadata_json"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _map_run_payload(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "name": str(row["name"]),
        "created_at": str(row["created_at"]),
        "status": str(row["status"]),
        "similarity_mode": str(row["similarity_mode"]),
        "model_id": str(row["model_id"]) if row["model_id"] is not None else None,
        "seed": int(row["seed"]),
        "requested_clusters": (
            int(row["requested_clusters"])
            if row["requested_clusters"] is not None
            else None
        ),
        "requested_neighbors": int(row["requested_neighbors"]),
        "requested_limit": int(row["requested_limit"]),
        "document_count": int(row["document_count"]),
        "cluster_count": int(row["cluster_count"]),
        "document_set_signature": str(row["document_set_signature"]),
        "warnings": load_json_list(row["warnings_json"]),
        "metadata": load_json_object(row["metadata_json"]),
    }


def _map_run_point_payload(row: sqlite3.Row) -> dict[str, object]:
    return {
        "document_id": str(row["document_id"]),
        "x": float(row["x"]),
        "y": float(row["y"]),
        "cluster_id": int(row["cluster_id"]),
        "cluster_label": str(row["cluster_label"]),
        "cluster_signature": str(row["cluster_signature"]),
        "top_terms": load_json_list(row["top_terms_json"]),
        "nearest_neighbors": load_json_list(row["nearest_neighbors_json"]),
    }


def _map_run_cluster_payload(row: sqlite3.Row) -> dict[str, object]:
    return {
        "cluster_id": int(row["cluster_id"]),
        "cluster_signature": str(row["cluster_signature"]),
        "display_label": str(row["display_label"]),
        "generated_label": str(row["generated_label"]),
        "source": str(row["source"]),
        "size": int(row["size"]),
        "document_ids": load_json_list(row["document_ids_json"]),
        "top_terms": load_json_list(row["top_terms_json"]),
        "representatives": load_json_list(row["representatives_json"]),
        "warnings": load_json_list(row["warnings_json"]),
    }


def _zotero_child_row_id(parent_id: str, kind: str, index: int) -> str:
    digest = hashlib.sha256(f"{parent_id}\0{kind}\0{index}".encode()).hexdigest()
    return f"zotero_{kind}_{digest[:16]}"


def _zotero_filter_clauses(
    *,
    status: str,
    collection: str | None,
    tag: str | None,
    q: str | None,
) -> tuple[str, tuple[object, ...]]:
    clauses = ["zi.deleted_at IS NULL"]
    params: list[object] = []
    if status != "all":
        clauses.append("zi.reading_status = ?")
        params.append(status)
    if collection:
        clauses.append("zc.deleted_at IS NULL")
        clauses.append(
            "(zc.zotero_key = ? OR zc.name = ? COLLATE NOCASE "
            "OR zc.path = ? COLLATE NOCASE)"
        )
        params.extend([collection, collection, collection])
    if tag:
        clauses.append("zit.tag = ?")
        params.append(tag)
    if q:
        like = f"%{q.lower()}%"
        clauses.append(
            "(LOWER(zi.title) LIKE ? OR LOWER(COALESCE(zi.abstract_note, '')) LIKE ?)"
        )
        params.extend([like, like])
    return f"WHERE {' AND '.join(clauses)}", tuple(params)


def _zotero_select_uri(zotero_key: str) -> str | None:
    if re.fullmatch(r"[A-Z0-9]{8,}", zotero_key):
        return f"zotero://select/items/{zotero_key}"
    return None


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return None


def _extraction_report_from_row(row: sqlite3.Row) -> ExtractionReport:
    warnings = tuple(str(warning) for warning in load_json_list(row["warnings_json"]))
    metadata = load_json_object(row["metadata_json"])
    return ExtractionReport(
        id=str(row["id"]),
        scan_run_id=str(row["scan_run_id"]),
        document_id=str(row["document_id"]) if row["document_id"] is not None else None,
        corpus_id=str(row["corpus_id"]),
        relative_path=str(row["relative_path"]),
        file_type=str(row["file_type"]),
        method=str(row["method"]),
        status=str(row["status"]),
        char_count=int(row["char_count"]),
        warnings=warnings,
        metadata=metadata,
        created_at=str(row["created_at"]),
    )


def _skipped_id(scan_run_id: str, relative_path: str, reason: str) -> str:
    digest = hashlib.sha256(
        f"{scan_run_id}\0{relative_path}\0{reason}".encode()
    ).hexdigest()
    return f"skip_{digest[:16]}"


def _cluster_label_override_id(cluster_signature: str) -> str:
    digest = hashlib.sha256(cluster_signature.encode("utf-8")).hexdigest()
    return f"cluster_label_{digest[:16]}"


def _json_list(value: object) -> list[object]:
    return load_json_list(value)


def _json_object(value: object) -> dict[str, object]:
    return load_json_object(value)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _safe_fts_query(query: str) -> str:
    tokens = re.findall(r"[\w]+", query, flags=re.UNICODE)
    return " OR ".join(f'"{token}"' for token in tokens)


def _status_filter(
    statuses: set[str] | None, *, table_alias: str | None = None
) -> tuple[str, tuple[str, ...]]:
    if not statuses:
        return "", ()
    ordered_statuses = tuple(sorted(statuses))
    placeholders = ", ".join("?" for _ in ordered_statuses)
    column = f"{table_alias}.status" if table_alias else "status"
    return f"WHERE {column} IN ({placeholders})", ordered_statuses


def _scalar_int(
    connection: sqlite3.Connection,
    sql: str,
    parameters: tuple[object, ...] = (),
) -> int:
    row = connection.execute(sql, parameters).fetchone()
    return int(row[0]) if row is not None else 0
