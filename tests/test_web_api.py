from __future__ import annotations

import math
from pathlib import Path

from fastapi.testclient import TestClient

from paper_galaxy.indexer import index_corpus
from paper_galaxy.storage.migrations import initialize_database
from paper_galaxy.storage.sqlite import connect_database, resolve_database_path
from paper_galaxy.web.server import create_app
from tests.test_indexer import copy_tiny_corpus, fetch_document


def test_health_and_missing_database_state(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path))

    health = client.get("/api/health")
    stats = client.get("/api/stats")
    map_response = client.get("/api/map")

    assert health.status_code == 200
    assert health.json()["database_exists"] is False
    assert stats.status_code == 200
    assert stats.json()["error"]["code"] == "database_missing"
    assert "paper-galaxy index" in stats.json()["error"]["command"]
    assert map_response.status_code == 200
    assert map_response.json()["points"] == []


def test_existing_empty_database_returns_empty_state(tmp_path: Path) -> None:
    connection = connect_database(tmp_path)
    try:
        initialize_database(connection)
    finally:
        connection.close()
    client = TestClient(create_app(tmp_path))

    stats = client.get("/api/stats").json()
    map_data = client.get("/api/map").json()

    assert stats["database_exists"] is True
    assert stats["stats"]["active_documents"] == 0
    assert map_data["database_exists"] is True
    assert map_data["documents"] == []
    assert map_data["points"] == []
    assert map_data["warnings"]


def test_indexed_database_endpoints_return_map_and_documents(
    tmp_path: Path,
) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    client = TestClient(create_app(tmp_path, seed=7, neighbors=3))

    stats = client.get("/api/stats").json()
    search = client.get("/api/search", params={"q": "neural operator"}).json()
    map_data = client.get("/api/map").json()
    documents = client.get("/api/documents").json()
    first_document_id = documents["documents"][0]["document_id"]
    detail = client.get(f"/api/documents/{first_document_id}").json()

    assert stats["stats"]["active_documents"] == 8
    assert search["results"]
    assert len(map_data["documents"]) == 8
    assert len(map_data["points"]) == 8
    assert map_data["cluster_labels"]
    assert documents["documents"]
    assert detail["metadata"]["document_id"] == first_document_id
    assert detail["chunk_count"] >= 1
    assert detail["chunks"]


def test_map_points_are_finite_and_neighbors_reference_returned_documents(
    tmp_path: Path,
) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    client = TestClient(create_app(tmp_path, seed=11, neighbors=4))

    map_data = client.get("/api/map").json()
    document_ids = {document["document_id"] for document in map_data["documents"]}

    assert all(
        math.isfinite(point["x"]) and math.isfinite(point["y"])
        for point in map_data["points"]
    )
    assert all(label for label in map_data["cluster_labels"].values())
    assert any(point["nearest_neighbors"] for point in map_data["points"])
    for point in map_data["points"]:
        assert point["document_id"] in document_ids
        for neighbor in point["nearest_neighbors"]:
            assert neighbor["document_id"] in document_ids


def test_map_excludes_missing_and_unindexed_documents_and_search_can_include_missing(
    tmp_path: Path,
) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    missing_rel = "neural_operators/fourier_neural_operator.md"
    unindexed_rel = "neural_operators/deep_operator_network.txt"
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    database_path = resolve_database_path(tmp_path)
    missing_id = fetch_document(database_path, missing_rel)["id"]
    unindexed_id = fetch_document(database_path, unindexed_rel)["id"]
    (corpus / missing_rel).unlink()
    (corpus / unindexed_rel).write_text("tiny", encoding="utf-8")
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    client = TestClient(create_app(tmp_path))

    map_data = client.get("/api/map").json()
    default_search = client.get("/api/search", params={"q": "Fourier Neural"}).json()
    missing_search = client.get(
        "/api/search",
        params={"q": "Fourier Neural", "include_missing": "true"},
    ).json()
    unindexed_search = client.get(
        "/api/search",
        params={"q": "Deep networks", "include_missing": "true"},
    ).json()

    map_ids = {document["document_id"] for document in map_data["documents"]}
    assert missing_id not in map_ids
    assert unindexed_id not in map_ids
    assert all(
        result["document_id"] != missing_id for result in default_search["results"]
    )
    assert any(
        result["document_id"] == missing_id for result in missing_search["results"]
    )
    assert all(
        result["document_id"] != unindexed_id for result in unindexed_search["results"]
    )
