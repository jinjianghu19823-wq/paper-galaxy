import sqlite3
from pathlib import Path

from paper_galaxy.config import _validate_model
from paper_galaxy.storage.migrations import initialize_database
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
    assert "documents_fts" in tables

    assert version is not None
    assert version["value"] == "6"


def test_schema_upgrades_version_one_database_idempotently(tmp_path: Path) -> None:
    connection = connect_database(tmp_path)
    try:
        connection.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('schema_version', '1')"
        )
        initialize_database(connection)
        report_table = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name = 'extraction_reports'
            """
        ).fetchone()
        override_table = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name = 'cluster_label_overrides'
            """
        ).fetchone()
        map_runs_table = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name = 'map_runs'
            """
        ).fetchone()
        zotero_items_table = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name = 'zotero_items'
            """
        ).fetchone()
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
    finally:
        connection.close()

    assert report_table is not None
    assert override_table is not None
    assert map_runs_table is not None
    assert zotero_items_table is not None
    assert version is not None
    assert version["value"] == "6"


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
