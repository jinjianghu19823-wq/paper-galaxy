"""Regression tests for the versioned SQLite migration lifecycle."""

from __future__ import annotations

import hashlib
import sqlite3
import stat
import threading
from collections.abc import Mapping
from pathlib import Path

import pytest

from paper_galaxy.errors import UnsupportedSchemaError
from paper_galaxy.storage import migrations

V6_SCHEMA_FIXTURE = Path(__file__).parent / "fixtures" / "storage" / "schema_v6.sql"
V6_SCHEMA_SHA256 = "eaab6c5bf9bfd1d60c6d2164ff3ebeed57b6c64ae17535d677f048664ee90326"
V6_SCHEMA_SOURCE_COMMIT = "c3be2cec83d57cba8ee8badac003fc6b8870c8ac"


def _current_schema_version() -> int:
    return int(migrations.CURRENT_SCHEMA_VERSION)


def _schema_version(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
    ).fetchone()
    assert row is not None
    return int(row[0])


def _create_v6_database(
    database_path: Path, *, wal: bool = False
) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    if wal:
        journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()
        assert journal_mode is not None
        assert str(journal_mode[0]).lower() == "wal"
        connection.execute("PRAGMA wal_autocheckpoint = 0")

    connection.executescript(V6_SCHEMA_FIXTURE.read_text(encoding="utf-8"))
    connection.execute(
        "INSERT INTO schema_meta(key, value) VALUES ('schema_version', '6')"
    )
    connection.execute(
        """
        INSERT INTO corpora(id, root_path, created_at, updated_at)
        VALUES ('historical-corpus', '/synthetic-corpus', '2026-01-01', '2026-01-01')
        """
    )
    connection.execute(
        """
        INSERT INTO documents(
          id, corpus_id, path, relative_path, file_type, title, sha256,
          size_bytes, mtime_ns, char_count, status, first_seen_at,
          last_seen_at, updated_at
        )
        VALUES (
          'historical-document', 'historical-corpus',
          '/synthetic-corpus/paper.md', 'paper.md', '.md',
          'Historical v6 paper', 'historical-content-hash', 123, 456, 27,
          'active', '2026-01-01', '2026-01-01', '2026-01-01'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO document_texts(document_id, text)
        VALUES ('historical-document', 'preserve this historical text')
        """
    )
    connection.execute(
        """
        INSERT INTO chunks(id, document_id, chunk_index, text, char_count)
        VALUES (
          'historical-chunk', 'historical-document', 0,
          'preserve this historical text', 29
        )
        """
    )
    connection.execute(
        """
        INSERT INTO documents_fts(document_id, title, relative_path, text)
        VALUES (
          'historical-document', 'Historical v6 paper', 'paper.md',
          'preserve this historical text'
        )
        """
    )
    connection.commit()
    return connection


def _assert_historical_rows_preserved(connection: sqlite3.Connection) -> None:
    document = connection.execute(
        """
        SELECT title, sha256, status
        FROM documents
        WHERE id = 'historical-document'
        """
    ).fetchone()
    assert document == ("Historical v6 paper", "historical-content-hash", "active")
    text = connection.execute(
        "SELECT text FROM document_texts WHERE document_id = 'historical-document'"
    ).fetchone()
    assert text == ("preserve this historical text",)
    chunk = connection.execute(
        "SELECT id, chunk_index FROM chunks WHERE document_id = 'historical-document'"
    ).fetchone()
    assert chunk == ("historical-chunk", 0)
    fts = connection.execute(
        "SELECT document_id FROM documents_fts WHERE documents_fts MATCH 'historical'"
    ).fetchall()
    assert fts == [("historical-document",)]


def _replace_fts_with_plain_table(connection: sqlite3.Connection) -> None:
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


def _replace_document_texts_without_constraints(
    connection: sqlite3.Connection,
) -> None:
    connection.execute("DROP TABLE document_texts")
    connection.execute(
        """
        CREATE TABLE document_texts (
          document_id TEXT,
          text TEXT
        )
        """
    )


