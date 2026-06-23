"""Build optional local document and chunk embeddings."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from paper_galaxy.embeddings.codec import FLOAT32_DTYPE, encode_vector
from paper_galaxy.embeddings.models import (
    EmbeddingModelRecord,
    EmbeddingRunSummary,
    VectorRecord,
    stable_embedding_model_id,
    stable_vector_id,
    text_sha256,
)
from paper_galaxy.embeddings.sentence_transformers import (
    EmbeddingEncoder,
    load_sentence_transformer,
)
from paper_galaxy.records import IndexedChunk, IndexedDocument
from paper_galaxy.storage.migrations import initialize_database
from paper_galaxy.storage.repository import Repository
from paper_galaxy.storage.sqlite import connect_database, resolve_database_path

DOCUMENT_OBJECT = "document"
CHUNK_OBJECT = "chunk"
BOTH_OBJECTS = "both"
EMBEDDING_PROVIDER = "sentence-transformers"
EMBEDDING_DISTANCE = "cosine"


@dataclass(frozen=True)
class _EmbeddingPayload:
    object_type: str
    object_id: str
    text: str
    metadata: dict[str, object]


def build_embeddings(
    *,
    project_dir: Path,
    model: str,
    allow_model_download: bool = False,
    object_type: str = BOTH_OBJECTS,
    limit: int | None = None,
    force: bool = False,
    batch_size: int = 32,
    max_document_chars: int = 8000,
    max_chunk_chars: int = 2000,
    normalize: bool = True,
    encoder: EmbeddingEncoder | None = None,
) -> EmbeddingRunSummary:
    """Build vectors for active indexed documents and/or chunks."""

    resolved_project_dir = project_dir.expanduser().resolve()
    selected_encoder = encoder or load_sentence_transformer(
        model,
        allow_model_download=allow_model_download,
    )
    model_config = {"normalize": normalize}
    model_id = stable_embedding_model_id(
        provider=EMBEDDING_PROVIDER,
        name=selected_encoder.model_name,
        dimension=selected_encoder.dimension,
        distance=EMBEDDING_DISTANCE,
        config=model_config,
    )
    now = _utc_now()
    run_id = f"embed_run_{uuid4().hex[:16]}"
    database_path = resolve_database_path(resolved_project_dir)
    connection = connect_database(resolved_project_dir)
    documents_seen = 0
    documents_embedded = 0
    documents_unchanged = 0
    chunks_seen = 0
    chunks_embedded = 0
    chunks_unchanged = 0
    finished_at = now
    try:
        initialize_database(connection)
        repository = Repository(connection, database_path)
        with repository.connection:
            repository.upsert_embedding_model(
                EmbeddingModelRecord(
                    id=model_id,
                    name=selected_encoder.model_name,
                    provider=EMBEDDING_PROVIDER,
                    dimension=selected_encoder.dimension,
                    distance=EMBEDDING_DISTANCE,
                    config=model_config,
                    created_at=now,
                )
            )
            repository.create_embedding_run(
                run_id,
                model_id,
                started_at=now,
                config={
                    "object_type": object_type,
                    "limit": limit,
                    "force": force,
                    "batch_size": batch_size,
                    "max_document_chars": max_document_chars,
                    "max_chunk_chars": max_chunk_chars,
                    "normalize": normalize,
                },
            )

            if object_type in {DOCUMENT_OBJECT, BOTH_OBJECTS}:
                document_payloads = _document_payloads(
                    repository,
                    limit=limit,
                    max_document_chars=max_document_chars,
                )
                documents_seen = len(document_payloads)
                documents_embedded, documents_unchanged = _embed_payloads(
                    repository,
                    selected_encoder,
                    model_id=model_id,
                    payloads=document_payloads,
                    force=force,
                    batch_size=batch_size,
                    normalize=normalize,
                    now=now,
                )

            if object_type in {CHUNK_OBJECT, BOTH_OBJECTS}:
                chunk_payloads = _chunk_payloads(
                    repository,
                    limit=limit,
                    max_chunk_chars=max_chunk_chars,
                )
                chunks_seen = len(chunk_payloads)
                chunks_embedded, chunks_unchanged = _embed_payloads(
                    repository,
                    selected_encoder,
                    model_id=model_id,
                    payloads=chunk_payloads,
                    force=force,
                    batch_size=batch_size,
                    normalize=normalize,
                    now=now,
                )

            finished_at = _utc_now()
            repository.finish_embedding_run(
                run_id,
                finished_at=finished_at,
                status="completed",
                documents_seen=documents_seen,
                documents_embedded=documents_embedded,
                documents_unchanged=documents_unchanged,
                chunks_seen=chunks_seen,
                chunks_embedded=chunks_embedded,
                chunks_unchanged=chunks_unchanged,
            )
    finally:
        connection.close()

    return EmbeddingRunSummary(
        id=run_id,
        model_id=model_id,
        model_name=selected_encoder.model_name,
        provider=EMBEDDING_PROVIDER,
        dimension=selected_encoder.dimension,
        database_path=database_path,
        started_at=now,
        finished_at=finished_at,
        status="completed",
        documents_seen=documents_seen,
        documents_embedded=documents_embedded,
        documents_unchanged=documents_unchanged,
        chunks_seen=chunks_seen,
        chunks_embedded=chunks_embedded,
        chunks_unchanged=chunks_unchanged,
    )


def build_document_embedding_text(
    document: IndexedDocument,
    text: str,
    *,
    max_document_chars: int = 8000,
) -> str:
    """Construct transparent weighted text for a document vector."""

    capped_text = text[: max(0, max_document_chars)]
    return "\n".join(
        [
            document.title,
            document.title,
            document.title,
            document.relative_path,
            capped_text,
        ]
    ).strip()


def build_chunk_embedding_text(
    chunk: IndexedChunk,
    *,
    max_chunk_chars: int = 2000,
) -> str:
    """Construct text for a chunk vector."""

    return chunk.text[: max(0, max_chunk_chars)]


def _document_payloads(
    repository: Repository,
    *,
    limit: int | None,
    max_document_chars: int,
) -> list[_EmbeddingPayload]:
    rows = repository.list_documents_with_text(
        statuses={"active"},
        limit=_effective_limit(limit),
    )
    return [
        _EmbeddingPayload(
            object_type=DOCUMENT_OBJECT,
            object_id=document.id,
            text=build_document_embedding_text(
                document,
                text,
                max_document_chars=max_document_chars,
            ),
            metadata={
                "document_id": document.id,
                "title": document.title,
                "relative_path": document.relative_path,
                "status": document.status,
            },
        )
        for document, text in rows
    ]


def _chunk_payloads(
    repository: Repository,
    *,
    limit: int | None,
    max_chunk_chars: int,
) -> list[_EmbeddingPayload]:
    rows = repository.list_chunks_with_documents(
        statuses={"active"},
        limit=_effective_limit(limit),
    )
    return [
        _EmbeddingPayload(
            object_type=CHUNK_OBJECT,
            object_id=chunk.id,
            text=build_chunk_embedding_text(
                chunk,
                max_chunk_chars=max_chunk_chars,
            ),
            metadata={
                "document_id": document.id,
                "title": document.title,
                "relative_path": document.relative_path,
                "chunk_index": chunk.chunk_index,
            },
        )
        for document, chunk in rows
    ]


def _embed_payloads(
    repository: Repository,
    encoder: EmbeddingEncoder,
    *,
    model_id: str,
    payloads: list[_EmbeddingPayload],
    force: bool,
    batch_size: int,
    normalize: bool,
    now: str,
) -> tuple[int, int]:
    to_embed: list[tuple[_EmbeddingPayload, str]] = []
    unchanged = 0
    for payload in payloads:
        current_hash = text_sha256(payload.text)
        existing = repository.get_vector(
            model_id,
            payload.object_type,
            payload.object_id,
        )
        if existing is not None and existing.text_sha256 == current_hash and not force:
            unchanged += 1
            continue
        to_embed.append((payload, current_hash))

    embedded = 0
    for batch in _batches(to_embed, max(1, batch_size)):
        batch_texts = [payload.text for payload, _ in batch]
        batch_vectors = encoder.encode(
            batch_texts,
            batch_size=max(1, batch_size),
            normalize=normalize,
        )
        if len(batch_vectors) != len(batch):
            raise ValueError(
                "Embedding encoder returned "
                f"{len(batch_vectors)} vectors for {len(batch)} texts."
            )
        for (payload, current_hash), values in zip(batch, batch_vectors, strict=True):
            repository.upsert_vector(
                VectorRecord(
                    id=stable_vector_id(
                        model_id,
                        payload.object_type,
                        payload.object_id,
                    ),
                    model_id=model_id,
                    object_type=payload.object_type,
                    object_id=payload.object_id,
                    text_sha256=current_hash,
                    dimension=encoder.dimension,
                    dtype=FLOAT32_DTYPE,
                    vector=encode_vector(
                        values,
                        expected_dimension=encoder.dimension,
                        normalize=normalize,
                    ),
                    metadata=payload.metadata,
                    created_at=now,
                    updated_at=now,
                )
            )
            embedded += 1
    return embedded, unchanged


def _batches(
    values: list[tuple[_EmbeddingPayload, str]], size: int
) -> Iterable[list[tuple[_EmbeddingPayload, str]]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _effective_limit(limit: int | None) -> int:
    if limit is None:
        return 1_000_000_000
    return max(0, limit)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
