from pathlib import Path

from paper_galaxy.indexer import index_corpus
from paper_galaxy.maps import (
    build_and_store_map_run,
    export_map_run,
    persisted_map_payload,
)
from paper_galaxy.storage.repository import Repository
from paper_galaxy.storage.sqlite import connect_database, resolve_database_path
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


def test_non_tfidf_saved_map_run_is_rejected(tmp_path: Path) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)

    try:
        build_and_store_map_run(project_dir=tmp_path, similarity_mode="dense")
    except ValueError as exc:
        assert "tfidf" in str(exc)
    else:
        raise AssertionError("Expected dense map run to be rejected.")