def _replace_document_texts_without_foreign_key(
    connection: sqlite3.Connection,
) -> None:
    connection.execute("DROP TABLE document_texts")
    connection.execute(
        """
        CREATE TABLE document_texts (
          document_id TEXT PRIMARY KEY,
          text TEXT NOT NULL
        )
        """
    )


def test_v6_fixture_is_exact_historical_schema() -> None:
    """The migration source is real history, not a current-schema lookalike."""

    fixture = V6_SCHEMA_FIXTURE.read_bytes()

    assert V6_SCHEMA_SOURCE_COMMIT == "c3be2cec83d57cba8ee8badac003fc6b8870c8ac"
    assert hashlib.sha256(fixture).hexdigest() == V6_SCHEMA_SHA256
    assert len(fixture.splitlines()) == 421


def test_bootstrap_commits_current_schema_version_before_reopen(tmp_path: Path) -> None:
    database_path = tmp_path / "bootstrap.sqlite3"
    assert _current_schema_version() > 6

    connection = sqlite3.connect(database_path)
    migrations.initialize_database(connection)
    connection.close()

    reopened = sqlite3.connect(database_path)
    try:
        assert _schema_version(reopened) == _current_schema_version()
        assert reopened.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'documents'"
        ).fetchone() == (1,)
        assert reopened.execute("PRAGMA quick_check").fetchone() == ("ok",)
    finally:
        reopened.close()


def test_real_v6_database_migrates_without_losing_data(tmp_path: Path) -> None:
    database_path = tmp_path / "historical-v6.sqlite3"
    connection = _create_v6_database(database_path)
    connection.close()

    migrating = sqlite3.connect(database_path)
    migrations.initialize_database(
        migrating,
        backup_path=tmp_path / "historical-v6.pre-migration.sqlite3",
    )
    migrating.close()

    reopened = sqlite3.connect(database_path)
    try:
        assert _schema_version(reopened) == _current_schema_version()
        assert _current_schema_version() > 6
        _assert_historical_rows_preserved(reopened)
        assert reopened.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert reopened.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        reopened.close()


@pytest.mark.parametrize(
    ("damage_sql", "case_name"),
    [
        ("DROP TABLE chunks", "missing-table"),
        ("DROP INDEX idx_documents_sha256", "missing-index"),
    ],
)
def test_incomplete_v6_schema_is_rejected_before_backup_without_changes(
    tmp_path: Path,
    damage_sql: str,
    case_name: str,
) -> None:
    database_path = tmp_path / f"partial-v6-{case_name}.sqlite3"
    backup_path = tmp_path / f"partial-v6-{case_name}.backup.sqlite3"
    connection = _create_v6_database(database_path)
    connection.execute(damage_sql)
    connection.commit()
    connection.close()
    before = database_path.read_bytes()

    migrating = sqlite3.connect(database_path)
    try:
        with pytest.raises(UnsupportedSchemaError, match="schema identity"):
            migrations.initialize_database(migrating, backup_path=backup_path)
    finally:
        migrating.close()

    assert database_path.read_bytes() == before
    assert not backup_path.exists()
    assert not list(tmp_path.glob("*.staging"))


