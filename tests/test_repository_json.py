from __future__ import annotations

import importlib
import json
import sqlite3
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi.testclient import TestClient

from paper_galaxy.storage.migrations import initialize_database
from paper_galaxy.storage.repository import Repository
from paper_galaxy.storage.sqlite import connect_database, resolve_database_path
from paper_galaxy.web.server import create_app


def _json_storage_module() -> ModuleType:
    return importlib.import_module("paper_galaxy.storage.json")


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        (None, []),
        ([], []),
        (["warning", {"code": "partial"}], ["warning", {"code": "partial"}]),
        ('["warning", {"code": "partial"}]', ["warning", {"code": "partial"}]),
    ],
)
def test_load_json_list_distinguishes_sql_null_and_valid_lists(
    stored: object,
    expected: list[object],
) -> None:
    loader = _json_storage_module().load_json_list

    assert loader(stored) == expected


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        (None, {}),
        ({}, {}),
        ({"full": False, "filters": ["tag"]}, {"full": False, "filters": ["tag"]}),
        (
            '{"filters": ["tag"], "full": false}',
            {"full": False, "filters": ["tag"]},
        ),
    ],
)
def test_load_json_object_distinguishes_sql_null_and_valid_objects(
    stored: object,
    expected: dict[str, object],
) -> None:
    loader = _json_storage_module().load_json_object

    assert loader(stored) == expected


@pytest.mark.parametrize(
    ("loader_name", "stored", "expected_code"),
    [
        ("load_json_list", "[", "invalid_json"),
        ("load_json_object", "{", "invalid_json"),
        ("load_json_list", "{}", "json_type_mismatch"),
        ("load_json_list", "null", "json_type_mismatch"),
        ("load_json_object", "[]", "json_type_mismatch"),
        ("load_json_object", "42", "json_type_mismatch"),
        ("load_json_list", {}, "json_type_mismatch"),
        ("load_json_object", [], "json_type_mismatch"),
        ("load_json_list", "[NaN]", "invalid_json"),
        ("load_json_object", '{"x": Infinity}', "invalid_json"),
        ("load_json_object", '{"x": 1, "x": 2}', "invalid_json"),
        ("load_json_list", [float("-inf")], "invalid_json"),
        ("load_json_list", [{1: "not-json"}], "json_type_mismatch"),
        ("load_json_object", {"nested": {1, 2}}, "json_type_mismatch"),
    ],
)
def test_json_loaders_reject_invalid_json_and_wrong_top_level_types(
    loader_name: str,
    stored: object,
    expected_code: str,
) -> None:
    loader = getattr(_json_storage_module(), loader_name)

    with pytest.raises(ValueError) as caught:
        loader(stored)

    assert type(caught.value) is not ValueError
    assert caught.value.code == expected_code  # type: ignore[attr-defined]


def _repository(tmp_path: Path) -> tuple[sqlite3.Connection, Repository]:
    connection = connect_database(tmp_path)
    initialize_database(connection)
    return connection, Repository(connection, resolve_database_path(tmp_path))


def _insert_zotero_source(repository: Repository, tmp_path: Path) -> None:
    repository.upsert_zotero_source(
        {
            "id": "source_local",
            "source_type": "sqlite",
            "data_dir": str(tmp_path / "private" / "Zotero"),
            "name": "Local Zotero",
            "created_at": "2026-07-11T00:00:00+00:00",
            "updated_at": "2026-07-11T00:00:00+00:00",
        }
    )


def test_zotero_import_status_decodes_warnings_and_config_json(
    tmp_path: Path,
) -> None:
    connection, repository = _repository(tmp_path)
    try:
        with connection:
            _insert_zotero_source(repository, tmp_path)
            repository.create_zotero_import_run(
                "run_1",
                "source_local",
                started_at="2026-07-11T00:00:00+00:00",
                config={
                    "full": False,
                    "filters": {"collections": ["Research"], "tags": ["todo"]},
                },
            )
            repository.finish_zotero_import_run(
                "run_1",
                finished_at="2026-07-11T00:00:01+00:00",
                status="completed",
                items_seen=2,
                items_imported=1,
                items_updated=1,
                items_unchanged=0,
                attachments_seen=0,
                attachments_resolved=0,
                pdfs_extracted=0,
                notes_imported=0,
                skipped=0,
                warnings=["metadata only", "one attachment was missing"],
            )

        status = repository.zotero_import_status()
    finally:
        connection.close()

    assert status is not None
    assert status["warnings"] == [
        "metadata only",
        "one attachment was missing",
    ]
    assert status["config"] == {
        "filters": {"collections": ["Research"], "tags": ["todo"]},
        "full": False,
    }


