from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from paper_galaxy.embeddings.builder import build_embeddings
from paper_galaxy.embeddings.search import semantic_search
from paper_galaxy.embeddings.similarity import compare_neighbors
from paper_galaxy.indexer import index_corpus
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
