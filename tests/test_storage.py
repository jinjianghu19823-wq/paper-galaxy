import sqlite3
from pathlib import Path

from paper_galaxy.config import _validate_model
from paper_galaxy.storage.migrations import initialize_database
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
    assert "documents_fts" in tables

    assert version is not None
    assert version["value"] == "2"


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
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
    finally:
        connection.close()

    assert report_table is not None
    assert version is not None
    assert version["value"] == "2"


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
