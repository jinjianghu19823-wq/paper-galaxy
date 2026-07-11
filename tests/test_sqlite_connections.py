from __future__ import annotations

import hashlib
import shutil
import sqlite3
import stat
from collections.abc import Callable
from pathlib import Path

import pytest

from paper_galaxy import errors
from paper_galaxy.storage import sqlite as sqlite_storage
from paper_galaxy.storage.migrations import (
    CURRENT_SCHEMA_VERSION,
    initialize_database,
)


def _database_bytes_sha256(database_path: Path) -> str:
    return hashlib.sha256(database_path.read_bytes()).hexdigest()


def _bootstrap_database(project_dir: Path) -> Path:
    database_path = sqlite_storage.resolve_database_path(project_dir)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    try:
        initialize_database(connection)
        connection.commit()
    finally:
        connection.close()
    return database_path


def _replace_fts_with_plain_table(database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("DROP TABLE documents_fts")
        connection.execute(
            """
            CREATE TABLE documents_fts (
              document_id TEXT,
              title TEXT,
              relative_path TEXT,
              text TEXT
            )
            """
        )
        connection.commit()
    finally:
        connection.close()


def _replace_document_texts_without_constraints(database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("DROP TABLE document_texts")
        connection.execute(
            """
            CREATE TABLE document_texts (
              document_id TEXT,
              text TEXT
            )
            """
        )
        connection.commit()
    finally:
        connection.close()


def _replace_vectors_without_identity_unique_key(database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("DROP TABLE vectors")
        connection.execute(
            """
            CREATE TABLE vectors (
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
              FOREIGN KEY(model_id) REFERENCES embedding_models(id)
            )
            """
        )
        connection.execute(
            "CREATE INDEX idx_vectors_model_object_type "
            "ON vectors(model_id, object_type)"
        )
        connection.execute(
            "CREATE INDEX idx_vectors_object ON vectors(object_type, object_id)"
        )
        connection.execute(
            "CREATE INDEX idx_vectors_text_sha256 ON vectors(text_sha256)"
        )
        connection.commit()
    finally:
        connection.close()


def _schema_snapshot(connection: sqlite3.Connection) -> list[tuple[object, ...]]:
    rows = connection.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM sqlite_schema
        WHERE name NOT LIKE 'sqlite_%'
        ORDER BY type, name
        """
    ).fetchall()
    return [tuple(row) for row in rows]


def _assert_structured_database_error(
    error: BaseException,
    *,
    code: str,
    database_path: Path,
) -> None:
    assert error.code == code  # type: ignore[attr-defined]
    assert Path(error.database_path) == database_path  # type: ignore[attr-defined]
    safe_message = error.safe_message  # type: ignore[attr-defined]
    assert isinstance(safe_message, str)
    assert safe_message.strip()
    assert str(database_path) not in safe_message
    assert str(database_path.parent) not in safe_message


@pytest.mark.parametrize("connector_name", ["connect_read_only", "connect_read_write"])
def test_non_migration_connections_do_not_create_a_missing_database(
    tmp_path: Path,
    connector_name: str,
) -> None:
    project_dir = tmp_path / "missing-project"
    database_path = sqlite_storage.resolve_database_path(project_dir)
    connector = getattr(sqlite_storage, connector_name)

    with pytest.raises(errors.DatabaseNotFoundError) as caught:
        connector(project_dir)

    _assert_structured_database_error(
        caught.value,
        code="database_missing",
        database_path=database_path,
    )
    assert not project_dir.exists()
    assert not database_path.exists()


def test_migration_connection_is_the_only_connection_that_can_create_database(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "new-project"
    database_path = sqlite_storage.resolve_database_path(project_dir)

    connection = sqlite_storage.connect_migration(project_dir)
    try:
        assert database_path.exists()
        initialize_database(connection)
    finally:
        connection.close()

    assert database_path.is_file()
    assert stat.S_IMODE(database_path.stat().st_mode) & 0o077 == 0


def test_read_only_connection_cannot_write_or_mutate_database(
    tmp_path: Path,
) -> None:
    database_path = _bootstrap_database(tmp_path)
    before_hash = _database_bytes_sha256(database_path)
    before_mtime_ns = database_path.stat().st_mtime_ns
    fixture_connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        before_schema = _schema_snapshot(fixture_connection)
    finally:
        fixture_connection.close()

    connection = sqlite_storage.connect_read_only(tmp_path)
    try:
        assert _schema_snapshot(connection) == before_schema
        with pytest.raises(sqlite3.OperationalError, match=r"readonly|read-only"):
            connection.execute(
                "INSERT INTO schema_meta(key, value) VALUES ('write_probe', 'no')"
            )
    finally:
        connection.close()

    assert _database_bytes_sha256(database_path) == before_hash
    assert database_path.stat().st_mtime_ns == before_mtime_ns
    verification = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        assert _schema_snapshot(verification) == before_schema
        assert (
            verification.execute(
                "SELECT value FROM schema_meta WHERE key = 'write_probe'"
            ).fetchone()
            is None
        )
    finally:
        verification.close()


def test_read_write_connection_configures_reliable_writer_pragmas(
    tmp_path: Path,
) -> None:
    database_path = _bootstrap_database(tmp_path)

    connection = sqlite_storage.connect_read_write(tmp_path)
    try:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] >= 1_000
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
    finally:
        connection.close()
    assert not Path(f"{database_path}-wal").exists()
    assert not Path(f"{database_path}-shm").exists()


@pytest.mark.parametrize("connector_name", ["connect_read_only", "connect_read_write"])
def test_future_schema_is_rejected_without_being_rewritten(
    tmp_path: Path,
    connector_name: str,
) -> None:
    database_path = sqlite_storage.resolve_database_path(tmp_path)
    database_path.parent.mkdir(parents=True)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('schema_version', '999999')"
        )
        connection.commit()
    finally:
        connection.close()
    before_hash = _database_bytes_sha256(database_path)
    connector: Callable[[Path], sqlite3.Connection] = getattr(
        sqlite_storage, connector_name
    )
    expected_error = errors.FutureSchemaError

    with pytest.raises(expected_error) as caught:
        connector(tmp_path)

    _assert_structured_database_error(
        caught.value,
        code="future_schema",
        database_path=database_path,
    )
    assert caught.value.found_version == 999999
    assert _database_bytes_sha256(database_path) == before_hash


def test_corrupt_database_returns_structured_error_without_leaking_path(
    tmp_path: Path,
) -> None:
    database_path = sqlite_storage.resolve_database_path(tmp_path)
    database_path.parent.mkdir(parents=True)
    original_bytes = b"this is not a sqlite database\x00private-local-data"
    database_path.write_bytes(original_bytes)
    expected_error = errors.DatabaseCorruptError

    with pytest.raises(expected_error) as caught:
        sqlite_storage.connect_read_only(tmp_path)

    _assert_structured_database_error(
        caught.value,
        code="database_corrupt",
        database_path=database_path,
    )
    assert database_path.read_bytes() == original_bytes


def test_read_only_refuses_incomplete_wal_without_creating_shm(tmp_path: Path) -> None:
    source_project = tmp_path / "source"
    source_database = _bootstrap_database(source_project)
    writer = sqlite3.connect(source_database)
    try:
        assert writer.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint = 0")
        writer.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('wal_marker', 'present')"
        )
        writer.commit()
        source_wal = Path(f"{source_database}-wal")
        assert source_wal.stat().st_size > 0

        snapshot_project = tmp_path / "snapshot"
        snapshot_database = sqlite_storage.resolve_database_path(snapshot_project)
        snapshot_database.parent.mkdir(parents=True)
        shutil.copy2(source_database, snapshot_database)
        shutil.copy2(source_wal, Path(f"{snapshot_database}-wal"))
        names_before = sorted(path.name for path in snapshot_database.parent.iterdir())

        with pytest.raises(errors.DatabaseLockedError) as caught:
            sqlite_storage.connect_read_only(snapshot_project)

        _assert_structured_database_error(
            caught.value,
            code="database_locked",
            database_path=snapshot_database,
        )
        names_after = sorted(path.name for path in snapshot_database.parent.iterdir())
        assert names_after == names_before
        assert not Path(f"{snapshot_database}-shm").exists()
    finally:
        writer.close()


def test_read_only_refuses_empty_wal_without_creating_shm(tmp_path: Path) -> None:
    database_path = _bootstrap_database(tmp_path)
    wal_path = Path(f"{database_path}-wal")
    shm_path = Path(f"{database_path}-shm")
    wal_path.touch()
    names_before = sorted(path.name for path in database_path.parent.iterdir())

    with pytest.raises(errors.DatabaseLockedError):
        sqlite_storage.connect_read_only(tmp_path)

    assert sorted(path.name for path in database_path.parent.iterdir()) == names_before
    assert wal_path.exists() and wal_path.stat().st_size == 0
    assert not shm_path.exists()


def test_clean_wal_database_requires_writer_normalization_without_side_effects(
    tmp_path: Path,
) -> None:
    database_path = _bootstrap_database(tmp_path)
    writer = sqlite3.connect(database_path)
    try:
        assert writer.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
        writer.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('clean_wal', 'present')"
        )
        writer.commit()
    finally:
        writer.close()
    assert not Path(f"{database_path}-wal").exists()
    assert not Path(f"{database_path}-shm").exists()
    before_names = sorted(path.name for path in database_path.parent.iterdir())
    before_bytes = database_path.read_bytes()

    with pytest.raises(errors.DatabaseNeedsWriterNormalizationError):
        sqlite_storage.connect_read_only(tmp_path)

    assert sorted(path.name for path in database_path.parent.iterdir()) == before_names
    assert database_path.read_bytes() == before_bytes

    writer = sqlite_storage.connect_read_write(tmp_path)
    writer.close()
    assert not Path(f"{database_path}-wal").exists()
    assert not Path(f"{database_path}-shm").exists()
    reader = sqlite_storage.connect_read_only(tmp_path)
    try:
        row = reader.execute(
            "SELECT value FROM schema_meta WHERE key = 'clean_wal'"
        ).fetchone()
        assert row is not None and row[0] == "present"
    finally:
        reader.close()


def test_read_only_active_wal_sees_committed_data_without_new_entries(
    tmp_path: Path,
) -> None:
    database_path = _bootstrap_database(tmp_path)
    writer = sqlite3.connect(database_path)
    try:
        assert writer.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint = 0")
        writer.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('active_wal', 'visible')"
        )
        writer.commit()
        wal_path = Path(f"{database_path}-wal")
        shm_path = Path(f"{database_path}-shm")
        assert wal_path.is_file() and shm_path.is_file()
        before_names = sorted(path.name for path in database_path.parent.iterdir())
        before_database = database_path.read_bytes()
        before_wal = wal_path.read_bytes()

        reader = sqlite_storage.connect_read_only(tmp_path)
        try:
            row = reader.execute(
                "SELECT value FROM schema_meta WHERE key = 'active_wal'"
            ).fetchone()
            assert row is not None and row[0] == "visible"
        finally:
            reader.close()

        assert (
            sorted(path.name for path in database_path.parent.iterdir()) == before_names
        )
        assert database_path.read_bytes() == before_database
        assert wal_path.read_bytes() == before_wal
    finally:
        writer.close()


def test_external_clean_wal_is_refused_without_touching_zotero_fixture(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "zotero.sqlite"
    writer = sqlite3.connect(database_path)
    try:
        writer.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, title TEXT)")
        assert writer.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
        writer.execute("INSERT INTO items(title) VALUES ('synthetic')")
        writer.commit()
    finally:
        writer.close()
    before_names = sorted(path.name for path in tmp_path.iterdir())
    before_bytes = database_path.read_bytes()

    with pytest.raises(errors.DatabaseNeedsWriterNormalizationError) as caught:
        sqlite_storage.connect_external_read_only(database_path)

    assert "Open Zotero Desktop" in caught.value.safe_message
    assert "will not modify" in caught.value.safe_message
    assert str(database_path) not in caught.value.safe_message
    assert sorted(path.name for path in tmp_path.iterdir()) == before_names
    assert database_path.read_bytes() == before_bytes


def test_migration_connection_refuses_future_schema_before_returning(
    tmp_path: Path,
) -> None:
    database_path = sqlite_storage.resolve_database_path(tmp_path)
    database_path.parent.mkdir(parents=True)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('schema_version', '999999')"
        )
        connection.commit()
    finally:
        connection.close()
    before_hash = _database_bytes_sha256(database_path)

    with pytest.raises(errors.FutureSchemaError):
        sqlite_storage.connect_migration(tmp_path)

    assert _database_bytes_sha256(database_path) == before_hash


def test_migration_connection_refuses_unsupported_old_schema_before_returning(
    tmp_path: Path,
) -> None:
    database_path = sqlite_storage.resolve_database_path(tmp_path)
    database_path.parent.mkdir(parents=True)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('schema_version', '1')"
        )
        connection.commit()
    finally:
        connection.close()
    before_hash = _database_bytes_sha256(database_path)

    with pytest.raises(errors.UnsupportedSchemaError):
        sqlite_storage.connect_migration(tmp_path)

    assert _database_bytes_sha256(database_path) == before_hash


@pytest.mark.parametrize(
    "connector_name",
    ["connect_read_only", "connect_read_write", "connect_migration"],
)
def test_connectors_reject_version_only_partial_current_schema_without_changes(
    tmp_path: Path,
    connector_name: str,
) -> None:
    database_path = sqlite_storage.resolve_database_path(tmp_path)
    database_path.parent.mkdir(parents=True)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('schema_version', ?)",
            (str(CURRENT_SCHEMA_VERSION),),
        )
        connection.commit()
    finally:
        connection.close()
    before_hash = _database_bytes_sha256(database_path)
    connector: Callable[[Path], sqlite3.Connection] = getattr(
        sqlite_storage, connector_name
    )

    with pytest.raises(
        errors.UnsupportedSchemaError, match="schema identity"
    ) as caught:
        connector(tmp_path)

    _assert_structured_database_error(
        caught.value,
        code="unsupported_schema",
        database_path=database_path,
    )
    assert _database_bytes_sha256(database_path) == before_hash
    assert not Path(f"{database_path}-wal").exists()
    assert not Path(f"{database_path}-shm").exists()


def test_diagnostic_read_only_can_inspect_partial_current_schema_without_writes(
    tmp_path: Path,
) -> None:
    database_path = sqlite_storage.resolve_database_path(tmp_path)
    database_path.parent.mkdir(parents=True)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('schema_version', ?)",
            (str(CURRENT_SCHEMA_VERSION),),
        )
        connection.commit()
    finally:
        connection.close()
    before_hash = _database_bytes_sha256(database_path)

    diagnostic = sqlite_storage.connect_diagnostic_read_only(tmp_path)
    try:
        assert diagnostic.execute("PRAGMA query_only").fetchone()[0] == 1
        assert diagnostic.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0] == str(CURRENT_SCHEMA_VERSION)
        with pytest.raises(sqlite3.OperationalError, match=r"readonly|read-only"):
            diagnostic.execute("CREATE TABLE forbidden_write (id INTEGER)")
    finally:
        diagnostic.close()

    assert _database_bytes_sha256(database_path) == before_hash


@pytest.mark.parametrize(
    "connector_name",
    ["connect_read_only", "connect_read_write", "connect_migration"],
)
def test_connectors_reject_plain_table_impersonating_current_fts(
    tmp_path: Path,
    connector_name: str,
) -> None:
    database_path = _bootstrap_database(tmp_path)
    _replace_fts_with_plain_table(database_path)
    before_hash = _database_bytes_sha256(database_path)
    connector: Callable[[Path], sqlite3.Connection] = getattr(
        sqlite_storage, connector_name
    )

    with pytest.raises(
        errors.UnsupportedSchemaError,
        match="not an FTS5 virtual table",
    ) as caught:
        connector(tmp_path)

    _assert_structured_database_error(
        caught.value,
        code="unsupported_schema",
        database_path=database_path,
    )
    assert _database_bytes_sha256(database_path) == before_hash
    assert not Path(f"{database_path}-wal").exists()
    assert not Path(f"{database_path}-shm").exists()


@pytest.mark.parametrize(
    "connector_name",
    ["connect_read_only", "connect_read_write", "connect_migration"],
)
def test_connectors_reject_same_columns_without_current_pk_or_fk(
    tmp_path: Path,
    connector_name: str,
) -> None:
    database_path = _bootstrap_database(tmp_path)
    _replace_document_texts_without_constraints(database_path)
    before_hash = _database_bytes_sha256(database_path)
    connector: Callable[[Path], sqlite3.Connection] = getattr(
        sqlite_storage, connector_name
    )

    with pytest.raises(errors.UnsupportedSchemaError, match="primary key") as caught:
        connector(tmp_path)

    _assert_structured_database_error(
        caught.value,
        code="unsupported_schema",
        database_path=database_path,
    )
    assert _database_bytes_sha256(database_path) == before_hash
    assert not Path(f"{database_path}-wal").exists()
    assert not Path(f"{database_path}-shm").exists()


def test_read_only_rejects_vector_table_without_identity_unique_key(
    tmp_path: Path,
) -> None:
    database_path = _bootstrap_database(tmp_path)
    _replace_vectors_without_identity_unique_key(database_path)
    before_hash = _database_bytes_sha256(database_path)

    with pytest.raises(errors.UnsupportedSchemaError, match="unique key") as caught:
        sqlite_storage.connect_read_only(tmp_path)

    _assert_structured_database_error(
        caught.value,
        code="unsupported_schema",
        database_path=database_path,
    )
    assert _database_bytes_sha256(database_path) == before_hash