def test_v6_plain_table_cannot_impersonate_fts_before_migration(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "plain-fts-v6.sqlite3"
    backup_path = tmp_path / "plain-fts-v6.backup.sqlite3"
    connection = _create_v6_database(database_path)
    _replace_fts_with_plain_table(connection)
    connection.commit()
    connection.close()
    before = database_path.read_bytes()

    migrating = sqlite3.connect(database_path)
    try:
        with pytest.raises(UnsupportedSchemaError, match="not an FTS5 virtual table"):
            migrations.initialize_database(migrating, backup_path=backup_path)
    finally:
        migrating.close()

    assert database_path.read_bytes() == before
    assert not backup_path.exists()


def test_v6_same_column_table_without_pk_or_fk_is_rejected_before_backup(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "constraint-impostor-v6.sqlite3"
    backup_path = tmp_path / "constraint-impostor-v6.backup.sqlite3"
    connection = _create_v6_database(database_path)
    _replace_document_texts_without_constraints(connection)
    connection.commit()
    connection.close()
    before = database_path.read_bytes()

    migrating = sqlite3.connect(database_path)
    try:
        with pytest.raises(UnsupportedSchemaError, match="primary key"):
            migrations.initialize_database(migrating, backup_path=backup_path)
    finally:
        migrating.close()

    assert database_path.read_bytes() == before
    assert not backup_path.exists()


def test_v6_same_column_table_with_wrong_fk_is_rejected_before_backup(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "foreign-key-impostor-v6.sqlite3"
    backup_path = tmp_path / "foreign-key-impostor-v6.backup.sqlite3"
    connection = _create_v6_database(database_path)
    _replace_document_texts_without_foreign_key(connection)
    connection.commit()
    connection.close()
    before = database_path.read_bytes()

    migrating = sqlite3.connect(database_path)
    try:
        with pytest.raises(UnsupportedSchemaError, match="foreign key"):
            migrations.initialize_database(migrating, backup_path=backup_path)
    finally:
        migrating.close()

    assert database_path.read_bytes() == before
    assert not backup_path.exists()


def test_current_schema_early_return_rejects_plain_table_impersonating_fts(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "plain-fts-current.sqlite3"
    connection = sqlite3.connect(database_path)
    migrations.initialize_database(connection)
    _replace_fts_with_plain_table(connection)
    connection.commit()
    connection.close()
    before = database_path.read_bytes()

    current = sqlite3.connect(database_path)
    try:
        with pytest.raises(UnsupportedSchemaError, match="not an FTS5 virtual table"):
            migrations.initialize_database(current)
    finally:
        current.close()

    assert database_path.read_bytes() == before


def test_migration_registry_versions_and_names_are_unique_and_contiguous() -> None:
    current = _current_schema_version()
    registry = migrations.MIGRATIONS
    entries = (
        list(registry.values()) if isinstance(registry, Mapping) else list(registry)
    )
    versions = [int(entry.version) for entry in entries]
    names = [str(entry.name) for entry in entries]

    assert current > 6
    assert versions == list(range(7, current + 1))
    assert len(names) == len(set(names))
    assert all(name.strip() for name in names)
    assert all(callable(entry.up) for entry in entries)


def test_migration_failure_rolls_back_every_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "rollback.sqlite3"
    connection = _create_v6_database(database_path)
    connection.close()

    class FailingMigration:
        version = 7
        name = "synthetic_failure"

        @staticmethod
        def up(connection: sqlite3.Connection) -> None:
            connection.execute(
                "CREATE TABLE must_rollback (id INTEGER PRIMARY KEY, value TEXT)"
            )
            connection.execute(
                "INSERT INTO must_rollback(value) VALUES ('partial state')"
            )
            raise RuntimeError("synthetic migration failure")

    original_registry = migrations.MIGRATIONS
    replacement: object
    if isinstance(original_registry, Mapping):
        replacement = {7: FailingMigration()}
    else:
        replacement = (FailingMigration(),)
    monkeypatch.setattr(migrations, "MIGRATIONS", replacement)
    monkeypatch.setattr(migrations, "CURRENT_SCHEMA_VERSION", 7)

    migrating = sqlite3.connect(database_path)
    with pytest.raises(RuntimeError, match="synthetic migration failure"):
        migrations.initialize_database(
            migrating,
            backup_path=tmp_path / "rollback.pre-migration.sqlite3",
        )
    assert migrating.in_transaction is False
    migrating.close()

    reopened = sqlite3.connect(database_path)
    try:
        assert _schema_version(reopened) == 6
        assert (
            reopened.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'table' AND name = 'must_rollback'
                """
            ).fetchone()
            is None
        )
        _assert_historical_rows_preserved(reopened)
    finally:
        reopened.close()


def test_future_schema_is_rejected_without_downgrade(tmp_path: Path) -> None:
    database_path = tmp_path / "future.sqlite3"
    connection = _create_v6_database(database_path)
    future_version = _current_schema_version() + 1
    connection.execute(
        "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
        (str(future_version),),
    )
    connection.commit()
    connection.close()

    future = sqlite3.connect(database_path)
    try:
        with pytest.raises(RuntimeError, match=r"(?i)(future|newer|schema version)"):
            migrations.initialize_database(future)
    finally:
        future.close()

    reopened = sqlite3.connect(database_path)
    try:
        assert _schema_version(reopened) == future_version
        _assert_historical_rows_preserved(reopened)
    finally:
        reopened.close()


def test_repeated_initialization_is_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "idempotent.sqlite3"
    connection = sqlite3.connect(database_path)
    migrations.initialize_database(connection)
    connection.execute(
        """
        INSERT INTO corpora(id, root_path, created_at, updated_at)
        VALUES ('do-not-reset', '/synthetic', '2026-01-01', '2026-01-01')
        """
    )
    connection.commit()
    schema_cookie_before = connection.execute("PRAGMA schema_version").fetchone()

    migrations.initialize_database(connection)
    migrations.initialize_database(connection)

    assert _schema_version(connection) == _current_schema_version()
    assert connection.execute(
        "SELECT root_path FROM corpora WHERE id = 'do-not-reset'"
    ).fetchall() == [("/synthetic",)]
    assert connection.execute(
        "SELECT COUNT(*) FROM schema_meta WHERE key = 'schema_version'"
    ).fetchone() == (1,)
    assert (
        connection.execute("PRAGMA schema_version").fetchone() == schema_cookie_before
    )
    connection.close()


def test_pre_migration_backup_is_consistent_with_active_wal(tmp_path: Path) -> None:
    database_path = tmp_path / "wal-v6.sqlite3"
    backup_path = tmp_path / "wal-v6.pre-migration.sqlite3"
    connection = _create_v6_database(database_path, wal=True)
    wal_path = Path(f"{database_path}-wal")
    assert wal_path.exists()
    assert wal_path.stat().st_size > 0

    migrations.initialize_database(connection, backup_path=backup_path)

    assert backup_path.is_file()
    assert stat.S_IMODE(backup_path.stat().st_mode) == 0o600
    backup = sqlite3.connect(backup_path)
    try:
        assert backup.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert backup.execute("PRAGMA foreign_key_check").fetchall() == []
        assert _schema_version(backup) == 6
        _assert_historical_rows_preserved(backup)
    finally:
        backup.close()

    assert _schema_version(connection) == _current_schema_version()
    connection.close()


def test_migration_backup_refuses_symlink_without_touching_target(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "symlink-v6.sqlite3"
    connection = _create_v6_database(database_path)
    connection.close()
    sentinel = tmp_path / "user-sentinel.txt"
    original = b"private user bytes must survive"
    sentinel.write_bytes(original)
    backup_path = tmp_path / "migration-backup.sqlite3"
    backup_path.symlink_to(sentinel)

    migrating = sqlite3.connect(database_path)
    try:
        with pytest.raises(FileExistsError, match="existing migration backup"):
            migrations.initialize_database(migrating, backup_path=backup_path)
    finally:
        migrating.close()

    assert sentinel.read_bytes() == original
    assert backup_path.is_symlink()
    reopened = sqlite3.connect(database_path)
    try:
        assert _schema_version(reopened) == 6
        _assert_historical_rows_preserved(reopened)
    finally:
        reopened.close()


def test_concurrent_initializers_serialize_without_partial_schema(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "concurrent.sqlite3"
    barrier = threading.Barrier(2)
    failures: list[BaseException] = []

    def initialize() -> None:
        connection = sqlite3.connect(database_path)
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            barrier.wait()
            migrations.initialize_database(connection)
        except BaseException as exc:
            failures.append(exc)
        finally:
            connection.close()

    workers = [threading.Thread(target=initialize) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    assert not any(worker.is_alive() for worker in workers)
    assert failures == []
    connection = sqlite3.connect(database_path)
    try:
        assert _schema_version(connection) == _current_schema_version()
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone() == (len(migrations.MIGRATIONS),)
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
    finally:
        connection.close()
