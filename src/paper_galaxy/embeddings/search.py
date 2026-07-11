"""Semantic search over locally stored dense vectors."""

from __future__ import annotations

from pathlib import Path

from paper_galaxy.embeddings.builder import (
    EMBEDDING_DISTANCE,
    EMBEDDING_PROVIDER,
    build_document_embedding_text,
    embedding_model_config,
    vector_algorithm_version,
)
from paper_galaxy.embeddings.models import (
    SemanticResultSource,
    SemanticSearchResult,
    stable_embedding_model_id,
    text_sha256,
)
from paper_galaxy.embeddings.ranking import VectorCandidate, blockwise_cosine_top_k
from paper_galaxy.embeddings.sentence_transformers import (
    EmbeddingEncoder,
    load_sentence_transformer,
)
from paper_galaxy.storage.provenance import document_content_revision_sha256
from paper_galaxy.storage.repository import Repository
from paper_galaxy.storage.sqlite import connect_read_only, resolve_database_path


class NoVectorsFoundError(RuntimeError):
    """Raised when a semantic command has no stored vectors to search."""


MAX_SEMANTIC_RESULTS = 100


def semantic_search(
    query: str,
    *,
    project_dir: Path,
    model: str,
    allow_model_download: bool = False,
    object_type: str = "document",
    limit: int = 10,
    include_missing: bool = False,
    normalize: bool = True,
    encoder: EmbeddingEncoder | None = None,
) -> list[SemanticSearchResult]:
    """Search stored local vectors using a query embedding."""

    if not query.strip():
        return []
    if object_type not in {"document", "chunk"}:
        raise ValueError("Semantic object type must be 'document' or 'chunk'.")
    bounded_limit = min(max(0, limit), MAX_SEMANTIC_RESULTS)
    if bounded_limit == 0:
        return []
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
    query_vector = selected_encoder.encode(
        [query],
        batch_size=1,
        normalize=normalize,
    )[0]
    connection = connect_read_only(project_dir)
    try:
        repository = Repository(connection, resolve_database_path(project_dir))
        ranked = blockwise_cosine_top_k(
            query_vector,
            repository.iter_eligible_vector_candidates(
                model_id=model_id,
                model_name=selected_encoder.model_name,
                model_provider=EMBEDDING_PROVIDER,
                model_dimension=selected_encoder.dimension,
                model_distance=EMBEDDING_DISTANCE,
                model_config=model_config,
                object_type=object_type,
                model_fingerprint=selected_encoder.model_fingerprint,
                fingerprint_algorithm=selected_encoder.fingerprint_algorithm,
                algorithm_version=vector_algorithm_version(object_type),
                include_missing=include_missing,
            ),
            limit=bounded_limit,
            invalid="skip",
        )
        if not ranked:
            raise NoVectorsFoundError(
                "No current vectors found for this model. Run paper-galaxy embed first."
            )
        sources = repository.get_semantic_result_sources(
            model_id=model_id,
            model_name=selected_encoder.model_name,
            model_provider=EMBEDDING_PROVIDER,
            model_dimension=selected_encoder.dimension,
            model_distance=EMBEDDING_DISTANCE,
            model_config=model_config,
            model_fingerprint=selected_encoder.model_fingerprint,
            fingerprint_algorithm=selected_encoder.fingerprint_algorithm,
            object_type=object_type,
            object_ids=(item.candidate.object_id for item in ranked),
            include_missing=include_missing,
        )
        results = []
        for item in ranked:
            source = sources.get(item.candidate.object_id)
            if source is None:
                continue
            candidate = item.candidate
            if not _source_matches_candidate(source, candidate):
                continue
            document = source.document
            results.append(
                SemanticSearchResult(
                    rank=0,
                    object_type=object_type,
                    object_id=candidate.object_id,
                    document_id=document.id,
                    title=document.title,
                    relative_path=document.relative_path,
                    file_type=document.file_type,
                    status=document.status,
                    score=item.score,
                    snippet=_snippet(query, source.text),
                    chunk_index=source.chunk_index,
                )
            )
    finally:
        connection.close()
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
        for index, result in enumerate(results)
    ]


def vector_stats(project_dir: Path) -> dict[str, object]:
    """Return JSON-serializable vector statistics for a local project."""

    database_path = resolve_database_path(project_dir)
    if not database_path.is_file():
        return {
            "database_path": str(database_path),
            "models": [],
            "vector_counts": [],
            "last_run": None,
            "vector_indexes": [],
        }
    connection = connect_read_only(project_dir)
    try:
        repository = Repository(connection, database_path)
        return repository.vector_stats()
    finally:
        connection.close()


def _source_matches_candidate(
    source: SemanticResultSource,
    candidate: VectorCandidate,
) -> bool:
    if candidate.chunk_index is None:
        current_source_revision = document_content_revision_sha256(
            title=source.document.title,
            relative_path=source.document.relative_path,
            text=source.text,
        )
        max_chars = candidate.metadata.get("max_document_chars")
        if (
            not isinstance(max_chars, int)
            or isinstance(max_chars, bool)
            or max_chars < 0
        ):
            return False
        current_embedding_input = build_document_embedding_text(
            source.document,
            source.text,
            max_document_chars=max_chars,
        )
    else:
        current_source_revision = text_sha256(source.text)
        max_chars = candidate.metadata.get("max_chunk_chars")
        if (
            not isinstance(max_chars, int)
            or isinstance(max_chars, bool)
            or max_chars < 0
        ):
            return False
        current_embedding_input = source.text[:max_chars]
    return (
        source.source_content_sha256 == current_source_revision
        and source.source_content_sha256 == candidate.source_content_sha256
        and text_sha256(current_embedding_input) == candidate.text_sha256
        and source.vector_id == candidate.vector_id
        and source.vector_text_sha256 == candidate.text_sha256
        and source.vector_updated_at == candidate.updated_at
        and source.vector_blob == candidate.blob
        and source.vector_dimension == candidate.dimension
        and source.vector_dtype == candidate.dtype
        and source.vector_model_fingerprint == candidate.model_fingerprint
        and source.vector_algorithm_version == candidate.algorithm_version
    )


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
