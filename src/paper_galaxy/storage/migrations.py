"""Transactional bootstrap and ordered migrations for project databases."""

from __future__ import annotations

import os
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from paper_galaxy.errors import (
    DatabaseCorruptError,
    DatabaseError,
    DatabaseLockedError,
    FTSUnavailableError,
    FutureSchemaError,
    UnsupportedSchemaError,
)

CURRENT_SCHEMA_VERSION = 7
SCHEMA_VERSION = str(CURRENT_SCHEMA_VERSION)
OLDEST_SUPPORTED_SCHEMA_VERSION = 6


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

_CURRENT_REQUIRED_COLUMNS = {
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

_V6_FORBIDDEN_COLUMNS: dict[str, frozenset[str]] = {
    "scan_runs": _columns("error_code error_message"),
    "embedding_runs": _columns("error_code error_message"),
    "zotero_import_runs": _columns("error_code error_message"),
    "zotero_items": _columns("child_manifest_json"),
}

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

_CURRENT_REQUIRED_PRIMARY_KEYS = {
    **_V6_REQUIRED_PRIMARY_KEYS,
    "schema_migrations": ("version",),
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

_CURRENT_REQUIRED_UNIQUE_KEYS = {
    **_V6_REQUIRED_UNIQUE_KEYS,
    "schema_migrations": (("name",),),
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


MIGRATIONS: tuple[Migration, ...] | dict[int, Migration] = (
    Migration(7, "record_migrations_and_run_failures", _migrate_v7),
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
    elif version == CURRENT_SCHEMA_VERSION:
        required_columns = _CURRENT_REQUIRED_COLUMNS
        required_primary_keys = _CURRENT_REQUIRED_PRIMARY_KEYS
        required_unique_keys = _CURRENT_REQUIRED_UNIQUE_KEYS
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
        for table_name, forbidden in sorted(_V6_FORBIDDEN_COLUMNS.items()):
            if table_name not in tables:
                continue
            present = sorted(forbidden & set(_table_columns(connection, table_name)))
            if present:
                problems.append(
                    f"v6 table {table_name} already contains migration columns "
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
    _check_required_indexes(connection, problems)
    _check_fts_shape(connection, problems)
    if version == CURRENT_SCHEMA_VERSION and "schema_migrations" in tables:
        _check_migration_history(connection, problems)

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
        _REQUIRED_INDEXES.items()
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
    problems: list[str],
) -> None:
    expected = {
        int(migration.version): str(migration.name)
        for migration in _migration_entries()
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
