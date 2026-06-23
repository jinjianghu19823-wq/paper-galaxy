"""Explicit SQLite repository operations for Phase 2."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

from paper_galaxy.embeddings.models import EmbeddingModelRecord, VectorRecord
from paper_galaxy.records import (
    DatabaseStats,
    ExtractionReport,
    IndexedChunk,
    IndexedDocument,
    SearchResult,
)


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
            INSERT INTO scan_runs(id, corpus_id, corpus_path, started_at, status)
            VALUES (?, ?, ?, ?, 'running')
            """,
            (scan_run_id, corpus_id, corpus_path, now),
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
                status = ?
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
            ORDER BY relative_path
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
            ORDER BY d.relative_path
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
            SELECT id, document_id, chunk_index, text, char_count
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
              c.char_count AS chunk_char_count
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            {status_sql}
            ORDER BY d.relative_path, c.chunk_index
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
                ),
            )
            for row in rows
        ]

    def get_document_by_id_or_relative_path(
        self, document_id_or_path: str
    ) -> IndexedDocument | None:
        row = self.connection.execute(
            """
            SELECT *
            FROM documents
            WHERE id = ? OR relative_path = ?
            ORDER BY CASE WHEN id = ? THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (document_id_or_path, document_id_or_path, document_id_or_path),
        ).fetchone()
        return _document_from_row(row) if row is not None else None

    def upsert_embedding_model(self, model: EmbeddingModelRecord) -> None:
        self.connection.execute(
            """
            INSERT INTO embedding_models(
              id, name, provider, dimension, distance, config_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              name = excluded.name,
              provider = excluded.provider,
              dimension = excluded.dimension,
              distance = excluded.distance,
              config_json = excluded.config_json
            """,
            (
                model.id,
                model.name,
                model.provider,
                model.dimension,
                model.distance,
                json.dumps(model.config, sort_keys=True),
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
            INSERT INTO embedding_runs(id, model_id, started_at, status, config_json)
            VALUES (?, ?, ?, 'running', ?)
            """,
            (run_id, model_id, started_at, json.dumps(config, sort_keys=True)),
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
        errors: int = 0,
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
                errors = ?
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
                errors,
                run_id,
            ),
        )

    def upsert_vector(self, vector: VectorRecord) -> None:
        self.connection.execute(
            """
            INSERT INTO vectors(
              id, model_id, object_type, object_id, text_sha256, dimension, dtype,
              vector, metadata_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(model_id, object_type, object_id) DO UPDATE SET
              id = excluded.id,
              text_sha256 = excluded.text_sha256,
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
                vector.dimension,
                vector.dtype,
                vector.vector,
                json.dumps(vector.metadata, sort_keys=True),
                vector.created_at,
                vector.updated_at,
            ),
        )

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
                    "config": json.loads(str(row["config_json"])),
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
                    "created_at": str(row["created_at"]),
                    "metadata": json.loads(str(row["metadata_json"])),
                }
                for row in index_rows
            ],
        }

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
        self.connection.execute(
            """
            INSERT INTO documents(
              id, corpus_id, path, relative_path, file_type, title, sha256,
              size_bytes, mtime_ns, char_count, status, first_seen_at,
              last_seen_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              path = excluded.path,
              file_type = excluded.file_type,
              title = excluded.title,
              sha256 = excluded.sha256,
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
            INSERT INTO chunks(id, document_id, chunk_index, text, char_count)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    chunk.id,
                    chunk.document_id,
                    chunk.chunk_index,
                    chunk.text,
                    chunk.char_count,
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
        try:
            metadata = json.loads(str(row["metadata_json"]))
        except json.JSONDecodeError:
            return None
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
    )


def _embedding_model_from_row(row: sqlite3.Row) -> EmbeddingModelRecord:
    config = json.loads(str(row["config_json"]))
    return EmbeddingModelRecord(
        id=str(row["id"]),
        name=str(row["name"]),
        provider=str(row["provider"]),
        dimension=int(row["dimension"]),
        distance=str(row["distance"]),
        config=config if isinstance(config, dict) else {},
        created_at=str(row["created_at"]),
    )


def _vector_from_row(row: sqlite3.Row) -> VectorRecord:
    metadata = json.loads(str(row["metadata_json"]))
    return VectorRecord(
        id=str(row["id"]),
        model_id=str(row["model_id"]),
        object_type=str(row["object_type"]),
        object_id=str(row["object_id"]),
        text_sha256=str(row["text_sha256"]),
        dimension=int(row["dimension"]),
        dtype=str(row["dtype"]),
        vector=bytes(row["vector"]),
        metadata=metadata if isinstance(metadata, dict) else {},
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _embedding_run_payload(row: sqlite3.Row | None) -> dict[str, object] | None:
    if row is None:
        return None
    config = json.loads(str(row["config_json"]))
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
        "errors": int(row["errors"]),
        "config": config if isinstance(config, dict) else {},
    }


def _extraction_report_from_row(row: sqlite3.Row) -> ExtractionReport:
    warnings_raw = json.loads(str(row["warnings_json"]))
    metadata_raw = json.loads(str(row["metadata_json"]))
    warnings = tuple(str(warning) for warning in warnings_raw)
    metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
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
