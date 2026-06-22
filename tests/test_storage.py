import sqlite3
from pathlib import Path

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
    finally:
        connection.close()

    assert "schema_meta" in tables
    assert "documents" in tables
    assert "chunks" in tables
    assert "scan_runs" in tables
    assert "documents_fts" in tables


def test_sqlite_build_supports_fts5(tmp_path: Path) -> None:
    connection = sqlite3.connect(tmp_path / "fts.sqlite3")
    try:
        connection.execute("CREATE VIRTUAL TABLE probe USING fts5(text)")
    finally:
        connection.close()