def test_invalid_persisted_zotero_json_is_not_silently_treated_as_empty(
    tmp_path: Path,
) -> None:
    connection, repository = _repository(tmp_path)
    try:
        with connection:
            _insert_zotero_source(repository, tmp_path)
            repository.create_zotero_import_run(
                "run_bad_json",
                "source_local",
                started_at="2026-07-11T00:00:00+00:00",
                config={},
            )
            connection.execute(
                "UPDATE zotero_import_runs SET warnings_json = '[' WHERE id = ?",
                ("run_bad_json",),
            )

        with pytest.raises(ValueError) as caught:
            repository.zotero_import_status()
    finally:
        connection.close()

    assert caught.value.code == "invalid_json"  # type: ignore[attr-defined]


def test_ordinary_zotero_payload_filters_private_raw_data(
    tmp_path: Path,
) -> None:
    connection, repository = _repository(tmp_path)
    private_path = str(tmp_path / "private" / "Zotero" / "storage" / "paper.pdf")
    private_marker = "PRIVATE_RAW_ZOTERO_MARKER"
    try:
        with connection:
            _insert_zotero_source(repository, tmp_path)
            repository.upsert_zotero_item(
                {
                    "id": "zotero_item_1",
                    "source_id": "source_local",
                    "zotero_key": "AAAA1111",
                    "version": 7,
                    "item_type": "journalArticle",
                    "title": "Synthetic paper",
                    "reading_status": "to_read",
                    "data": {
                        "private_marker": private_marker,
                        "attachmentPath": private_path,
                        "nested": {"raw": "must not leave storage"},
                    },
                    "created_at": "2026-07-11T00:00:00+00:00",
                    "updated_at": "2026-07-11T00:00:00+00:00",
                }
            )
            repository.upsert_zotero_attachment(
                {
                    "id": "attachment_1",
                    "source_id": "source_local",
                    "parent_zotero_item_id": "zotero_item_1",
                    "zotero_key": "ATTACH11",
                    "title": "Local PDF",
                    "filename": "paper.pdf",
                    "content_type": "application/pdf",
                    "link_mode": "imported_file",
                    "zotero_path": private_path,
                    "resolved_path": private_path,
                    "path_status": "resolved",
                    "data": {"private_marker": private_marker},
                    "created_at": "2026-07-11T00:00:00+00:00",
                    "updated_at": "2026-07-11T00:00:00+00:00",
                }
            )

        item_by_key = repository.get_zotero_item_by_source_key(
            "source_local", "AAAA1111"
        )
        listed_item = repository.list_zotero_items()[0]
        detailed_item = repository.get_zotero_item_detail("zotero_item_1")
    finally:
        connection.close()

    assert item_by_key is not None
    assert detailed_item is not None
    for payload in (item_by_key, listed_item, detailed_item):
        assert "data" not in payload
        serialized = json.dumps(payload, sort_keys=True)
        assert private_marker not in serialized
        assert private_path not in serialized
        assert str(tmp_path) not in serialized
    for attachment in detailed_item["attachments"]:
        assert "zotero_path" not in attachment
        assert "resolved_path" not in attachment

    response = TestClient(create_app(tmp_path)).get("/api/zotero/item/zotero_item_1")
    assert response.status_code == 200
    response_text = response.text
    assert private_marker not in response_text
    assert private_path not in response_text
    assert str(tmp_path) not in response_text


def test_private_zotero_raw_json_remains_available_only_in_storage(
    tmp_path: Path,
) -> None:
    connection, repository = _repository(tmp_path)
    raw_payload: dict[str, Any] = {
        "private_marker": "STORAGE_ONLY_MARKER",
        "local_path": str(tmp_path / "private.pdf"),
    }
    try:
        with connection:
            _insert_zotero_source(repository, tmp_path)
            repository.upsert_zotero_item(
                {
                    "id": "zotero_item_1",
                    "source_id": "source_local",
                    "zotero_key": "AAAA1111",
                    "item_type": "journalArticle",
                    "title": "Synthetic paper",
                    "data": raw_payload,
                    "created_at": "2026-07-11T00:00:00+00:00",
                    "updated_at": "2026-07-11T00:00:00+00:00",
                }
            )
        row = connection.execute(
            "SELECT data_json FROM zotero_items WHERE id = 'zotero_item_1'"
        ).fetchone()
    finally:
        connection.close()

    assert row is not None
    assert json.loads(str(row["data_json"])) == raw_payload


