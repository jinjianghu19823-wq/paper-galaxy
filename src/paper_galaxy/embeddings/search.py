"""Semantic search over locally stored dense vectors."""

from __future__ import annotations

from pathlib import Path

from paper_galaxy.embeddings.builder import EMBEDDING_DISTANCE, EMBEDDING_PROVIDER
from paper_galaxy.embeddings.codec import cosine_similarity, decode_vector
from paper_galaxy.embeddings.models import (
    SemanticSearchResult,
    VectorRecord,
    stable_embedding_model_id,
)
from paper_galaxy.embeddings.sentence_transformers import (
    EmbeddingEncoder,
    load_sentence_transformer,
)
from paper_galaxy.records import IndexedChunk, IndexedDocument
from paper_galaxy.storage.migrations import initialize_database
from paper_galaxy.storage.repository import Repository
from paper_galaxy.storage.sqlite import connect_database, resolve_database_path


class NoVectorsFoundError(RuntimeError):
    """Raised when a semantic command has no stored vectors to search."""


def semantic_search(
    query: str,
    *,
    project_dir: Path,
    model: str,
    allow_model_download: bool = False,
    object_type: str = "document",
    limit: int = 10,
    include_missing: bool = False,
    encoder: EmbeddingEncoder | None = None,
) -> list[SemanticSearchResult]:
    """Search stored local vectors using a query embedding."""

    if not query.strip():
        return []
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
    query_vector = selected_encoder.encode(
        [query],
        batch_size=1,
        normalize=True,
    )[0]
    connection = connect_database(project_dir)
    try:
        initialize_database(connection)
        repository = Repository(connection, resolve_database_path(project_dir))
        vectors = repository.list_vectors(model_id, object_type)
        if not vectors:
            raise NoVectorsFoundError(
                "No vectors found for this model. Run paper-galaxy embed first."
            )
        if object_type == "document":
            results = _document_results(
                repository,
                query,
                query_vector,
                vectors,
                include_missing=include_missing,
            )
        else:
            results = _chunk_results(repository, query, query_vector, vectors)
    finally:
        connection.close()
    results.sort(
        key=lambda item: (-item.score, item.relative_path, item.chunk_index or -1)
    )
    return [
        SemanticSearchResult(
            rank=index + 1,
            object_type=result.object_type,
            object_id=result.object_id,
            document_id=result.document_id,
            title=result.title,
            relative_path=result.relative_path,
            file_type=result.file_type,
            status=result.status,
            score=round(result.score, 4),
            snippet=result.snippet,
            chunk_index=result.chunk_index,
        )
        for index, result in enumerate(results[: max(0, limit)])
    ]


def vector_stats(project_dir: Path) -> dict[str, object]:
    """Return JSON-serializable vector statistics for a local project."""

    connection = connect_database(project_dir)
    try:
        initialize_database(connection)
        repository = Repository(connection, resolve_database_path(project_dir))
        return repository.vector_stats()
    finally:
        connection.close()


def _document_results(
    repository: Repository,
    query: str,
    query_vector: list[float],
    vectors: list[VectorRecord],
    *,
    include_missing: bool,
) -> list[SemanticSearchResult]:
    results: list[SemanticSearchResult] = []
    allowed_statuses = {"active", "missing"} if include_missing else {"active"}
    for vector in vectors:
        document = repository.get_document(vector.object_id)
        if document is None or document.status not in allowed_statuses:
            continue
        stored = decode_vector(vector.vector, dimension=vector.dimension)
        score = cosine_similarity(query_vector, stored)
        text = repository.get_document_text(document.id) or ""
        results.append(
            SemanticSearchResult(
                rank=0,
                object_type=vector.object_type,
                object_id=vector.object_id,
                document_id=document.id,
                title=document.title,
                relative_path=document.relative_path,
                file_type=document.file_type,
                status=document.status,
                score=score,
                snippet=_snippet(query, text),
            )
        )
    return results


def _chunk_results(
    repository: Repository,
    query: str,
    query_vector: list[float],
    vectors: list[VectorRecord],
) -> list[SemanticSearchResult]:
    chunk_rows = repository.list_chunks_with_documents(
        statuses={"active"},
        limit=1_000_000_000,
    )
    chunk_map: dict[str, tuple[IndexedDocument, IndexedChunk]] = {
        chunk.id: (document, chunk) for document, chunk in chunk_rows
    }
    results: list[SemanticSearchResult] = []
    for vector in vectors:
        pair = chunk_map.get(vector.object_id)
        if pair is None:
            continue
        document, chunk = pair
        stored = decode_vector(vector.vector, dimension=vector.dimension)
        score = cosine_similarity(query_vector, stored)
        results.append(
            SemanticSearchResult(
                rank=0,
                object_type=vector.object_type,
                object_id=vector.object_id,
                document_id=document.id,
                title=document.title,
                relative_path=document.relative_path,
                file_type=document.file_type,
                status=document.status,
                score=score,
                snippet=_snippet(query, chunk.text),
                chunk_index=chunk.chunk_index,
            )
        )
    return results


def _snippet(query: str, text: str, *, limit: int = 240) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    query_terms = [term.lower() for term in query.split() if term.strip()]
    lowered = compact.lower()
    hit_index = min(
        (lowered.find(term) for term in query_terms if lowered.find(term) >= 0),
        default=0,
    )
    start = max(0, hit_index - limit // 3)
    end = min(len(compact), start + limit)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(compact) else ""
    return prefix + compact[start:end].strip() + suffix
