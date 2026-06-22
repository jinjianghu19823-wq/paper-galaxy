"""Explicit SQLite repository operations for Phase 2."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

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