def test_vector_stats_api_does_not_expose_local_model_or_index_paths(
    tmp_path: Path,
) -> None:
    connection, _repository_instance = _repository(tmp_path)
    private_model = str(tmp_path / "private" / "models" / "local-model")
    private_index = str(tmp_path / "private" / "vectors" / "model.index")
    private_marker = "PRIVATE_VECTOR_CONFIG_MARKER"
    try:
        with connection:
            connection.execute(
                """
                INSERT INTO embedding_models(
                  id, name, provider, dimension, distance, config_json, created_at
                ) VALUES ('model', ?, 'local', 2, 'cosine', ?, ?)
                """,
                (
                    private_model,
                    json.dumps({"path": private_model, "marker": private_marker}),
                    "2026-07-11T00:00:00+00:00",
                ),
            )
            connection.execute(
                """
                INSERT INTO embedding_runs(
                  id, model_id, started_at, status, config_json
                ) VALUES ('run', 'model', ?, 'completed', ?)
                """,
                (
                    "2026-07-11T00:00:00+00:00",
                    json.dumps({"path": private_model, "marker": private_marker}),
                ),
            )
            connection.execute(
                """
                INSERT INTO vector_indexes(
                  id, model_id, object_type, index_path, vector_count,
                  created_at, metadata_json
                ) VALUES ('index', 'model', 'document', ?, 0, ?, ?)
                """,
                (
                    private_index,
                    "2026-07-11T00:00:00+00:00",
                    json.dumps({"path": private_index, "marker": private_marker}),
                ),
            )
    finally:
        connection.close()

    response = TestClient(create_app(tmp_path)).get("/api/vector-stats")

    assert response.status_code == 200
    assert response.json()["vector_stats"]["models"][0]["name"] == "local-model"
    assert str(tmp_path) not in response.text
    assert private_marker not in response.text


def test_web_status_and_saved_maps_filter_legacy_private_json(
    tmp_path: Path,
) -> None:
    connection, repository = _repository(tmp_path)
    private_path = str(tmp_path / "private" / "Zotero" / "storage")
    private_marker = "LEGACY_PRIVATE_JSON_MARKER"
    try:
        with connection:
            _insert_zotero_source(repository, tmp_path)
            repository.create_zotero_import_run(
                "run_private",
                "source_local",
                started_at="2026-07-11T00:00:00+00:00",
                config={"data_dir": private_path, "marker": private_marker},
            )
            repository.finish_zotero_import_run(
                "run_private",
                finished_at="2026-07-11T00:00:01+00:00",
                status="failed",
                items_seen=0,
                items_imported=0,
                items_updated=0,
                items_unchanged=0,
                attachments_seen=0,
                attachments_resolved=0,
                pdfs_extracted=0,
                notes_imported=0,
                skipped=0,
                warnings=[f"legacy attachment failed at {private_path}"],
            )
            connection.execute(
                """
                INSERT INTO map_runs(
                  id, name, created_at, status, similarity_mode, seed,
                  requested_neighbors, requested_limit, document_count,
                  cluster_count, document_set_signature, warnings_json,
                  metadata_json
                ) VALUES (
                  'map_private', 'Safe map', ?, 'completed', 'tfidf', 42,
                  5, 100, 0, 0, 'signature', ?, ?
                )
                """,
                (
                    "2026-07-11T00:00:00+00:00",
                    json.dumps([f"legacy map warning at {private_path}"]),
                    json.dumps({"source_path": private_path, "marker": private_marker}),
                ),
            )
    finally:
        connection.close()

    client = TestClient(create_app(tmp_path))
    responses = [
        client.get("/api/zotero/status"),
        client.get("/api/zotero/reading-map"),
        client.get("/api/map-runs"),
        client.get("/api/map-runs/map_private"),
    ]

    for response in responses:
        assert response.status_code == 200
        assert str(tmp_path) not in response.text
        assert private_path not in response.text
        assert private_marker not in response.text
