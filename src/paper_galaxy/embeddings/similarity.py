"""Compare TF-IDF, dense, and hybrid document neighbors."""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path

from paper_galaxy.embeddings.builder import (
    EMBEDDING_DISTANCE,
    EMBEDDING_PROVIDER,
    build_document_embedding_text,
    embedding_model_config,
    vector_algorithm_version,
)
from paper_galaxy.embeddings.codec import decode_vector
from paper_galaxy.embeddings.models import (
    NeighborResult,
    SimilarityComparisonResult,
    stable_embedding_model_id,
    text_sha256,
)
from paper_galaxy.embeddings.ranking import VectorCandidate, iter_cosine_scores
from paper_galaxy.embeddings.search import NoVectorsFoundError
from paper_galaxy.embeddings.sentence_transformers import (
    EmbeddingEncoder,
    load_sentence_transformer,
)
from paper_galaxy.ml.tfidf import compute_tfidf
from paper_galaxy.records import IndexedDocument
from paper_galaxy.storage.provenance import document_content_revision_sha256
from paper_galaxy.storage.repository import Repository
from paper_galaxy.storage.sqlite import connect_read_only, resolve_database_path

MAX_NEIGHBOR_RESULTS = 100
MAX_COMPARISON_DOCUMENTS = 20_000
MAX_COMPARISON_SNAPSHOT_ATTEMPTS = 3


def compare_neighbors(
    document_id_or_path: str,
    *,
    project_dir: Path,
    model: str,
    allow_model_download: bool = False,
    limit: int = 10,
    dense_weight: float = 0.65,
    tfidf_weight: float = 0.35,
    normalize: bool = True,
    encoder: EmbeddingEncoder | None = None,
) -> SimilarityComparisonResult:
    """Compare TF-IDF, dense, and hybrid neighbors for one active document."""

    if (
        not math.isfinite(dense_weight)
        or not math.isfinite(tfidf_weight)
        or dense_weight < 0.0
        or tfidf_weight < 0.0
        or not math.isfinite(dense_weight + tfidf_weight)
        or dense_weight + tfidf_weight <= 0.0
    ):
        raise ValueError(
            "Neighbor weights must be finite, non-negative, and not both zero."
        )
    bounded_limit = min(max(0, limit), MAX_NEIGHBOR_RESULTS)
    selected_encoder = encoder or load_sentence_transformer(
        model,
        allow_model_download=allow_model_download,
    )
    model_config = embedding_model_config(selected_encoder, normalize=normalize)
    model_id = stable_embedding_model_id(
        provider=EMBEDDING_PROVIDER,
        name=selected_encoder.model_name,
        dimension=selected_encoder.dimension,
        distance=EMBEDDING_DISTANCE,
        config=model_config,
        model_fingerprint=selected_encoder.model_fingerprint,
        fingerprint_algorithm=selected_encoder.fingerprint_algorithm,
    )
    connection = connect_read_only(project_dir)
    try:
        repository = Repository(connection, resolve_database_path(project_dir))
        for attempt in range(MAX_COMPARISON_SNAPSHOT_ATTEMPTS):
            data_version = _data_version(connection)
            target = repository.get_document_by_id_or_relative_path(document_id_or_path)
            if target is None:
                raise ValueError(f"No document found for {document_id_or_path}.")
            rows = repository.list_documents_with_text(
                statuses={"active"},
                limit=MAX_COMPARISON_DOCUMENTS + 1,
            )
            if len(rows) > MAX_COMPARISON_DOCUMENTS:
                raise ValueError(
                    "Neighbor comparison is limited to 20000 active documents."
                )
            documents = [document for document, _ in rows]
            texts = [text for _, text in rows]
            document_map = {document.id: document for document in documents}
            document_texts = {document.id: text for document, text in rows}
            snapshot_target = document_map.get(target.id)
            if snapshot_target is None:
                if (
                    attempt + 1 < MAX_COMPARISON_SNAPSHOT_ATTEMPTS
                    and _data_version(connection) != data_version
                ):
                    continue
                raise ValueError("Document must be active to compare neighbors.")

            tfidf_scores = _tfidf_scores(snapshot_target.id, documents, texts)
            try:
                dense_scores = _dense_scores(
                    repository,
                    model_id,
                    snapshot_target.id,
                    document_map,
                    document_texts,
                    model_name=selected_encoder.model_name,
                    model_provider=EMBEDDING_PROVIDER,
                    model_dimension=selected_encoder.dimension,
                    model_distance=EMBEDDING_DISTANCE,
                    model_config=model_config,
                    model_fingerprint=selected_encoder.model_fingerprint,
                    fingerprint_algorithm=selected_encoder.fingerprint_algorithm,
                )
            except NoVectorsFoundError:
                if (
                    attempt + 1 < MAX_COMPARISON_SNAPSHOT_ATTEMPTS
                    and _data_version(connection) != data_version
                ):
                    continue
                raise

            hybrid_scores = {
                document_id: (dense_weight * dense_scores.get(document_id, 0.0))
                + (tfidf_weight * tfidf_scores.get(document_id, 0.0))
                for document_id in set(tfidf_scores) | set(dense_scores)
            }
            if any(not math.isfinite(score) for score in hybrid_scores.values()):
                raise ValueError("Hybrid neighbor weights produced a non-finite score.")
            result = SimilarityComparisonResult(
                target=snapshot_target,
                tfidf_neighbors=_rank_neighbors(
                    tfidf_scores, document_map, limit=bounded_limit
                ),
                dense_neighbors=_rank_neighbors(
                    dense_scores, document_map, limit=bounded_limit
                ),
                hybrid_neighbors=_rank_neighbors(
                    hybrid_scores, document_map, limit=bounded_limit
                ),
            )
            if _data_version(connection) == data_version:
                return result
        raise NoVectorsFoundError(
            "The project changed repeatedly during neighbor comparison. "
            "Retry the command after indexing finishes."
        )
    finally:
        connection.close()


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
    document_texts: dict[str, str],
    *,
    model_name: str,
    model_provider: str,
    model_dimension: int,
    model_distance: str,
    model_config: dict[str, object],
    model_fingerprint: str,
    fingerprint_algorithm: str,
) -> dict[str, float]:
    algorithm_version = vector_algorithm_version("document")
    target_candidate = next(
        (
            candidate
            for candidate in repository.iter_eligible_vector_candidates(
                model_id=model_id,
                model_name=model_name,
                model_provider=model_provider,
                model_dimension=model_dimension,
                model_distance=model_distance,
                model_config=model_config,
                object_type="document",
                model_fingerprint=model_fingerprint,
                fingerprint_algorithm=fingerprint_algorithm,
                algorithm_version=algorithm_version,
            )
            if candidate.object_id == target_id
        ),
        None,
    )
    if target_candidate is None:
        raise NoVectorsFoundError(
            "No current vector found for the target document. "
            "Run paper-galaxy embed first."
        )
    target_document = documents.get(target_id)
    if target_document is None or not _candidate_matches_document(
        target_candidate,
        target_document,
        document_texts.get(target_id),
    ):
        raise NoVectorsFoundError(
            "The target vector changed during comparison. Retry the command."
        )
    target_values = decode_vector(
        target_candidate.blob,
        dimension=target_candidate.dimension,
    )
    scores: dict[str, float] = {}
    candidates = repository.iter_eligible_vector_candidates(
        model_id=model_id,
        model_name=model_name,
        model_provider=model_provider,
        model_dimension=model_dimension,
        model_distance=model_distance,
        model_config=model_config,
        object_type="document",
        model_fingerprint=model_fingerprint,
        fingerprint_algorithm=fingerprint_algorithm,
        algorithm_version=algorithm_version,
    )
    for scored in iter_cosine_scores(
        target_values,
        candidates,
        invalid="skip",
    ):
        document_id = scored.candidate.object_id
        document = documents.get(document_id)
        if (
            document_id == target_id
            or document is None
            or not _candidate_matches_document(
                scored.candidate,
                document,
                document_texts.get(document_id),
            )
        ):
            continue
        scores[document_id] = scored.score
    return scores


