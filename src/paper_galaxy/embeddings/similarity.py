"""Compare TF-IDF, dense, and hybrid document neighbors."""

from __future__ import annotations

from pathlib import Path

from paper_galaxy.embeddings.builder import EMBEDDING_DISTANCE, EMBEDDING_PROVIDER
from paper_galaxy.embeddings.codec import cosine_similarity, decode_vector
from paper_galaxy.embeddings.models import (
    NeighborResult,
    SimilarityComparisonResult,
    stable_embedding_model_id,
)
from paper_galaxy.embeddings.search import NoVectorsFoundError
from paper_galaxy.embeddings.sentence_transformers import (
    EmbeddingEncoder,
    load_sentence_transformer,
)
from paper_galaxy.ml.tfidf import compute_tfidf
from paper_galaxy.records import IndexedDocument
from paper_galaxy.storage.migrations import initialize_database
from paper_galaxy.storage.repository import Repository
from paper_galaxy.storage.sqlite import connect_database, resolve_database_path


def compare_neighbors(
    document_id_or_path: str,
    *,
    project_dir: Path,
    model: str,
    allow_model_download: bool = False,
    limit: int = 10,
    dense_weight: float = 0.65,
    tfidf_weight: float = 0.35,
    encoder: EmbeddingEncoder | None = None,
) -> SimilarityComparisonResult:
    """Compare TF-IDF, dense, and hybrid neighbors for one active document."""

    selected_encoder = encoder or load_sentence_transformer(
        model,
        allow_model_download=allow_model_download,
    )
    model_id = stable_embedding_model_id(
        provider=EMBEDDING_PROVIDER,
        name=selected_encoder.model_name,
        dimension=selected_encoder.dimension,
        distance=EMBEDDING_DISTANCE,
        config={"normalize": True},
    )
    connection = connect_database(project_dir)
    try:
        initialize_database(connection)
        repository = Repository(connection, resolve_database_path(project_dir))
        target = repository.get_document_by_id_or_relative_path(document_id_or_path)
        if target is None:
            raise ValueError(f"No document found for {document_id_or_path}.")
        rows = repository.list_documents_with_text(
            statuses={"active"},
            limit=1_000_000_000,
        )
        documents = [document for document, _ in rows]
        texts = [text for _, text in rows]
        document_map = {document.id: document for document in documents}
        if target.id not in document_map:
            raise ValueError("Document must be active to compare neighbors.")

        tfidf_scores = _tfidf_scores(target.id, documents, texts)
        dense_scores = _dense_scores(repository, model_id, target.id, document_map)
    finally:
        connection.close()

    hybrid_scores = {
        document_id: (dense_weight * dense_scores.get(document_id, 0.0))
        + (tfidf_weight * tfidf_scores.get(document_id, 0.0))
        for document_id in set(tfidf_scores) | set(dense_scores)
    }
    return SimilarityComparisonResult(
        target=target,
        tfidf_neighbors=_rank_neighbors(tfidf_scores, document_map, limit=limit),
        dense_neighbors=_rank_neighbors(dense_scores, document_map, limit=limit),
        hybrid_neighbors=_rank_neighbors(hybrid_scores, document_map, limit=limit),
    )


def _tfidf_scores(
    target_id: str, documents: list[IndexedDocument], texts: list[str]
) -> dict[str, float]:
    _, matrix, _ = compute_tfidf(texts)
    try:
        from sklearn.metrics.pairwise import cosine_similarity as sklearn_cosine
    except ImportError as exc:
        from paper_galaxy.errors import MissingDependencyError

        raise MissingDependencyError("scikit-learn") from exc
    target_index = next(
        index for index, document in enumerate(documents) if document.id == target_id
    )
    similarities = sklearn_cosine(matrix[target_index], matrix).flatten()
    return {
        document.id: float(similarities[index])
        for index, document in enumerate(documents)
        if document.id != target_id
    }


def _dense_scores(
    repository: Repository,
    model_id: str,
    target_id: str,
    documents: dict[str, IndexedDocument],
) -> dict[str, float]:
    vectors = repository.list_vectors(model_id, "document")
    if not vectors:
        raise NoVectorsFoundError(
            "No vectors found for this model. Run paper-galaxy embed first."
        )
    vector_map = {vector.object_id: vector for vector in vectors}
    target_vector = vector_map.get(target_id)
    if target_vector is None:
        raise NoVectorsFoundError(
            "No vector found for the target document. Run paper-galaxy embed first."
        )
    target_values = decode_vector(
        target_vector.vector,
        dimension=target_vector.dimension,
    )
    scores: dict[str, float] = {}
    for document_id, vector in vector_map.items():
        if document_id == target_id or document_id not in documents:
            continue
        values = decode_vector(vector.vector, dimension=vector.dimension)
        scores[document_id] = cosine_similarity(target_values, values)
    return scores


def _rank_neighbors(
    scores: dict[str, float],
    documents: dict[str, IndexedDocument],
    *,
    limit: int,
) -> list[NeighborResult]:
    ordered = sorted(
        (
            (document_id, score)
            for document_id, score in scores.items()
            if document_id in documents
        ),
        key=lambda item: (-item[1], documents[item[0]].relative_path),
    )
    return [
        NeighborResult(
            rank=index + 1,
            document_id=document_id,
            title=documents[document_id].title,
            relative_path=documents[document_id].relative_path,
            score=round(score, 4),
        )
        for index, (document_id, score) in enumerate(ordered[: max(0, limit)])
    ]
