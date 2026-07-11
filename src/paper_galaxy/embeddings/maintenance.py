"""Explicit, local-only maintenance for stale dense-vector rows."""

from __future__ import annotations

import json
import math
import sqlite3
import struct
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from paper_galaxy.embeddings.builder import vector_algorithm_version
from paper_galaxy.embeddings.codec import FLOAT32_BYTES, FLOAT32_DTYPE
from paper_galaxy.embeddings.models import (
    LEGACY_UNKNOWN_PROVENANCE,
    stable_embedding_model_id,
)
from paper_galaxy.storage.json import StoredJSONError, load_json_object
from paper_galaxy.storage.sqlite import (
    connect_read_only,
    connect_read_write,
    ensure_database_ready,
)


@dataclass(frozen=True)
class VectorMaintenanceReport:
    """Safe summary of a dry-run or applied stale-vector prune."""

    dry_run: bool
    vectors_scanned: int
    stale_vectors: int
    vectors_deleted: int
    stale_index_metadata: int
    index_metadata_deleted: int
    reasons: dict[str, int]


@dataclass(frozen=True)
class _InvalidVector:
    vector_id: str
    model_id: str
    object_type: str
    updated_at: str
    reasons: tuple[str, ...]


def prune_stale_vectors(
    project_dir: Path,
    *,
    dry_run: bool = True,
    yes: bool = False,
) -> VectorMaintenanceReport:
    """Report or delete only vector rows that cannot be proven current.

    The default is read-only. Applying a prune requires both ``dry_run=False``
    and ``yes=True``. This operation never removes source files, project
    databases, backups, or on-disk index files.
    """

    if not dry_run and not yes:
        raise ValueError("Applying vector maintenance requires explicit --yes.")

    resolved_project = project_dir.expanduser().resolve()
    if dry_run:
        connection = connect_read_only(resolved_project)
    else:
        ensure_database_ready(resolved_project)
        connection = connect_read_write(resolved_project)

    try:
        if dry_run:
            connection.execute("BEGIN")
        else:
            connection.execute("BEGIN IMMEDIATE")
        vectors_scanned = 0
        stale_vectors = 0
        deleted = 0
        deleted_groups: set[tuple[str, str]] = set()
        reason_counts: Counter[str] = Counter()
        last_vector_rowid: int | None = None
        while rows := _vector_audit_page(connection, last_vector_rowid):
            last_vector_rowid = int(rows[-1]["audit_rowid"])
            vectors_scanned += len(rows)
            for row in rows:
                item = _classify_vector(row)
                if item is None:
                    continue
                stale_vectors += 1
                reason_counts.update(item.reasons)
                if not dry_run:
                    cursor = connection.execute(
                        """
                        DELETE FROM vectors
                        WHERE id = ? AND updated_at = ?
                        """,
                        (item.vector_id, item.updated_at),
                    )
                    if cursor.rowcount:
                        deleted += 1
                        deleted_groups.add((item.model_id, item.object_type))

        stale_index_metadata = 0
        index_deleted = 0
        last_index_rowid: int | None = None
        while rows := _index_audit_page(connection, last_index_rowid):
            last_index_rowid = int(rows[-1]["audit_rowid"])
            for row in rows:
                if not _index_metadata_is_stale(row):
                    continue
                stale_index_metadata += 1
                if not dry_run:
                    cursor = connection.execute(
                        "DELETE FROM vector_indexes WHERE id = ?",
                        (str(row["id"]),),
                    )
                    index_deleted += max(0, cursor.rowcount)

        if not dry_run:
            for model_id, object_type in sorted(deleted_groups):
                cursor = connection.execute(
                    """
                    DELETE FROM vector_indexes
                    WHERE model_id = ? AND object_type = ?
                    """,
                    (model_id, object_type),
                )
                index_deleted += max(0, cursor.rowcount)
            connection.commit()
        elif connection.in_transaction:
            connection.rollback()

        return VectorMaintenanceReport(
            dry_run=dry_run,
            vectors_scanned=vectors_scanned,
            stale_vectors=stale_vectors,
            vectors_deleted=deleted,
            stale_index_metadata=stale_index_metadata,
            index_metadata_deleted=index_deleted,
            reasons=dict(sorted(reason_counts.items())),
        )
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def _classify_vector(row: sqlite3.Row) -> _InvalidVector | None:
    reasons: list[str] = []
    object_type = str(row["object_type"])
    if row["model_exists"] is None:
        reasons.append("missing_model")
    if object_type == "document":
        if row["document_exists"] is None:
            reasons.append("missing_target")
        else:
            if str(row["document_status"]) != "active":
                reasons.append("inactive_target")
            source_hash = str(row["source_content_sha256"])
            current_hash = str(row["document_sha256"])
            if not _is_sha256(source_hash) or not _is_sha256(current_hash):
                reasons.append("unknown_source_hash")
            elif source_hash != current_hash:
                reasons.append("source_hash_mismatch")
    elif object_type == "chunk":
        if row["chunk_exists"] is None:
            reasons.append("missing_target")
        else:
            if str(row["chunk_document_status"]) != "active":
                reasons.append("inactive_target")
            source_hash = str(row["source_content_sha256"])
            current_hash = str(row["chunk_text_sha256"])
            if not _is_sha256(source_hash) or not _is_sha256(current_hash):
                reasons.append("unknown_source_hash")
            elif source_hash != current_hash:
                reasons.append("source_hash_mismatch")
    else:
        reasons.append("unknown_object_type")

    vector_dimension_type = str(row["vector_dimension_type"])
    model_dimension_type = str(row["model_dimension_type"] or "")
    try:
        dimension = int(row["dimension"])
    except (TypeError, ValueError):
        dimension = -1
    try:
        expected_dimension = int(row["model_dimension"])
    except (TypeError, ValueError):
        expected_dimension = -1

    vector_fingerprint = str(row["vector_model_fingerprint"])
    model_fingerprint = str(row["registered_model_fingerprint"] or "")
    fingerprint_algorithm = str(row["fingerprint_algorithm"] or "")
    if (
        not _is_sha256(vector_fingerprint)
        or not _is_sha256(model_fingerprint)
        or fingerprint_algorithm in {"", LEGACY_UNKNOWN_PROVENANCE}
    ):
        reasons.append("unknown_model_fingerprint")
    elif vector_fingerprint != model_fingerprint:
        reasons.append("model_fingerprint_mismatch")
    try:
        model_config = load_json_object(row["registered_model_config_json"])
    except StoredJSONError:
        reasons.append("invalid_model_config")
    else:
        if (
            model_config.get("model_fingerprint") != model_fingerprint
            or model_config.get("fingerprint_algorithm") != fingerprint_algorithm
            or json.dumps(model_config, sort_keys=True)
            != str(row["registered_model_config_json"])
        ):
            reasons.append("model_identity_config_mismatch")
        else:
            try:
                expected_model_id = stable_embedding_model_id(
                    provider=str(row["registered_model_provider"]),
                    name=str(row["registered_model_name"]),
                    dimension=expected_dimension,
                    distance=str(row["registered_model_distance"]),
                    config=model_config,
                    model_fingerprint=model_fingerprint,
                    fingerprint_algorithm=fingerprint_algorithm,
                )
            except (TypeError, ValueError):
                reasons.append("invalid_model_config")
            else:
                if expected_model_id != str(row["model_id"]):
                    reasons.append("model_identity_config_mismatch")

    algorithm_version = str(row["algorithm_version"])
    if algorithm_version == LEGACY_UNKNOWN_PROVENANCE:
        reasons.append("unknown_algorithm_version")
    elif object_type in {
        "document",
        "chunk",
    } and algorithm_version != vector_algorithm_version(object_type):
        reasons.append("algorithm_version_mismatch")

    if not _is_sha256(str(row["text_sha256"])):
        reasons.append("unknown_embedding_input_hash")

    if (
        vector_dimension_type != "integer"
        or model_dimension_type != "integer"
        or dimension <= 0
        or expected_dimension <= 0
        or dimension != expected_dimension
    ):
        reasons.append("dimension_mismatch")
    dtype = str(row["dtype"])
    if dtype != FLOAT32_DTYPE:
        reasons.append("dtype_mismatch")
    if str(row["vector_storage_type"]) != "blob":
        reasons.append("blob_type_mismatch")
        blob = b""
    else:
        blob = bytes(row["vector"])
    if str(row["vector_storage_type"]) == "blob" and (
        dimension <= 0 or len(blob) != dimension * FLOAT32_BYTES
    ):
        reasons.append("blob_size_mismatch")
    elif blob and dtype == FLOAT32_DTYPE:
        if any(not math.isfinite(value) for (value,) in struct.iter_unpack("<f", blob)):
            reasons.append("nonfinite_vector")

    if not reasons:
        return None
    return _InvalidVector(
        vector_id=str(row["id"]),
        model_id=str(row["model_id"]),
        object_type=object_type,
        updated_at=str(row["updated_at"]),
        reasons=tuple(dict.fromkeys(reasons)),
    )