def _candidate_matches_document(
    candidate: VectorCandidate,
    document: IndexedDocument,
    document_text: str | None,
) -> bool:
    max_chars = candidate.metadata.get("max_document_chars")
    current_revision = (
        document_content_revision_sha256(
            title=document.title,
            relative_path=document.relative_path,
            text=document_text,
        )
        if document_text is not None
        else ""
    )
    if (
        document_text is None
        or not isinstance(max_chars, int)
        or isinstance(max_chars, bool)
        or max_chars < 0
        or document.content_revision_sha256 != current_revision
        or candidate.source_content_sha256 != current_revision
    ):
        return False
    embedding_text = build_document_embedding_text(
        document,
        document_text,
        max_document_chars=max_chars,
    )
    return text_sha256(embedding_text) == candidate.text_sha256


def _data_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("PRAGMA data_version").fetchone()
    if row is None:
        raise RuntimeError("SQLite did not return a data version.")
    return int(row[0])


def _rank_neighbors(
    scores: dict[str, float],
    documents: dict[str, IndexedDocument],
    *,
    limit: int,
) -> list[NeighborResult]:
    bounded_limit = min(max(0, limit), MAX_NEIGHBOR_RESULTS)
    ordered = sorted(
        (
            (document_id, score)
            for document_id, score in scores.items()
            if document_id in documents
        ),
        key=lambda item: (-item[1], documents[item[0]].relative_path, item[0]),
    )
    return [
        NeighborResult(
            rank=index + 1,
            document_id=document_id,
            title=documents[document_id].title,
            relative_path=documents[document_id].relative_path,
            score=round(score, 4),
        )
        for index, (document_id, score) in enumerate(ordered[:bounded_limit])
    ]
