"""Explicit SQLite repository operations for Phase 2."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
              last_version = excluded.last_version,
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
              id, source_id, started_at, status, config_json
            )
            VALUES (?, ?, ?, 'running', ?)
            """,
            (run_id, source_id, started_at, json.dumps(dict(config), sort_keys=True)),
        )

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
                warnings_json = ?
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
                run_id,
            ),
        )

    def upsert_zotero_collection(self, collection: Mapping[str, Any]) -> None:
        """Insert or update normalized Zotero collection metadata."""

        self.connection.execute(
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
              data_json = excluded.data_json
            """,
            (
                str(collection["id"]),
                str(collection["source_id"]),
                str(collection["zotero_key"]),
                _optional_str(collection.get("parent_key")),
                str(collection.get("name", "")),
                _optional_str(collection.get("path")),
                _optional_int(collection.get("version")),
                json.dumps(dict(collection.get("data", {})), sort_keys=True),
            ),
        )

    def upsert_zotero_item(self, item: Mapping[str, Any]) -> None:
        """Insert or update normalized Zotero item metadata."""

        self.connection.execute(
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
              updated_at = excluded.updated_at
            """,
            (
                str(item["id"]),
                str(item["source_id"]),
                str(item["zotero_key"]),
                _optional_int(item.get("version")),
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

    def upsert_zotero_attachment(self, attachment: Mapping[str, Any]) -> None:
        """Insert or update a Zotero attachment record."""

        self.connection.execute(
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
              updated_at = excluded.updated_at
            """,
            (
                str(attachment["id"]),
                str(attachment["source_id"]),
                _optional_str(attachment.get("parent_zotero_item_id")),
                str(attachment["zotero_key"]),
                _optional_str(attachment.get("title")),
                _optional_str(attachment.get("filename")),
                _optional_str(attachment.get("content_type")),
                _optional_str(attachment.get("link_mode")),
                _optional_str(attachment.get("zotero_path")),
                _optional_str(attachment.get("resolved_path")),
                str(attachment.get("path_status", "unsupported")),
                _optional_int(attachment.get("version")),
                json.dumps(dict(attachment.get("data", {})), sort_keys=True),
                str(attachment["created_at"]),
                str(attachment["updated_at"]),
            ),
        )

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
        if row is None:
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
              zi.publication_title AS zotero_publication_title,
              zi.year AS zotero_year
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
                "SELECT COUNT(*) FROM zotero_items",
            ),
            "imported_document_count": _scalar_int(
                self.connection,
                "SELECT COUNT(DISTINCT document_id) FROM zotero_document_links",
            ),
            "attachment_count": _scalar_int(
                self.connection, "SELECT COUNT(*) FROM zotero_attachments"
            ),
            "missing_attachment_count": _scalar_int(
                self.connection,
                """
                SELECT COUNT(*)
                FROM zotero_attachments
                WHERE path_status IN ('missing', 'no_local_file', 'unsupported')
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
            SELECT *
            FROM zotero_import_runs
            ORDER BY started_at DESC
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
            "data": _json_object(row["data_json"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "zotero_uri": f"zotero://select/items/{row['zotero_key']}",
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
        return {
            "zotero_item_id": item_id,
            "zotero_key": str(row["zotero_key"]),
            "reading_status": str(row["reading_status"]),
            "title": str(row["zotero_title"]),
            "creators": "; ".join(name for name in creators if name),
            "publication": " ".join(
                part
                for part in (
                    _optional_str(row["zotero_publication_title"]),
                    _optional_str(row["zotero_year"]),
                )
                if part
            ),
            "tags": "; ".join(str(tag["tag"]) for tag in self._zotero_tags(item_id)),
            "collections": "; ".join(
                str(collection.get("path") or collection.get("name") or "")
                for collection in collections
            ),
            "attachment_status": "; ".join(
                str(attachment["path_status"]) for attachment in attachments
            ),
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
            WHERE zic.zotero_item_id = ?
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
            WHERE parent_zotero_item_id = ?
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
                "zotero_path": _optional_str(row["zotero_path"]),
                "resolved_path": _optional_str(row["resolved_path"]),
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
}


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


def _cluster_label_override_payload(row: sqlite3.Row | None) -> dict[str, object]:
    if row is None:
        return {}
    metadata = json.loads(str(row["metadata_json"]))
    return {
        "id": str(row["id"]),
        "cluster_signature": str(row["cluster_signature"]),
        "label": str(row["label"]),
        "source": str(row["source"]),
        "metadata": metadata if isinstance(metadata, dict) else {},
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _map_run_payload(row: sqlite3.Row) -> dict[str, object]:
    warnings = json.loads(str(row["warnings_json"]))
    metadata = json.loads(str(row["metadata_json"]))
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
        "warnings": warnings if isinstance(warnings, list) else [],
        "metadata": metadata if isinstance(metadata, dict) else {},
    }


def _map_run_point_payload(row: sqlite3.Row) -> dict[str, object]:
    return {
        "document_id": str(row["document_id"]),
        "x": float(row["x"]),
        "y": float(row["y"]),
        "cluster_id": int(row["cluster_id"]),
        "cluster_label": str(row["cluster_label"]),
        "cluster_signature": str(row["cluster_signature"]),
        "top_terms": _json_list(json.loads(str(row["top_terms_json"]))),
        "nearest_neighbors": _json_list(json.loads(str(row["nearest_neighbors_json"]))),
    }


def _map_run_cluster_payload(row: sqlite3.Row) -> dict[str, object]:
    return {
        "cluster_id": int(row["cluster_id"]),
        "cluster_signature": str(row["cluster_signature"]),
        "display_label": str(row["display_label"]),
        "generated_label": str(row["generated_label"]),
        "source": str(row["source"]),
        "size": int(row["size"]),
        "document_ids": _json_list(json.loads(str(row["document_ids_json"]))),
        "top_terms": _json_list(json.loads(str(row["top_terms_json"]))),
        "representatives": _json_list(json.loads(str(row["representatives_json"]))),
        "warnings": _json_list(json.loads(str(row["warnings_json"]))),
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
    clauses = ["1 = 1"]
    params: list[object] = []
    if status != "all":
        clauses.append("zi.reading_status = ?")
        params.append(status)
    if collection:
        clauses.append("(zc.zotero_key = ? OR zc.name = ? OR zc.path = ?)")
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


def _cluster_label_override_id(cluster_signature: str) -> str:
    digest = hashlib.sha256(cluster_signature.encode("utf-8")).hexdigest()
    return f"cluster_label_{digest[:16]}"


def _json_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _json_object(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


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
