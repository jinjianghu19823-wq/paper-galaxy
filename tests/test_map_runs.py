import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from paper_galaxy.indexer import index_corpus
from paper_galaxy.maps import (
    build_and_store_map_run,
    export_map_run,
    persisted_map_payload,
)
from paper_galaxy.storage.repository import Repository
from paper_galaxy.storage.sqlite import connect_database, resolve_database_path
from paper_galaxy.web.server import create_app
from tests.test_indexer import copy_tiny_corpus


def test_build_persist_read_and_export_map_run(tmp_path: Path) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)

    built = build_and_store_map_run(
        project_dir=tmp_path,
        name="Tiny map",
        seed=13,
        neighbors=3,
        limit=20,
    )
    run = built["map_run"]
    assert isinstance(run, dict)
    run_id = str(run["id"])
    persisted = persisted_map_payload(project_dir=tmp_path, run_id=run_id)
    output = export_map_run(
        project_dir=tmp_path,
        run_id=run_id,
        output_path=tmp_path / "map-run.json",
    )

    assert run["name"] == "Tiny map"
    assert len(persisted["points"]) == 8
    assert len(persisted["documents"]) == 8
    assert persisted["map_run"]["similarity_mode"] == "tfidf"
    assert output.exists()
    exported = output.read_text(encoding="utf-8")
    assert "text_preview" not in exported
    assert "chunk_count" not in exported


def test_saved_map_run_delete_cascades_points(tmp_path: Path) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    run = build_and_store_map_run(project_dir=tmp_path, name="Delete me")["map_run"]
    assert isinstance(run, dict)
    connection = connect_database(tmp_path)
    try:
        repository = Repository(connection, resolve_database_path(tmp_path))
        with connection:
            assert repository.delete_map_run(str(run["id"])) is True
        assert repository.list_map_run_points(str(run["id"])) == []
    finally:
        connection.close()


def test_saved_map_api_and_export_deep_filter_persisted_private_json(
    tmp_path: Path,
) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    saved = build_and_store_map_run(project_dir=tmp_path, name="Private JSON test")
    run = saved["map_run"]
    assert isinstance(run, dict)
    run_id = str(run["id"])
    private_path = str(tmp_path / "private" / "paper.pdf")
    private_marker = "PRIVATE_PERSISTED_MAP_MARKER"
    database_path = resolve_database_path(tmp_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            UPDATE map_runs
            SET warnings_json = ?, metadata_json = ?
            WHERE id = ?
            """,
            (
                json.dumps([f"warning at {private_path}"]),
                json.dumps({"raw": private_marker, "resolved_path": private_path}),
                run_id,
            ),
        )
        connection.execute(
            """
            UPDATE map_run_points
            SET top_terms_json = ?, nearest_neighbors_json = ?
            WHERE map_run_id = ?
            """,
            (
                json.dumps(["safe-term", {"raw": private_marker}]),
                json.dumps(
                    [
                        {
                            "document_id": "doc_safe_neighbor",
                            "title": "Safe neighbor",
                            "relative_path": "safe/neighbor.md",
                            "score": 0.75,
                            "resolved_path": private_path,
                            "raw": private_marker,
                        }
                    ]
                ),
                run_id,
            ),
        )
        connection.execute(
            """
            UPDATE map_run_clusters
            SET document_ids_json = ?, top_terms_json = ?,
                representatives_json = ?, warnings_json = ?
            WHERE map_run_id = ?
            """,
            (
                json.dumps(["doc_safe", private_path]),
                json.dumps([{"term": "safe", "score": 1.0, "raw": private_marker}]),
                json.dumps(
                    [
                        {
                            "document_id": "doc_safe",
                            "title": "Safe representative",
                            "relative_path": "safe/paper.md",
                            "score": 1.0,
                            "resolved_path": private_path,
                            "raw": private_marker,
                        }
                    ]
                ),
                json.dumps([private_marker, private_path]),
                run_id,
            ),
        )
        connection.commit()

    response = TestClient(create_app(tmp_path)).get(f"/api/map-runs/{run_id}")
    output = export_map_run(
        project_dir=tmp_path,
        run_id=run_id,
        output_path=tmp_path / "safe-map-export.json",
    )

    assert response.status_code == 200
    for serialized in (response.text, output.read_text(encoding="utf-8")):
        assert private_marker not in serialized
        assert private_path not in serialized
        assert "resolved_path" not in serialized
        assert '"raw"' not in serialized
        assert "safe/neighbor.md" in serialized
        assert "safe/paper.md" in serialized


def test_non_tfidf_saved_map_run_is_rejected(tmp_path: Path) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)

    try:
        build_and_store_map_run(project_dir=tmp_path, similarity_mode="dense")
    except ValueError as exc:
        assert "tfidf" in str(exc)
    else:
        raise AssertionError("Expected dense map run to be rejected.")
