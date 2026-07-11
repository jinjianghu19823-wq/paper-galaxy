import sqlite3
from pathlib import Path

import pytest

from paper_galaxy.config import _validate_model
from paper_galaxy.errors import UnsupportedSchemaError
from paper_galaxy.storage.migrations import (
    CURRENT_SCHEMA_VERSION,
    initialize_database,
)
from paper_galaxy.storage.repository import Repository
from paper_galaxy.storage.sqlite import connect_database, resolve_database_path


def test_database_path_defaults_under_project_metadata(tmp_path: Path) -> None:
    assert (
        resolve_database_path(tmp_path)
        == (tmp_path / ".paper-galaxy" / "paper_galaxy.sqlite3").resolve()
    )


def test_database_path_respects_project_config(tmp_path: Path) -> None:
    metadata_dir = tmp_path / ".paper-galaxy"
    metadata_dir.mkdir()
    (metadata_dir / "project.toml").write_text(
        "\n".join(
            [
                'project_name = "Custom"',
                'created_by = "test"',
                "map_seed = 42",
                "corpus_dirs = []",
                'database_path = ".paper-galaxy/custom.sqlite3"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    assert (
        resolve_database_path(tmp_path)
        == (tmp_path / ".paper-galaxy" / "custom.sqlite3").resolve()
    )


def test_schema_initializes_expected_tables(tmp_path: Path) -> None:
    connection = connect_database(tmp_path)
    try:
        initialize_database(connection)
        table_rows = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type IN ('table', 'virtual')
            """
        ).fetchall()
        tables = {str(row["name"]) for row in table_rows}
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
    finally:
        connection.close()

    assert "schema_meta" in tables
    assert "documents" in tables
    assert "chunks" in tables
    assert "scan_runs" in tables
    assert "extraction_reports" in tables
    assert "embedding_models" in tables
    assert "vectors" in tables
    assert "embedding_runs" in tables
    assert "cluster_label_overrides" in tables
    assert "map_runs" in tables
    assert "map_run_points" in tables
    assert "map_run_clusters" in tables
    assert "zotero_sources" in tables
    assert "zotero_import_runs" in tables
    assert "zotero_items" in tables
    assert "zotero_creators" in tables
    assert "zotero_collections" in tables
    assert "zotero_item_collections" in tables
    assert "zotero_item_tags" in tables
    assert "zotero_attachments" in tables
    assert "zotero_document_links" in tables
    assert "zotero_sync_profiles" in tables
    assert "zotero_profile_items" in tables
    assert "documents_fts" in tables

    assert version is not None
    assert version["value"] == str(CURRENT_SCHEMA_VERSION)


def test_unsupported_version_one_database_is_not_fabricated(tmp_path: Path) -> None:
    connection = connect_database(tmp_path)
    try:
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('schema_version', '1')"
        )
        connection.commit()
        with pytest.raises(UnsupportedSchemaError):
            initialize_database(connection)
        report_table = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name = 'extraction_reports'
            """
        ).fetchone()
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
    finally:
        connection.close()

    assert report_table is None
    assert version is not None
    assert version["value"] == "1"


def test_cluster_label_override_repository_methods(tmp_path: Path) -> None:
    connection = connect_database(tmp_path)
    try:
        initialize_database(connection)
        repository = Repository(connection, resolve_database_path(tmp_path))
        with connection:
            created = repository.upsert_cluster_label_override(
                cluster_signature="cluster_abc",
                label="Neural Operators",
                now="2026-01-01T00:00:00+00:00",
            )
            updated = repository.upsert_cluster_label_override(
                cluster_signature="cluster_abc",
                label="Operator Learning",
                metadata={"note": "manual"},
                now="2026-01-02T00:00:00+00:00",
            )
            labels = repository.get_cluster_label_overrides(["cluster_abc", "missing"])
            rows = repository.list_cluster_label_overrides()
            deleted = repository.delete_cluster_label_override("cluster_abc")
            deleted_again = repository.delete_cluster_label_override("cluster_abc")
    finally:
        connection.close()

    assert created["label"] == "Neural Operators"
    assert updated["label"] == "Operator Learning"
    assert updated["metadata"] == {"note": "manual"}
    assert labels == {"cluster_abc": "Operator Learning"}
    assert rows[0]["cluster_signature"] == "cluster_abc"
    assert deleted is True
    assert deleted_again is False


def _zotero_storage_fixture(
    connection: sqlite3.Connection, repository: Repository
) -> tuple[dict[str, object], dict[str, object]]:
    now = "2026-01-01T00:00:00+00:00"
    connection.execute(
        """
        INSERT INTO zotero_sources(
          id, source_type, local_api_url, library_id, library_type,
          name, created_at, updated_at
        ) VALUES (
          'zotero-source', 'local_api', 'http://localhost:23119/api',
          '0', 'user', 'Synthetic Zotero', ?, ?
        )
        """,
        (now, now),
    )
    for profile_id, profile_signature in (
        ("profile-a", "a" * 64),
        ("profile-b", "b" * 64),
    ):
        connection.execute(
            """
            INSERT INTO registered_sources(
              id, kind, display_name, zotero_source_id, profile_signature,
              config_json, created_at, updated_at
            ) VALUES (?, 'zotero_profile', ?, 'zotero-source', ?, '{}', ?, ?)
            """,
            (profile_id, profile_id, profile_signature, now, now),
        )
        repository.ensure_zotero_sync_profile(
            profile_id=profile_id,
            source_id="zotero-source",
            profile_signature=profile_signature,
            now=now,
        )
    first = repository.prepare_zotero_sync_profile_materialization(
        profile_id="profile-a",
        materialization_signature="1" * 64,
        now=now,
    )
    second = repository.prepare_zotero_sync_profile_materialization(
        profile_id="profile-b",
        materialization_signature="1" * 64,
        now=now,
    )
    return first, second


def _insert_zotero_item_and_document(
    connection: sqlite3.Connection, repository: Repository
) -> None:
    now = "2026-01-01T00:00:00+00:00"
    repository.upsert_corpus("zotero-corpus", "zotero://sources/test", now)
    assert repository.upsert_zotero_item(
        {
            "id": "zotero-item",
            "source_id": "zotero-source",
            "zotero_key": "ITEM0001",
            "version": 10,
            "item_type": "journalArticle",
            "title": "Synthetic item",
            "reading_status": "unknown",
            "data": {"key": "ITEM0001", "version": 10},
            "created_at": now,
            "updated_at": now,
        }
    )
    connection.execute(
        """
        INSERT INTO documents(
          id, corpus_id, path, relative_path, file_type, title, sha256,
          size_bytes, mtime_ns, char_count, status, first_seen_at,
          last_seen_at, updated_at
        ) VALUES (
          'document', 'zotero-corpus', 'zotero://ITEM0001',
          'zotero/ITEM0001', 'zotero', 'Synthetic item', 'sha',
          0, 0, 10, 'active', ?, ?, ?
        )
        """,
        (now, now, now),
    )
    connection.execute(
        "INSERT INTO document_texts(document_id, text) VALUES ('document', 'text')"
    )
    repository.upsert_zotero_document_link(
        document_id="document",
        zotero_item_id="zotero-item",
        attachment_id=None,
        role="primary",
    )


def test_zotero_materialization_is_source_global_and_fences_old_runners(
    tmp_path: Path,
) -> None:
    connection = connect_database(tmp_path)
    try:
        initialize_database(connection)
        repository = Repository(connection, resolve_database_path(tmp_path))
        with connection:
            first, second = _zotero_storage_fixture(connection, repository)
            same = repository.prepare_zotero_sync_profile_materialization(
                profile_id="profile-a",
                materialization_signature="1" * 64,
                now="2026-01-01T12:00:00+00:00",
                explicit_full=True,
            )
        with pytest.raises(RuntimeError, match="explicit full sync"):
            with connection:
                repository.prepare_zotero_sync_profile_materialization(
                    profile_id="profile-b",
                    materialization_signature="2" * 64,
                    now="2026-01-02T00:00:00+00:00",
                )
        with connection:
            changed = repository.prepare_zotero_sync_profile_materialization(
                profile_id="profile-b",
                materialization_signature="2" * 64,
                now="2026-01-02T00:00:00+00:00",
                explicit_full=True,
            )
        rows = connection.execute(
            """
            SELECT id, materialization_signature, requires_full_sync, revision
            FROM zotero_sync_profiles ORDER BY id
            """
        ).fetchall()
        with pytest.raises(RuntimeError, match="fence changed concurrently"):
            repository.assert_zotero_sync_profile_fence(
                profile_id="profile-a",
                expected_revision=int(first["revision"]),
                expected_last_version=None,
                expected_materialization_signature="1" * 64,
            )
    finally:
        connection.close()

    assert second["materialization_signature"] == "1" * 64
    assert int(same["revision"]) == int(first["revision"]) + 1
    assert same["requires_full_sync"] is True
    assert changed["materialization_signature"] == "2" * 64
    assert [(row[1], row[2]) for row in rows] == [("2" * 64, 1), ("2" * 64, 1)]
    assert all(int(row[3]) > int(first["revision"]) for row in rows)


def test_zotero_profile_membership_is_versioned_and_controls_union_visibility(
    tmp_path: Path,
) -> None:
    connection = connect_database(tmp_path)
    try:
        initialize_database(connection)
        repository = Repository(connection, resolve_database_path(tmp_path))
        with connection:
            first, _ = _zotero_storage_fixture(connection, repository)
            _insert_zotero_item_and_document(connection, repository)
            assert repository.upsert_zotero_profile_item(
                profile_id="profile-a",
                zotero_item_id="zotero-item",
                observed_version=10,
                expected_profile_revision=int(first["revision"]),
                expected_materialization_signature="1" * 64,
                now="2026-01-01T00:00:00+00:00",
            )
            assert repository.remove_zotero_profile_item(
                profile_id="profile-a",
                zotero_item_id="zotero-item",
                observed_version=12,
                expected_profile_revision=int(first["revision"]),
                expected_materialization_signature="1" * 64,
                now="2026-01-02T00:00:00+00:00",
            )
            assert not repository.upsert_zotero_profile_item(
                profile_id="profile-a",
                zotero_item_id="zotero-item",
                observed_version=11,
                expected_profile_revision=int(first["revision"]),
                expected_materialization_signature="1" * 64,
                now="2026-01-03T00:00:00+00:00",
            )
            changed = repository.reconcile_zotero_document_activity(
                ["zotero-item"], now="2026-01-03T00:00:00+00:00"
            )
        row = connection.execute(
            """
            SELECT is_member, observed_version FROM zotero_profile_items
            WHERE profile_id = 'profile-a' AND zotero_item_id = 'zotero-item'
            """
        ).fetchone()
        status = connection.execute(
            "SELECT status FROM documents WHERE id = 'document'"
        ).fetchone()[0]
    finally:
        connection.close()

    assert tuple(row) == (0, 12)
    assert changed == {"active": 0, "unindexed": 1, "missing": 0}
    assert status == "unindexed"


def test_zotero_parent_delete_cascades_and_tombstone_blocks_equal_resurrection(
    tmp_path: Path,
) -> None:
    connection = connect_database(tmp_path)
    try:
        initialize_database(connection)
        repository = Repository(connection, resolve_database_path(tmp_path))
        with connection:
            first, _ = _zotero_storage_fixture(connection, repository)
            _insert_zotero_item_and_document(connection, repository)
            assert repository.upsert_zotero_profile_item(
                profile_id="profile-a",
                zotero_item_id="zotero-item",
                observed_version=10,
                expected_profile_revision=int(first["revision"]),
                expected_materialization_signature="1" * 64,
                now="2026-01-01T00:00:00+00:00",
            )
            assert repository.upsert_zotero_child_item(
                source_id="zotero-source",
                zotero_key="NOTE0001",
                parent_key="ITEM0001",
                item_type="note",
                version=10,
                data={"key": "NOTE0001", "parentItem": "ITEM0001"},
                now="2026-01-01T00:00:00+00:00",
            )
            assert repository.upsert_zotero_attachment(
                {
                    "id": "attachment",
                    "source_id": "zotero-source",
                    "parent_zotero_item_id": "zotero-item",
                    "zotero_key": "ATTACH01",
                    "version": 10,
                    "path_status": "no_local_file",
                    "data": {"key": "ATTACH01"},
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                }
            )
            repository.record_zotero_tombstone(
                source_id="zotero-source",
                object_type="item",
                zotero_key="ITEM0001",
                library_version=12,
                deleted_at="2026-01-02T00:00:00+00:00",
            )
            assert repository.mark_zotero_parent_deleted(
                source_id="zotero-source",
                zotero_key="ITEM0001",
                library_version=12,
                deleted_at="2026-01-02T00:00:00+00:00",
            )
            assert not repository.upsert_zotero_item(
                {
                    "id": "zotero-item",
                    "source_id": "zotero-source",
                    "zotero_key": "ITEM0001",
                    "version": 12,
                    "item_type": "journalArticle",
                    "title": "Stale item",
                    "data": {"key": "ITEM0001", "version": 12},
                    "created_at": "2026-01-03T00:00:00+00:00",
                    "updated_at": "2026-01-03T00:00:00+00:00",
                }
            )
            assert not repository.clear_zotero_tombstone(
                source_id="zotero-source",
                object_type="item",
                zotero_key="ITEM0001",
                incoming_version=12,
            )
        state = connection.execute(
            """
            SELECT
              (SELECT deleted_at IS NOT NULL FROM zotero_items),
              (SELECT COUNT(*) FROM zotero_child_items WHERE deleted_at IS NULL),
              (SELECT COUNT(*) FROM zotero_attachments WHERE deleted_at IS NULL),
              (SELECT is_member FROM zotero_profile_items),
              (SELECT status FROM documents),
              (SELECT COUNT(*) FROM zotero_tombstones WHERE zotero_key = 'ITEM0001')
            """
        ).fetchone()
    finally:
        connection.close()

    assert tuple(state) == (1, 0, 0, 0, "missing", 1)


def test_map_run_repository_methods(tmp_path: Path) -> None:
    connection = connect_database(tmp_path)
    try:
        initialize_database(connection)
        repository = Repository(connection, resolve_database_path(tmp_path))
        with connection:
            repository.upsert_corpus(
                "corpus", str(tmp_path), "2026-01-01T00:00:00+00:00"
            )
            connection.execute(
                """
                INSERT INTO documents(
                  id, corpus_id, path, relative_path, file_type, title, sha256,
                  size_bytes, mtime_ns, char_count, status, first_seen_at,
                  last_seen_at, updated_at
                )
                VALUES (
                  'doc_1', 'corpus', 'doc.md', 'doc.md', '.md', 'Doc', 'sha',
                  1, 1, 10, 'active', '2026-01-01T00:00:00+00:00',
                  '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'
                )
                """
            )
            created = repository.save_map_run(
                run_id="map_run_test",
                name="Test Run",
                status="completed",
                similarity_mode="tfidf",
                model_id=None,
                seed=7,
                requested_clusters=None,
                requested_neighbors=3,
                requested_limit=20,
                document_count=1,
                cluster_count=1,
                document_set_signature="abc",
                warnings=[],
                metadata={"phase": 7},
                points=[
                    {
                        "document_id": "doc_1",
                        "x": 1.0,
                        "y": 2.0,
                        "cluster_id": 0,
                        "cluster_label": "Operators",
                        "cluster_signature": "cluster_0",
                        "top_terms": ["operator"],
                        "nearest_neighbors": [],
                    }
                ],
                clusters=[
                    {
                        "cluster_id": 0,
                        "cluster_signature": "cluster_0",
                        "display_label": "Operators",
                        "generated_label": "Operators",
                        "source": "generated",
                        "size": 1,
                        "document_ids": ["doc_1"],
                        "top_terms": [{"term": "operator", "score": 1.0}],
                        "representatives": [],
                        "warnings": [],
                    }
                ],
                now="2026-01-01T00:00:00+00:00",
            )
            rows = repository.list_map_runs()
            full = repository.get_map_run("map_run_test")
            deleted = repository.delete_map_run("map_run_test")
    finally:
        connection.close()

    assert created["id"] == "map_run_test"
    assert rows[0]["name"] == "Test Run"
    assert full is not None
    assert full["points"][0]["top_terms"] == ["operator"]
    assert full["clusters"][0]["display_label"] == "Operators"
    assert deleted is True


def test_sqlite_build_supports_fts5(tmp_path: Path) -> None:
    connection = sqlite3.connect(tmp_path / "fts.sqlite3")
    try:
        connection.execute("CREATE VIRTUAL TABLE probe USING fts5(text)")
    finally:
        connection.close()


def test_validate_model_uses_pydantic_v2_style_api_when_available() -> None:
    class V2Style:
        def __init__(self, value: str, source: str) -> None:
            self.value = value
            self.source = source

        @classmethod
        def model_validate(cls, data: dict[str, object]) -> "V2Style":
            return cls(str(data["value"]), "model_validate")

    result = _validate_model(V2Style, {"value": "ok"})

    assert result.value == "ok"
    assert result.source == "model_validate"


def test_validate_model_falls_back_to_pydantic_v1_style_api() -> None:
    class V1Style:
        def __init__(self, value: str, source: str) -> None:
            self.value = value
            self.source = source

        @classmethod
        def parse_obj(cls, data: dict[str, object]) -> "V1Style":
            return cls(str(data["value"]), "parse_obj")

    result = _validate_model(V1Style, {"value": "ok"})

    assert result.value == "ok"
    assert result.source == "parse_obj"
