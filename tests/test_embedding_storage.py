from __future__ import annotations

from pathlib import Path

from paper_galaxy.embeddings.codec import encode_vector
from paper_galaxy.embeddings.models import (
    EmbeddingModelRecord,
    VectorRecord,
    stable_embedding_model_id,
    stable_vector_id,
    text_sha256,
)
from paper_galaxy.storage.migrations import initialize_database
from paper_galaxy.storage.repository import Repository
from paper_galaxy.storage.sqlite import connect_database, resolve_database_path


def test_embedding_model_and_vector_upserts_work(tmp_path: Path) -> None:
    connection = connect_database(tmp_path)
    try:
        initialize_database(connection)
        repository = Repository(connection, resolve_database_path(tmp_path))
        model_id = stable_embedding_model_id(
            provider="sentence-transformers",
            name="/models/local",
            dimension=2,
            distance="cosine",
            config={"normalize": True},
        )
        model = EmbeddingModelRecord(
            id=model_id,
            name="/models/local",
            provider="sentence-transformers",
            dimension=2,
            distance="cosine",
            config={"normalize": True},
            created_at="2026-01-01T00:00:00+00:00",
        )
        vector = VectorRecord(
            id=stable_vector_id(model_id, "document", "doc_1"),
            model_id=model_id,
            object_type="document",
            object_id="doc_1",
            text_sha256=text_sha256("hello"),
            dimension=2,
            dtype="float32",
            vector=encode_vector([1.0, 0.0]),
            metadata={"relative_path": "a.md"},
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )

        repository.upsert_embedding_model(model)
        repository.upsert_vector(vector)
        repository.upsert_vector(
            VectorRecord(
                **{
                    **vector.__dict__,
                    "text_sha256": text_sha256("hello again"),
                    "vector": encode_vector([0.0, 1.0]),
                    "updated_at": "2026-01-01T00:00:01+00:00",
                }
            )
        )
        stored_model = repository.get_embedding_model(model_id)
        stored_vector = repository.get_vector(model_id, "document", "doc_1")
        stats = repository.vector_stats()
    finally:
        connection.close()

    assert stored_model is not None
    assert stored_model.id == model_id
    assert stored_vector is not None
    assert stored_vector.text_sha256 == text_sha256("hello again")
    assert len(stats["models"]) == 1
    assert stats["vector_counts"][0]["vector_count"] == 1