def _index_metadata_is_stale(row: sqlite3.Row) -> bool:
    model_fingerprint = str(row["model_fingerprint"])
    registered_fingerprint = str(row["registered_model_fingerprint"] or "")
    object_type = str(row["object_type"])
    algorithm_version = str(row["algorithm_version"])
    try:
        model_config = load_json_object(row["registered_model_config_json"])
    except StoredJSONError:
        return True
    try:
        registered_dimension = int(row["registered_model_dimension"])
        expected_model_id = stable_embedding_model_id(
            provider=str(row["registered_model_provider"]),
            name=str(row["registered_model_name"]),
            dimension=registered_dimension,
            distance=str(row["registered_model_distance"]),
            config=model_config,
            model_fingerprint=registered_fingerprint,
            fingerprint_algorithm=str(row["fingerprint_algorithm"] or ""),
        )
    except (TypeError, ValueError):
        return True
    return (
        row["model_exists"] is None
        or object_type not in {"document", "chunk"}
        or not _is_sha256(model_fingerprint)
        or not _is_sha256(registered_fingerprint)
        or model_fingerprint != registered_fingerprint
        or str(row["registered_model_dimension_type"]) != "integer"
        or registered_dimension <= 0
        or json.dumps(model_config, sort_keys=True)
        != str(row["registered_model_config_json"])
        or model_config.get("model_fingerprint") != registered_fingerprint
        or model_config.get("fingerprint_algorithm")
        != str(row["fingerprint_algorithm"] or "")
        or expected_model_id != str(row["model_id"])
        or (
            object_type in {"document", "chunk"}
            and algorithm_version != vector_algorithm_version(object_type)
        )
        or not _is_sha256(str(row["vector_set_sha256"]))
    )


