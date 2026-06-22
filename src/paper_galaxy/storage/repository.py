"""Explicit SQLite repository operations for Phase 2."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path

from paper_galaxy.records import (
    DatabaseStats,
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
            self.connection.execute(
                "DELETE FROM documents_fts WHERE document_id = ?",
                (document_id,),
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
              AND (? OR d.status != 'missing')
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


def _skipped_id(scan_run_id: str, relative_path: str, reason: str) -> str:
    digest = hashlib.sha256(
        f"{scan_run_id}\0{relative_path}\0{reason}".encode()
    ).hexdigest()
    return f"skip_{digest[:16]}"


def _safe_fts_query(query: str) -> str:
    tokens = re.findall(r"[\w]+", query, flags=re.UNICODE)
    return " OR ".join(f'"{token}"' for token in tokens)


def _scalar_int(connection: sqlite3.Connection, sql: str) -> int:
    row = connection.execute(sql).fetchone()
    return int(row[0]) if row is not None else 0
