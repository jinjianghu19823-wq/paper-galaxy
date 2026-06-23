from __future__ import annotations

import math
from pathlib import Path

from fastapi.testclient import TestClient

from paper_galaxy.embeddings.builder import build_embeddings
from paper_galaxy.indexer import index_corpus
from paper_galaxy.maps import build_and_store_map_run
from paper_galaxy.storage.migrations import initialize_database
from paper_galaxy.storage.sqlite import connect_database, resolve_database_path
from paper_galaxy.web.server import create_app
from tests.test_embedding_builder_search import FakeEncoder
from tests.test_indexer import copy_tiny_corpus, fetch_document


def test_health_and_missing_database_state(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path))

    health = client.get("/api/health")
    stats = client.get("/api/stats")
    map_response = client.get("/api/map")
    clusters_response = client.get("/api/clusters")

    assert health.status_code == 200
    assert health.json()["database_exists"] is False
    assert stats.status_code == 200
    assert stats.json()["error"]["code"] == "database_missing"
    assert "paper-galaxy index" in stats.json()["error"]["command"]
    assert map_response.status_code == 200
    assert map_response.json()["points"] == []
    assert clusters_response.json()["clusters"] == []
    assert client.get("/api/vector-stats").json()["vector_stats"]["models"] == []


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
    assert map_data["clusters"]
    assert all(point["cluster_signature"] for point in map_data["points"])
    assert documents["documents"]
    assert detail["metadata"]["document_id"] == first_document_id
    assert detail["chunk_count"] >= 1
    assert detail["chunks"]


def test_vector_stats_endpoint_reports_existing_vectors(tmp_path: Path) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="document",
        limit=2,
        encoder=FakeEncoder(),
    )
    client = TestClient(create_app(tmp_path))

    payload = client.get("/api/vector-stats").json()

    assert payload["database_exists"] is True
    assert payload["vector_stats"]["models"]
    assert payload["vector_stats"]["vector_counts"][0]["vector_count"] == 2


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


def test_saved_map_run_api_endpoints(tmp_path: Path) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    saved = build_and_store_map_run(project_dir=tmp_path, name="API run")
    run = saved["map_run"]
    assert isinstance(run, dict)
    client = TestClient(create_app(tmp_path))

    runs = client.get("/api/map-runs").json()
    saved_map = client.get("/api/map", params={"run_id": run["id"]}).json()
    detail = client.get(f"/api/map-runs/{run['id']}").json()
    missing = client.get("/api/map", params={"run_id": "missing"})

    assert runs["database_exists"] is True
    assert runs["map_runs"][0]["name"] == "API run"
    assert saved_map["map_run"]["id"] == run["id"]
    assert len(saved_map["points"]) == 8
    assert detail["map_run"]["id"] == run["id"]
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "map_run_not_found"


def test_cluster_endpoints_rename_reset_and_reject_bad_labels(
    tmp_path: Path,
) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    client = TestClient(create_app(tmp_path, seed=11, neighbors=4))
    map_data = client.get("/api/map").json()
    signature = map_data["clusters"][0]["cluster_signature"]

    bad_empty = client.put(f"/api/clusters/{signature}/label", json={"label": "  "})
    bad_long = client.put(
        f"/api/clusters/{signature}/label",
        json={"label": "x" * 121},
    )
    renamed = client.put(
        f"/api/clusters/{signature}/label",
        json={"label": "Neural Operators"},
    )
    clusters = client.get("/api/clusters").json()
    updated_map = client.get("/api/map").json()
    reset = client.delete(f"/api/clusters/{signature}/label")
    reset_clusters = client.get("/api/clusters").json()

    assert bad_empty.status_code == 422
    assert bad_long.status_code == 422
    assert renamed.status_code == 200
    assert any(
        cluster["display_label"] == "Neural Operators" and cluster["source"] == "manual"
        for cluster in clusters["clusters"]
    )
    assert any(
        point["cluster_signature"] == signature
        and point["cluster_label"] == "Neural Operators"
        for point in updated_map["points"]
    )
    assert reset.status_code == 200
    assert any(
        cluster["cluster_signature"] == signature and cluster["source"] == "generated"
        for cluster in reset_clusters["clusters"]
    )


def test_pair_explain_endpoint_handles_good_and_bad_document_ids(
    tmp_path: Path,
) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    client = TestClient(create_app(tmp_path))
    map_data = client.get("/api/map").json()
    point = next(point for point in map_data["points"] if point["nearest_neighbors"])
    neighbor = point["nearest_neighbors"][0]

    response = client.get(
        "/api/explain/pair",
        params={
            "source": point["document_id"],
            "target": neighbor["document_id"],
            "term_limit": 5,
            "chunk_limit": 2,
        },
    )
    missing = client.get(
        "/api/explain/pair",
        params={"source": "missing", "target": neighbor["document_id"]},
    )
    invalid = client.get("/api/explain/pair", params={"source": point["document_id"]})

    assert response.status_code == 200
    payload = response.json()["explanation"]
    assert payload["shared_terms"]
    assert payload["chunk_matches"]
    assert "source_excerpt" in payload["chunk_matches"][0]
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "document_not_found"
    assert invalid.status_code == 422


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