def _is_sha256(value: str) -> bool:
    return (
        len(value) == 64
        and value == value.lower()
        and all(character in "0123456789abcdef" for character in value)
    )


_AUDIT_BATCH_SIZE = 256

_VECTOR_AUDIT_QUERY = """
    SELECT
      v.rowid AS audit_rowid,
      v.*,
      typeof(v.vector) AS vector_storage_type,
      typeof(v.dimension) AS vector_dimension_type,
      m.id AS model_exists,
      m.name AS registered_model_name,
      m.provider AS registered_model_provider,
      m.dimension AS model_dimension,
      m.distance AS registered_model_distance,
      typeof(m.dimension) AS model_dimension_type,
      m.model_fingerprint AS registered_model_fingerprint,
      m.fingerprint_algorithm AS fingerprint_algorithm,
      m.config_json AS registered_model_config_json,
      d.id AS document_exists,
      d.status AS document_status,
      d.content_revision_sha256 AS document_sha256,
      c.id AS chunk_exists,
      c.text_sha256 AS chunk_text_sha256,
      cd.status AS chunk_document_status,
      v.model_fingerprint AS vector_model_fingerprint
    FROM vectors v
    LEFT JOIN embedding_models m ON m.id = v.model_id
    LEFT JOIN documents d
      ON v.object_type = 'document' AND d.id = v.object_id
    LEFT JOIN chunks c
      ON v.object_type = 'chunk' AND c.id = v.object_id
    LEFT JOIN documents cd ON cd.id = c.document_id
    {where_clause}
    ORDER BY v.rowid
    LIMIT ?
"""


_INDEX_AUDIT_QUERY = """
    SELECT
      vi.rowid AS audit_rowid,
      vi.*,
      m.id AS model_exists,
      m.name AS registered_model_name,
      m.provider AS registered_model_provider,
      m.dimension AS registered_model_dimension,
      typeof(m.dimension) AS registered_model_dimension_type,
      m.distance AS registered_model_distance,
      m.model_fingerprint AS registered_model_fingerprint,
      m.fingerprint_algorithm AS fingerprint_algorithm,
      m.config_json AS registered_model_config_json
    FROM vector_indexes vi
    LEFT JOIN embedding_models m ON m.id = vi.model_id
    {where_clause}
    ORDER BY vi.rowid
    LIMIT ?
"""


def _vector_audit_page(
    connection: sqlite3.Connection,
    last_rowid: int | None,
) -> list[sqlite3.Row]:
    where_clause = "" if last_rowid is None else "WHERE v.rowid > ?"
    params = (
        (_AUDIT_BATCH_SIZE,) if last_rowid is None else (last_rowid, _AUDIT_BATCH_SIZE)
    )
    return connection.execute(
        _VECTOR_AUDIT_QUERY.format(where_clause=where_clause),
        params,
    ).fetchall()


def _index_audit_page(
    connection: sqlite3.Connection,
    last_rowid: int | None,
) -> list[sqlite3.Row]:
    where_clause = "" if last_rowid is None else "WHERE vi.rowid > ?"
    params = (
        (_AUDIT_BATCH_SIZE,) if last_rowid is None else (last_rowid, _AUDIT_BATCH_SIZE)
    )
    return connection.execute(
        _INDEX_AUDIT_QUERY.format(where_clause=where_clause),
        params,
    ).fetchall()
