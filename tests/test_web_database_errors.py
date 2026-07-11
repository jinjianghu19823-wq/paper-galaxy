from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from paper_galaxy.storage.migrations import initialize_database
from paper_galaxy.storage.sqlite import connect_database, resolve_database_path
from paper_galaxy.web.server import create_app


def _initialize(project_dir: Path) -> Path:
    connection = connect_database(project_dir)
    try:
        initialize_database(connection)
    finally:
        connection.close()
    return resolve_database_path(project_dir)


def test_web_returns_structured_error_for_missing_schema_table(tmp_path: Path) -> None:
    database_path = _initialize(tmp_path)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("DROP TABLE documents")
        connection.commit()
    finally:
        connection.close()

    response = TestClient(create_app(tmp_path)).get("/api/documents")

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "unsupported_schema"
    assert "cannot be migrated safely" in response.json()["error"]["message"]
    assert str(tmp_path) not in response.text


def test_web_returns_structured_error_for_invalid_stored_json(tmp_path: Path) -> None:
    database_path = _initialize(tmp_path)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            """
            INSERT INTO zotero_sources(
              id, source_type, name, created_at, updated_at
            ) VALUES ('source', 'local_api', 'Synthetic', 'now', 'now')
            """
        )
        connection.execute(
            """
            INSERT INTO zotero_import_runs(
              id, source_id, started_at, status, warnings_json, config_json
            ) VALUES ('run', 'source', 'now', 'failed', '[', '{}')
            """
        )
        connection.commit()
    finally:
        connection.close()

    response = TestClient(create_app(tmp_path)).get("/api/zotero/status")

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "invalid_json"
    assert str(tmp_path) not in response.text
