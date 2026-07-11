"""Regression tests for the versioned SQLite migration lifecycle."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import stat
import threading
from collections.abc import Mapping
from pathlib import Path

import pytest

from paper_galaxy.errors import UnsupportedSchemaError
from paper_galaxy.storage import migrations
from paper_galaxy.storage.provenance import (
    document_content_revision_sha256,
    registered_source_identity,
)

V6_SCHEMA_FIXTURE = Path(__file__).parent / "fixtures" / "storage" / "schema_v6.sql"
V6_SCHEMA_SHA256 = "eaab6c5bf9bfd1d60c6d2164ff3ebeed57b6c64ae17535d677f048664ee90326"
V6_SCHEMA_SOURCE_COMMIT = "c3be2cec83d57cba8ee8badac003fc6b8870c8ac"
V9_SCHEMA_FIXTURE = Path(__file__).parent / "fixtures" / "storage" / "schema_v9.sql"
V9_SCHEMA_SHA256 = "79b89774e7054ea4fa117620e1c3a111880fac87afe744d0a243e45e9cde8440"
V9_SCHEMA_SOURCE_COMMIT = "2c2f98d69306519ba292442be6f2cc1fc8d92682"


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


def _create_v7_database(database_path: Path) -> sqlite3.Connection:
    connection = _create_v6_database(database_path)
    migration = next(entry for entry in migrations.MIGRATIONS if entry.version == 7)
    migration.up(connection)
    connection.execute(
        "INSERT INTO schema_migrations(version, name) VALUES (?, ?)",
        (migration.version, migration.name),
    )
    connection.execute(
        "UPDATE schema_meta SET value = '7' WHERE key = 'schema_version'"
    )
    connection.commit()
    return connection


def _create_v8_database(database_path: Path) -> sqlite3.Connection:
    connection = _create_v7_database(database_path)
    migration = next(entry for entry in migrations.MIGRATIONS if entry.version == 8)
    migration.up(connection)
    connection.execute(
        "INSERT INTO schema_migrations(version, name) VALUES (?, ?)",
        (migration.version, migration.name),
    )
    connection.execute(
        "UPDATE schema_meta SET value = '8' WHERE key = 'schema_version'"
    )
    connection.commit()
    return connection


def _create_v9_database(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(V9_SCHEMA_FIXTURE.read_text(encoding="utf-8"))
    connection.execute(
        "INSERT INTO schema_meta(key, value) VALUES ('schema_version', '9')"
    )
    for migration in migrations.MIGRATIONS:
        if 7 <= migration.version <= 9:
            connection.execute(
                "INSERT INTO schema_migrations(version, name) VALUES (?, ?)",
                (migration.version, migration.name),
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
        ) VALUES (
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


def test_v9_fixture_is_exact_stage_five_schema() -> None:
    """The v9→v10 boundary is frozen from the last Stage 5 checkpoint."""

    fixture = V9_SCHEMA_FIXTURE.read_bytes()

    assert V9_SCHEMA_SOURCE_COMMIT == "2c2f98d69306519ba292442be6f2cc1fc8d92682"
    assert hashlib.sha256(fixture).hexdigest() == V9_SCHEMA_SHA256
    assert len(fixture.splitlines()) == 539


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
        chunk_hash = reopened.execute(
            "SELECT text_sha256 FROM chunks WHERE id = 'historical-chunk'"
        ).fetchone()
        assert chunk_hash == (
            hashlib.sha256(b"preserve this historical text").hexdigest(),
        )
        assert reopened.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(7,), (8,), (9,), (10,)]
        assert reopened.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert reopened.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        reopened.close()


def test_v7_migrates_vector_provenance_without_trusting_legacy_rows(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "schema-v7.sqlite3"
    connection = _create_v7_database(database_path)
    connection.execute(
        """
        INSERT INTO embedding_models(
          id, name, provider, dimension, distance, config_json, created_at
        ) VALUES (
          'legacy-model', '/models/local', 'sentence-transformers', 2,
          'cosine', '{}', '2026-01-01'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO vectors(
          id, model_id, object_type, object_id, text_sha256, dimension, dtype,
          vector, metadata_json, created_at, updated_at
        ) VALUES (
          'legacy-vector', 'legacy-model', 'document', 'historical-document',
          'untrusted-input-hash', 2, 'float32', ?, '{}',
          '2026-01-01', '2026-01-01'
        )
        """,
        (b"\x00" * 8,),
    )
    connection.execute(
        """
        INSERT INTO vector_indexes(
          id, model_id, object_type, index_path, vector_count, created_at,
          metadata_json
        ) VALUES (
          'legacy-index', 'legacy-model', 'document', 'legacy.index', 1,
          '2026-01-01', '{}'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO embedding_runs(
          id, model_id, started_at, status, config_json
        ) VALUES (
          'legacy-run', 'legacy-model', '2026-01-01', 'completed', '{}'
        )
        """
    )
    connection.commit()
    connection.close()

    migrating = sqlite3.connect(database_path)
    migrations.initialize_database(
        migrating,
        backup_path=tmp_path / "schema-v7.pre-migration.sqlite3",
    )
    migrating.close()

    reopened = sqlite3.connect(database_path)
    try:
        assert _schema_version(reopened) == _current_schema_version()
        assert reopened.execute(
            """
            SELECT content_revision_sha256
            FROM documents WHERE id = 'historical-document'
            """
        ).fetchone() == (
            document_content_revision_sha256(
                title="Historical v6 paper",
                relative_path="paper.md",
                text="preserve this historical text",
            ),
        )
        assert reopened.execute(
            """
            SELECT model_fingerprint, fingerprint_algorithm
            FROM embedding_models WHERE id = 'legacy-model'
            """
        ).fetchone() == ("legacy-unknown", "legacy-unknown")
        assert reopened.execute(
            """
            SELECT source_content_sha256, model_fingerprint, algorithm_version
            FROM vectors WHERE id = 'legacy-vector'
            """
        ).fetchone() == (
            "legacy-unknown",
            "legacy-unknown",
            "legacy-unknown",
        )
        assert reopened.execute("SELECT COUNT(*) FROM vector_indexes").fetchone() == (
            0,
        )
        assert reopened.execute(
            """
            SELECT sources_changed, owner_pid
            FROM embedding_runs WHERE id = 'legacy-run'
            """
        ).fetchone() == (0, None)
        assert reopened.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(7,), (8,), (9,), (10,)]
        assert reopened.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert reopened.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        reopened.close()


def test_v8_migration_hashes_chunks_in_bounded_pages(tmp_path: Path) -> None:
    database_path = tmp_path / "many-chunks-v7.sqlite3"
    connection = _create_v7_database(database_path)
    connection.executemany(
        """
        INSERT INTO chunks(id, document_id, chunk_index, text, char_count)
        VALUES (?, 'historical-document', ?, ?, ?)
        """,
        (
            (
                f"paged-chunk-{index:04d}",
                index,
                f"synthetic chunk {index}",
                len(f"synthetic chunk {index}"),
            )
            for index in range(1, 602)
        ),
    )
    connection.commit()

    migrations.initialize_database(
        connection,
        backup_path=tmp_path / "many-chunks-v7.pre-migration.sqlite3",
    )

    assert connection.execute(
        "SELECT COUNT(*) FROM chunks WHERE text_sha256 = 'legacy-unknown'"
    ).fetchone() == (0,)
    assert connection.execute(
        "SELECT text_sha256 FROM chunks WHERE id = 'paged-chunk-0601'"
    ).fetchone() == (hashlib.sha256(b"synthetic chunk 601").hexdigest(),)
    connection.close()


def test_real_v8_database_migrates_to_current_without_losing_data(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "schema-v8.sqlite3"
    backup_path = tmp_path / "schema-v8.pre-migration.sqlite3"
    connection = _create_v8_database(database_path)
    assert _schema_version(connection) == 8
    assert (
        connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'jobs'"
        ).fetchone()
        is None
    )
    connection.close()

    migrating = sqlite3.connect(database_path)
    migrations.initialize_database(migrating, backup_path=backup_path)
    migrating.close()

    reopened = sqlite3.connect(database_path)
    try:
        assert _schema_version(reopened) == _current_schema_version()
        assert reopened.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(7,), (8,), (9,), (10,)]
        assert reopened.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'jobs'"
        ).fetchone() == ("jobs",)
        assert reopened.execute("SELECT COUNT(*) FROM jobs").fetchone() == (0,)
        _assert_historical_rows_preserved(reopened)
        assert reopened.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert reopened.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        reopened.close()

    backup = sqlite3.connect(backup_path)
    try:
        assert _schema_version(backup) == 8
        assert (
            backup.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'jobs'"
            ).fetchone()
            is None
        )
        _assert_historical_rows_preserved(backup)
    finally:
        backup.close()


def test_v9_backfills_legacy_corpus_and_zotero_sources(tmp_path: Path) -> None:
    database_path = tmp_path / "v9-source-backfill.sqlite3"
    connection = _create_v8_database(database_path)
    connection.execute(
        """
        INSERT INTO corpora(id, root_path, created_at, updated_at)
        VALUES (
          'zotero-shadow-corpus', 'zotero://sources/legacy-zotero',
          '2026-01-02', '2026-01-03'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO zotero_sources(
          id, source_type, local_api_url, data_dir, library_id, library_type,
          name, last_version, created_at, updated_at
        )
        VALUES (
          'legacy-zotero', 'local_api', 'http://127.0.0.1:23119/api',
          '/synthetic-zotero', '0', 'user', 'Synthetic Zotero', 42,
          '2026-01-02', '2026-01-03'
        )
        """
    )
    connection.commit()

    migrations.initialize_database(
        connection,
        backup_path=tmp_path / "v9-source-backfill.pre-migration.sqlite3",
    )

    corpus_source_id, corpus_signature = registered_source_identity(
        kind="corpus_directory",
        locator="/synthetic-corpus",
    )
    zotero_config = {
        "local_api_url": "http://127.0.0.1:23119/api",
        "data_dir": "/synthetic-zotero",
        "library_id": "0",
        "library_type": "user",
        "filters": {},
    }
    zotero_source_id, zotero_signature = registered_source_identity(
        kind="zotero_profile",
        locator="legacy-zotero",
        config=zotero_config,
    )

    rows = connection.execute(
        """
        SELECT id, kind, display_name, root_path, zotero_source_id,
               profile_signature, config_json, created_at, updated_at
        FROM registered_sources
        ORDER BY kind, id
        """
    ).fetchall()
    assert rows == [
        (
            corpus_source_id,
            "corpus_directory",
            "synthetic-corpus",
            "/synthetic-corpus",
            None,
            corpus_signature,
            '{"legacy_corpus_id":"historical-corpus"}',
            "2026-01-01",
            "2026-01-01",
        ),
        (
            zotero_source_id,
            "zotero_profile",
            "Synthetic Zotero",
            None,
            "legacy-zotero",
            zotero_signature,
            json.dumps(zotero_config, sort_keys=True, separators=(",", ":")),
            "2026-01-02",
            "2026-01-03",
        ),
    ]
    assert connection.execute(
        "SELECT COUNT(*) FROM registered_sources WHERE root_path LIKE 'zotero://%'"
    ).fetchone() == (0,)
    assert connection.execute(
        """
        SELECT id, source_id, profile_signature, materialization_signature,
               last_version, requires_full_sync, revision
        FROM zotero_sync_profiles
        """
    ).fetchone() == (
        zotero_source_id,
        "legacy-zotero",
        zotero_signature,
        None,
        None,
        1,
        0,
    )
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()


def test_v9_backfill_canonicalizes_zotero_profile_without_duplicate_registration(
    tmp_path: Path,
) -> None:
    from paper_galaxy.services.sources import (
        SOURCE_KIND_ZOTERO,
        list_sources,
        register_zotero_source,
    )

    metadata_dir = tmp_path / ".paper-galaxy"
    metadata_dir.mkdir()
    database_path = metadata_dir / "paper_galaxy.sqlite3"
    connection = _create_v8_database(database_path)
    raw_data_dir = tmp_path / "zotero-data" / "nested" / ".."
    connection.execute(
        """
        INSERT INTO zotero_sources(
          id, source_type, local_api_url, data_dir, library_id, library_type,
          name, last_version, created_at, updated_at
        ) VALUES (
          'canonical-zotero', 'local_api', 'HTTP://LOCALHOST.:23119/api/', ?,
          '0', 'user', 'Canonical Zotero', 12, '2026-01-02', '2026-01-03'
        )
        """,
        (str(raw_data_dir),),
    )
    connection.commit()

    migrations.initialize_database(
        connection,
        backup_path=tmp_path / "canonical-zotero.pre-migration.sqlite3",
    )
    stored_config = connection.execute(
        """
        SELECT config_json
        FROM registered_sources
        WHERE kind = 'zotero_profile' AND zotero_source_id = 'canonical-zotero'
        """
    ).fetchone()
    connection.close()

    assert stored_config is not None
    assert json.loads(str(stored_config[0])) == {
        "data_dir": str((tmp_path / "zotero-data").resolve()),
        "filters": {},
        "library_id": "0",
        "library_type": "user",
        "local_api_url": "http://localhost:23119/api/",
    }
    profile, created = register_zotero_source(tmp_path, "canonical-zotero")
    profiles = list_sources(tmp_path, kind=SOURCE_KIND_ZOTERO)

    assert created is False
    assert profiles == [profile]


def test_v10_migration_does_not_claim_removed_empty_zotero_profile(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "removed-empty-v9.sqlite3"
    connection = _create_v9_database(database_path)
    connection.execute(
        """
        INSERT INTO zotero_sources(
          id, source_type, local_api_url, library_id, library_type, name,
          created_at, updated_at
        ) VALUES (
          'removed-empty-source', 'local_api',
          'http://127.0.0.1:23119/api', '0', 'user', 'Removed empty source',
          '2026-01-01', '2026-01-01'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO registered_sources(
          id, kind, display_name, root_path, zotero_source_id,
          profile_signature, config_json, created_at, updated_at, removed_at
        ) VALUES (
          'removed-empty-profile', 'zotero_profile', 'Removed empty profile',
          NULL, 'removed-empty-source', 'removed-empty-signature',
          '{"filters":{}}', '2026-01-01', '2026-01-02', '2026-01-02'
        )
        """
    )
    connection.commit()

    migrations.initialize_database(
        connection,
        backup_path=tmp_path / "removed-empty-v9.pre-migration.sqlite3",
    )

    assert (
        connection.execute(
            "SELECT 1 FROM zotero_sync_profiles WHERE id = 'removed-empty-profile'"
        ).fetchone()
        is None
    )
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()


@pytest.mark.parametrize(
    ("index_name", "column_name", "active_statuses"),
    [
        (
            "idx_jobs_active_dedupe",
            "request_key",
            "'queued', 'running', 'cancelling'",
        ),
        ("idx_jobs_single_writer", "writer_slot", "'running', 'cancelling'"),
    ],
)
def test_current_schema_rejects_expanded_job_index_predicate(
    tmp_path: Path,
    index_name: str,
    column_name: str,
    active_statuses: str,
) -> None:
    database_path = tmp_path / "expanded-job-index.sqlite3"
    connection = sqlite3.connect(database_path)
    migrations.initialize_database(connection)
    connection.execute(f"DROP INDEX {index_name}")
    connection.execute(
        f"""
        CREATE UNIQUE INDEX {index_name}
        ON jobs({column_name})
        WHERE status IN ({active_statuses}) OR status = 'completed'
        """
    )
    connection.commit()
    connection.close()
    before = database_path.read_bytes()

    damaged = sqlite3.connect(database_path)
    try:
        with pytest.raises(
            UnsupportedSchemaError,
            match=r"wrong uniqueness or predicate semantics",
        ):
            migrations.initialize_database(damaged)
    finally:
        damaged.close()

    assert database_path.read_bytes() == before


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


def test_v8_migration_failure_restores_legacy_vectors_and_indexes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "v8-rollback.sqlite3"
    connection = _create_v7_database(database_path)
    connection.execute(
        """
        INSERT INTO embedding_models(
          id, name, provider, dimension, distance, config_json, created_at
        ) VALUES ('model', 'model', 'test', 2, 'cosine', '{}', '2026-01-01')
        """
    )
    connection.execute(
        """
        INSERT INTO vector_indexes(
          id, model_id, object_type, index_path, vector_count, created_at,
          metadata_json
        ) VALUES (
          'must-survive', 'model', 'document', 'legacy.index', 0,
          '2026-01-01', '{}'
        )
        """
    )
    connection.commit()
    connection.close()

    original = list(migrations.MIGRATIONS)
    version_seven = next(entry for entry in original if entry.version == 7)
    real_version_eight = next(entry for entry in original if entry.version == 8)

    class FailingVersionEight:
        version = 8
        name = real_version_eight.name

        @staticmethod
        def up(connection: sqlite3.Connection) -> None:
            real_version_eight.up(connection)
            raise RuntimeError("synthetic v8 failure")

    monkeypatch.setattr(
        migrations,
        "MIGRATIONS",
        (version_seven, FailingVersionEight()),
    )
    monkeypatch.setattr(migrations, "CURRENT_SCHEMA_VERSION", 8)
    migrating = sqlite3.connect(database_path)
    with pytest.raises(RuntimeError, match="synthetic v8 failure"):
        migrations.initialize_database(
            migrating,
            backup_path=tmp_path / "v8-rollback.pre-migration.sqlite3",
        )
    assert migrating.in_transaction is False
    migrating.close()

    reopened = sqlite3.connect(database_path)
    try:
        assert _schema_version(reopened) == 7
        assert "text_sha256" not in {
            str(row[1]) for row in reopened.execute("PRAGMA table_info(chunks)")
        }
        assert "content_revision_sha256" not in {
            str(row[1]) for row in reopened.execute("PRAGMA table_info(documents)")
        }
        assert reopened.execute("SELECT id FROM vector_indexes").fetchall() == [
            ("must-survive",)
        ]
        assert reopened.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(7,)]
    finally:
        reopened.close()


def test_v9_migration_failure_rolls_back_tables_and_source_backfill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "v9-rollback.sqlite3"
    connection = _create_v8_database(database_path)
    connection.execute(
        """
        INSERT INTO zotero_sources(
          id, source_type, name, created_at, updated_at
        ) VALUES (
          'rollback-zotero', 'local_api', 'Rollback Zotero',
          '2026-01-02', '2026-01-03'
        )
        """
    )
    connection.commit()
    connection.close()

    original = list(migrations.MIGRATIONS)
    version_seven = next(entry for entry in original if entry.version == 7)
    version_eight = next(entry for entry in original if entry.version == 8)
    real_version_nine = next(entry for entry in original if entry.version == 9)

    class FailingVersionNine:
        version = 9
        name = real_version_nine.name

        @staticmethod
        def up(connection: sqlite3.Connection) -> None:
            real_version_nine.up(connection)
            assert connection.execute(
                "SELECT COUNT(*) FROM registered_sources"
            ).fetchone() == (2,)
            raise RuntimeError("synthetic v9 failure")

    monkeypatch.setattr(
        migrations,
        "MIGRATIONS",
        (version_seven, version_eight, FailingVersionNine()),
    )
    monkeypatch.setattr(migrations, "CURRENT_SCHEMA_VERSION", 9)

    migrating = sqlite3.connect(database_path)
    with pytest.raises(RuntimeError, match="synthetic v9 failure"):
        migrations.initialize_database(
            migrating,
            backup_path=tmp_path / "v9-rollback.pre-migration.sqlite3",
        )
    assert migrating.in_transaction is False
    migrating.close()

    reopened = sqlite3.connect(database_path)
    try:
        assert _schema_version(reopened) == 8
        assert reopened.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(7,), (8,)]
        assert (
            reopened.execute(
                """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name IN ('registered_sources', 'jobs')
            """
            ).fetchall()
            == []
        )
        assert reopened.execute(
            "SELECT name FROM zotero_sources WHERE id = 'rollback-zotero'"
        ).fetchone() == ("Rollback Zotero",)
        _assert_historical_rows_preserved(reopened)
    finally:
        reopened.close()


def test_v10_migration_failure_rolls_back_columns_tables_and_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "v10-rollback.sqlite3"
    connection = _create_v9_database(database_path)
    connection.close()
    original = list(migrations.MIGRATIONS)
    real_version_ten = next(entry for entry in original if entry.version == 10)

    class FailingVersionTen:
        version = 10
        name = real_version_ten.name

        @staticmethod
        def up(connection: sqlite3.Connection) -> None:
            real_version_ten.up(connection)
            assert connection.execute(
                "SELECT name FROM sqlite_schema WHERE name = 'zotero_sync_profiles'"
            ).fetchone() == ("zotero_sync_profiles",)
            raise RuntimeError("synthetic v10 failure")

    monkeypatch.setattr(
        migrations,
        "MIGRATIONS",
        tuple(
            FailingVersionTen() if entry.version == 10 else entry for entry in original
        ),
    )
    migrating = sqlite3.connect(database_path)
    with pytest.raises(RuntimeError, match="synthetic v10 failure"):
        migrations.initialize_database(
            migrating,
            backup_path=tmp_path / "v10-rollback.pre-migration.sqlite3",
        )
    migrating.close()

    reopened = sqlite3.connect(database_path)
    try:
        assert _schema_version(reopened) == 9
        assert reopened.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(7,), (8,), (9,)]
        assert (
            reopened.execute(
                """
            SELECT name FROM sqlite_schema
            WHERE name IN (
              'zotero_sync_profiles', 'zotero_sync_run_details',
              'zotero_profile_items', 'zotero_child_items', 'zotero_tombstones'
            )
            """
            ).fetchall()
            == []
        )
        item_columns = {
            str(row[1]) for row in reopened.execute("PRAGMA table_info(zotero_items)")
        }
        assert "deleted_at" not in item_columns
        assert "deleted_version" not in item_columns
    finally:
        reopened.close()


def test_v9_capability_rejects_partially_applied_v10_columns(tmp_path: Path) -> None:
    database_path = tmp_path / "partial-v10.sqlite3"
    connection = _create_v9_database(database_path)
    connection.execute("ALTER TABLE zotero_items ADD COLUMN deleted_at TEXT")
    connection.commit()
    connection.close()

    migrating = sqlite3.connect(database_path)
    with pytest.raises(UnsupportedSchemaError, match="future columns deleted_at"):
        migrations.initialize_database(
            migrating,
            backup_path=tmp_path / "partial-v10.pre-migration.sqlite3",
        )
    migrating.close()


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


def test_frozen_v9_zotero_document_is_rematerialized_on_first_v10_sync(
    tmp_path: Path,
) -> None:
    from paper_galaxy.storage.sqlite import connect_read_only
    from paper_galaxy.zotero.importers import (
        import_from_zotero,
        stable_zotero_corpus_id,
        stable_zotero_document_id,
        stable_zotero_item_id,
        stable_zotero_source_id,
    )
    from paper_galaxy.zotero.models import ZoteroDeletedBatch, ZoteroSyncBatch

    project_dir = tmp_path / "project"
    metadata_dir = project_dir / ".paper-galaxy"
    metadata_dir.mkdir(parents=True)
    database_path = metadata_dir / "paper_galaxy.sqlite3"
    connection = _create_v9_database(database_path)
    api_url = "http://127.0.0.1:23119/api"
    source_id = stable_zotero_source_id(api_url, "0")
    corpus_id = stable_zotero_corpus_id(source_id)
    item_id = stable_zotero_item_id(source_id, "PARENT01")
    document_id = stable_zotero_document_id(source_id, "PARENT01")
    source_config = {
        "local_api_url": api_url,
        "data_dir": None,
        "library_id": "0",
        "library_type": "user",
        "filters": {"include_status": "all", "pdf_policy": "metadata"},
    }
    profile_id, profile_signature = registered_source_identity(
        kind="zotero_profile",
        locator=source_id,
        config=source_config,
    )
    parent_v5: dict[str, object] = {
        "key": "PARENT01",
        "version": 5,
        "library": {"id": 0, "type": "user", "name": "Synthetic"},
        "data": {
            "key": "PARENT01",
            "version": 5,
            "itemType": "journalArticle",
            "title": "Legacy v9 Zotero paper",
            "date": "2024",
            "creators": [],
            "tags": [],
            "collections": [],
        },
    }
    connection.execute(
        """
        INSERT INTO zotero_sources(
          id, source_type, local_api_url, library_id, library_type, name,
          last_version, created_at, updated_at
        ) VALUES (?, 'local_api', ?, '0', 'user', 'Legacy Zotero', 5, ?, ?)
        """,
        (source_id, api_url, "2026-01-01", "2026-01-01"),
    )
    connection.execute(
        """
        INSERT INTO registered_sources(
          id, kind, display_name, zotero_source_id, profile_signature,
          config_json, created_at, updated_at
        ) VALUES (?, 'zotero_profile', 'Legacy Zotero', ?, ?, ?, ?, ?)
        """,
        (
            profile_id,
            source_id,
            profile_signature,
            json.dumps(source_config, sort_keys=True, separators=(",", ":")),
            "2026-01-01",
            "2026-01-01",
        ),
    )
    connection.execute(
        """
        INSERT INTO corpora(id, root_path, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (corpus_id, f"zotero://sources/{source_id}", "2026-01-01", "2026-01-01"),
    )
    connection.execute(
        """
        INSERT INTO zotero_items(
          id, source_id, zotero_key, version, item_type, title, reading_status,
          data_json, child_manifest_json, created_at, updated_at
        ) VALUES (?, ?, 'PARENT01', 5, 'journalArticle', ?, 'unknown', ?, NULL, ?, ?)
        """,
        (
            item_id,
            source_id,
            "Legacy v9 Zotero paper",
            json.dumps(parent_v5, sort_keys=True),
            "2026-01-01",
            "2026-01-01",
        ),
    )
    connection.execute(
        """
        INSERT INTO documents(
          id, corpus_id, path, relative_path, file_type, title, sha256,
          size_bytes, mtime_ns, char_count, status, first_seen_at,
          last_seen_at, updated_at
        ) VALUES (?, ?, ?, ?, 'zotero', ?, 'legacy-sha', 0, 0, 18, 'active', ?, ?, ?)
        """,
        (
            document_id,
            corpus_id,
            "zotero://select/items/PARENT01",
            "zotero/PARENT01",
            "Legacy v9 Zotero paper",
            "2026-01-01",
            "2026-01-01",
            "2026-01-01",
        ),
    )
    connection.execute(
        "INSERT INTO document_texts(document_id, text) VALUES (?, ?)",
        (document_id, "stale v9 materialization"),
    )
    connection.execute(
        """
        INSERT INTO chunks(id, document_id, chunk_index, text, char_count)
        VALUES ('legacy-zotero-chunk', ?, 0, 'stale v9 materialization', 24)
        """,
        (document_id,),
    )
    connection.execute(
        """
        INSERT INTO documents_fts(document_id, title, relative_path, text)
        VALUES (?, ?, 'zotero/PARENT01', 'stale v9 materialization')
        """,
        (document_id, "Legacy v9 Zotero paper"),
    )
    connection.execute(
        """
        INSERT INTO zotero_document_links(document_id, zotero_item_id, role)
        VALUES (?, ?, 'primary')
        """,
        (document_id, item_id),
    )
    connection.commit()
    connection.close()

    parent_v6 = json.loads(json.dumps(parent_v5))
    parent_v6["version"] = 6
    assert isinstance(parent_v6["data"], dict)
    parent_v6["data"]["version"] = 6
    note_v6 = {
        "key": "NOTE0001",
        "version": 6,
        "data": {
            "key": "NOTE0001",
            "version": 6,
            "itemType": "note",
            "parentItem": "PARENT01",
            "note": "<p>fresh v10 evidence</p>",
        },
    }

    class FrozenV9Client:
        def sync_collections(self, *, cancel_requested=None):
            del cancel_requested
            return ZoteroSyncBatch((), 6)

        def sync_items(self, *, since, limit=None, cancel_requested=None):
            del limit, cancel_requested
            assert since == 0
            return ZoteroSyncBatch((parent_v6, note_v6), 6)

        def items_by_keys(self, keys, *, cancel_requested=None):
            del keys, cancel_requested
            return ZoteroSyncBatch((), 6)

        def deleted_since(self, *, since, cancel_requested=None):
            del cancel_requested
            assert since == 0
            return ZoteroDeletedBatch({}, 6)

    summary = import_from_zotero(
        project_dir=project_dir,
        api_url=api_url,
        client=FrozenV9Client(),
        pdf_policy="metadata",
        min_chars=1,
        build_reading_map=False,
    )

    migrated = connect_read_only(project_dir)
    try:
        text = migrated.execute(
            "SELECT text FROM document_texts WHERE document_id = ?", (document_id,)
        ).fetchone()[0]
        profile = migrated.execute(
            """
            SELECT materialization_signature, last_version, requires_full_sync
            FROM zotero_sync_profiles WHERE id = ?
            """,
            (profile_id,),
        ).fetchone()
        membership = migrated.execute(
            """
            SELECT is_member FROM zotero_profile_items
            WHERE profile_id = ? AND zotero_item_id = ?
            """,
            (profile_id, item_id),
        ).fetchone()
    finally:
        migrated.close()

    assert summary.full_sync is True
    assert summary.last_version_after == 6
    assert "fresh v10 evidence" in text
    assert "stale v9 materialization" not in text
    assert profile[0] is not None and tuple(profile)[1:] == (6, 0)
    assert tuple(membership) == (1,)
