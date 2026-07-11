from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from paper_galaxy.embeddings.builder import build_embeddings
from paper_galaxy.embeddings.search import semantic_search
from paper_galaxy.embeddings.similarity import compare_neighbors
from paper_galaxy.indexer import index_corpus
from paper_galaxy.storage.sqlite import resolve_database_path
from tests.test_indexer import copy_tiny_corpus


@dataclass
class FakeEncoder:
    model_name: str = "fake-local-encoder"
    dimension: int = 3

    def encode(
        self,
        texts: list[str],
        *,
        batch_size: int = 32,
        normalize: bool = True,
    ) -> list[list[float]]:
        del batch_size
        vectors = [_fake_vector(text) for text in texts]
        if not normalize:
            return vectors
        return [_normalize(vector) for vector in vectors]


def test_embedding_builder_embeds_skips_and_forces_vectors(tmp_path: Path) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    encoder = FakeEncoder()

    first = build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="both",
        limit=2,
        encoder=encoder,
    )
    second = build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="both",
        limit=2,
        encoder=encoder,
    )
    forced = build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="document",
        limit=2,
        force=True,
        encoder=encoder,
    )

    assert first.documents_seen == 2
    assert first.documents_embedded == 2
    assert first.chunks_seen == 2
    assert first.chunks_embedded == 2
    assert second.documents_unchanged == 2
    assert second.chunks_unchanged == 2
    assert forced.documents_embedded == 2


def test_semantic_search_uses_stored_document_vectors(tmp_path: Path) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    encoder = FakeEncoder()
    build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="document",
        encoder=encoder,
    )

    results = semantic_search(
        "neural operator",
        project_dir=tmp_path,
        model="unused",
        encoder=encoder,
        limit=5,
    )

    assert results
    assert results[0].score >= 0.99
    assert any("neural_operators" in result.relative_path for result in results)


def test_semantic_search_can_use_unnormalized_vector_identity(tmp_path: Path) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    encoder = FakeEncoder()
    build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="document",
        encoder=encoder,
        normalize=False,
    )

    results = semantic_search(
        "neural operator",
        project_dir=tmp_path,
        model="unused",
        encoder=encoder,
        limit=5,
        normalize=False,
    )

    assert results
    assert results[0].score >= 0.99


def test_compare_neighbors_returns_tfidf_dense_and_hybrid_lists(
    tmp_path: Path,
) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    encoder = FakeEncoder()
    build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="document",
        encoder=encoder,
    )

    comparison = compare_neighbors(
        "neural_operators/fourier_neural_operator.md",
        project_dir=tmp_path,
        model="unused",
        encoder=encoder,
        limit=3,
    )

    assert comparison.target.relative_path.endswith("fourier_neural_operator.md")
    assert comparison.tfidf_neighbors
    assert comparison.dense_neighbors
    assert comparison.hybrid_neighbors


def test_compare_neighbors_can_use_unnormalized_vector_identity(
    tmp_path: Path,
) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    encoder = FakeEncoder()
    build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="document",
        encoder=encoder,
        normalize=False,
    )

    comparison = compare_neighbors(
        "neural_operators/fourier_neural_operator.md",
        project_dir=tmp_path,
        model="unused",
        encoder=encoder,
        limit=3,
        normalize=False,
    )

    assert comparison.dense_neighbors
    assert comparison.hybrid_neighbors


def test_changed_document_prunes_document_chunk_vectors_and_index_metadata(
    tmp_path: Path,
) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    encoder = FakeEncoder()
    build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="both",
        encoder=encoder,
    )
    database_path = resolve_database_path(tmp_path)
    relative_path = "neural_operators/fourier_neural_operator.md"
    with sqlite3.connect(database_path) as connection:
        document_id = connection.execute(
            "SELECT id FROM documents WHERE relative_path = ?",
            (relative_path,),
        ).fetchone()[0]
        model_id = connection.execute("SELECT id FROM embedding_models").fetchone()[0]
        old_chunk_ids = {
            row[0]
            for row in connection.execute(
                "SELECT id FROM chunks WHERE document_id = ?", (document_id,)
            ).fetchall()
        }
        connection.executemany(
            """
            INSERT INTO vector_indexes(
              id, model_id, object_type, index_path, vector_count,
              created_at, metadata_json
            ) VALUES (?, ?, ?, ?, 1, '2026-07-11T00:00:00+00:00', '{}')
            """,
            [
                ("index_document", model_id, "document", "document.index"),
                ("index_chunk", model_id, "chunk", "chunk.index"),
            ],
        )
        connection.commit()

    source = corpus / relative_path
    source.write_text(
        source.read_text(encoding="utf-8")
        + "\n\nA changed synthetic paragraph invalidates old dense vectors.\n",
        encoding="utf-8",
    )
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)

    with sqlite3.connect(database_path) as connection:
        document_vectors = connection.execute(
            """
            SELECT COUNT(*) FROM vectors
            WHERE object_type = 'document' AND object_id = ?
            """,
            (document_id,),
        ).fetchone()[0]
        old_chunk_vectors = connection.execute(
            f"""
            SELECT COUNT(*) FROM vectors
            WHERE object_type = 'chunk'
              AND object_id IN ({", ".join("?" for _ in old_chunk_ids)})
            """,
            tuple(old_chunk_ids),
        ).fetchone()[0]
        remaining_vectors = connection.execute(
            "SELECT COUNT(*) FROM vectors"
        ).fetchone()[0]
        vector_indexes = connection.execute(
            "SELECT COUNT(*) FROM vector_indexes"
        ).fetchone()[0]
    assert document_vectors == 0
    assert old_chunk_vectors == 0
    assert remaining_vectors > 0
    assert vector_indexes == 0


def _fake_vector(text: str) -> list[float]:
    lowered = text.lower()
    if "neural" in lowered or "operator" in lowered:
        return [4.0, 0.0, 0.0]
    if "privacy" in lowered or "local" in lowered:
        return [0.0, 4.0, 0.0]
    return [0.0, 0.0, 4.0]


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector]
