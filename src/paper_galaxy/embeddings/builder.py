"""Build optional local document and chunk embeddings."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from paper_galaxy.embeddings.codec import FLOAT32_DTYPE, encode_vector
from paper_galaxy.embeddings.models import (
    LEGACY_UNKNOWN_PROVENANCE,
    EmbeddingModelRecord,
    EmbeddingRunSummary,
    VectorRecord,
    stable_embedding_model_id,
    stable_vector_id,
    text_sha256,
)
from paper_galaxy.embeddings.sentence_transformers import (
    EmbeddingEncoder,
    ModelFingerprintError,
    load_sentence_transformer,
)
from paper_galaxy.records import IndexedChunk, IndexedDocument
from paper_galaxy.storage.provenance import (
    DOCUMENT_CONTENT_REVISION_ALGORITHM,
    document_content_revision_sha256,
)
from paper_galaxy.storage.repository import Repository
from paper_galaxy.storage.sqlite import (
    connect_read_write,
    ensure_database_ready,
    resolve_database_path,
)

DOCUMENT_OBJECT = "document"
CHUNK_OBJECT = "chunk"
BOTH_OBJECTS = "both"
EMBEDDING_PROVIDER = "sentence-transformers"
EMBEDDING_DISTANCE = "cosine"
MAX_EMBEDDING_BATCH_SIZE = 512
DOCUMENT_VECTOR_ALGORITHM_VERSION = "paper-galaxy-weighted-text-v1"
CHUNK_VECTOR_ALGORITHM_VERSION = "paper-galaxy-chunk-text-v1"


@dataclass(frozen=True)
class _EmbeddingPayload:
    object_type: str
    object_id: str
    text: str
    source_content_sha256: str
    algorithm_version: str
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

    if object_type not in {DOCUMENT_OBJECT, CHUNK_OBJECT, BOTH_OBJECTS}:
        raise ValueError("Embedding object type must be document, chunk, or both.")
    if not 1 <= batch_size <= MAX_EMBEDDING_BATCH_SIZE:
        raise ValueError(
            f"Embedding batch size must be between 1 and {MAX_EMBEDDING_BATCH_SIZE}."
        )
    resolved_project_dir = project_dir.expanduser().resolve()
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
    now = _utc_now()
    run_id = f"embed_run_{uuid4().hex[:16]}"
    database_path = resolve_database_path(resolved_project_dir)
    ensure_database_ready(resolved_project_dir)
    connection = connect_read_write(resolved_project_dir)
    documents_seen = 0
    documents_embedded = 0
    documents_unchanged = 0
    chunks_seen = 0
    chunks_embedded = 0
    chunks_unchanged = 0
    sources_changed = 0
    finished_at = now
    try:
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
                    model_fingerprint=selected_encoder.model_fingerprint,
                    fingerprint_algorithm=selected_encoder.fingerprint_algorithm,
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

            def record_document_batch(count: int) -> None:
                nonlocal documents_embedded
                documents_embedded += count

            def record_unchanged_documents(count: int) -> None:
                nonlocal documents_unchanged
                documents_unchanged = count

            embedded_total, documents_unchanged, changed_total = _embed_payloads(
                repository,
                selected_encoder,
                model_id=model_id,
                payloads=document_payloads,
                force=force,
                batch_size=batch_size,
                normalize=normalize,
                now=now,
                on_batch_committed=record_document_batch,
                on_unchanged_count=record_unchanged_documents,
            )
            documents_embedded = embedded_total
            sources_changed += changed_total

        if object_type in {CHUNK_OBJECT, BOTH_OBJECTS}:
            chunk_payloads = _chunk_payloads(
                repository,
                limit=limit,
                max_chunk_chars=max_chunk_chars,
            )
            chunks_seen = len(chunk_payloads)

            def record_chunk_batch(count: int) -> None:
                nonlocal chunks_embedded
                chunks_embedded += count

            def record_unchanged_chunks(count: int) -> None:
                nonlocal chunks_unchanged
                chunks_unchanged = count

            embedded_total, chunks_unchanged, changed_total = _embed_payloads(
                repository,
                selected_encoder,
                model_id=model_id,
                payloads=chunk_payloads,
                force=force,
                batch_size=batch_size,
                normalize=normalize,
                now=now,
                on_batch_committed=record_chunk_batch,
                on_unchanged_count=record_unchanged_chunks,
            )
            chunks_embedded = embedded_total
            sources_changed += changed_total

        finished_at = _utc_now()
        with repository.connection:
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
                sources_changed=sources_changed,
            )
    except BaseException as exc:
        finished_at = _utc_now()
        status = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        with connection:
            Repository(connection, database_path).finish_embedding_run(
                run_id,
                finished_at=finished_at,
                status=status,
                documents_seen=documents_seen,
                documents_embedded=documents_embedded,
                documents_unchanged=documents_unchanged,
                chunks_seen=chunks_seen,
                chunks_embedded=chunks_embedded,
                chunks_unchanged=chunks_unchanged,
                sources_changed=sources_changed,
                errors=1,
                error_code=type(exc).__name__,
                error_message=_safe_error_message(exc),
            )
        raise
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
        sources_changed=sources_changed,
    )


def embedding_model_config(
    encoder: EmbeddingEncoder, *, normalize: bool
) -> dict[str, object]:
    """Return identity-bearing model configuration for vectors and queries."""

    fingerprint = encoder.model_fingerprint
    algorithm = encoder.fingerprint_algorithm
    if (
        len(fingerprint) != 64
        or fingerprint != fingerprint.lower()
        or any(character not in "0123456789abcdef" for character in fingerprint)
        or algorithm.strip() in {"", LEGACY_UNKNOWN_PROVENANCE}
    ):
        raise ModelFingerprintError(
            "The embedding encoder has no valid deterministic model fingerprint."
        )
    return {
        "normalize": normalize,
        "model_fingerprint": fingerprint,
        "fingerprint_algorithm": algorithm,
    }


def vector_algorithm_version(object_type: str) -> str:
    """Return the public embedding-input algorithm for an object type."""

    if object_type == DOCUMENT_OBJECT:
        return DOCUMENT_VECTOR_ALGORITHM_VERSION
    if object_type == CHUNK_OBJECT:
        return CHUNK_VECTOR_ALGORITHM_VERSION
    raise ValueError("Embedding object type must be 'document' or 'chunk'.")


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
    payloads: list[_EmbeddingPayload] = []
    for document, text in rows:
        content_revision = document_content_revision_sha256(
            title=document.title,
            relative_path=document.relative_path,
            text=text,
        )
        payloads.append(
            _EmbeddingPayload(
                object_type=DOCUMENT_OBJECT,
                object_id=document.id,
                text=build_document_embedding_text(
                    document,
                    text,
                    max_document_chars=max_document_chars,
                ),
                source_content_sha256=content_revision,
                algorithm_version=DOCUMENT_VECTOR_ALGORITHM_VERSION,
                metadata={
                    "document_id": document.id,
                    "title": document.title,
                    "relative_path": document.relative_path,
                    "status": document.status,
                    "source_text_sha256": text_sha256(text),
                    "source_identity_sha256": content_revision,
                    "source_revision_algorithm": (DOCUMENT_CONTENT_REVISION_ALGORITHM),
                    "embedding_input_algorithm": (DOCUMENT_VECTOR_ALGORITHM_VERSION),
                    "max_document_chars": max_document_chars,
                },
            )
        )
    return payloads


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
            source_content_sha256=chunk.text_sha256,
            algorithm_version=CHUNK_VECTOR_ALGORITHM_VERSION,
            metadata={
                "document_id": document.id,
                "title": document.title,
                "relative_path": document.relative_path,
                "chunk_index": chunk.chunk_index,
                "source_text_sha256": text_sha256(chunk.text),
                "embedding_input_algorithm": CHUNK_VECTOR_ALGORITHM_VERSION,
                "max_chunk_chars": max_chunk_chars,
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
    on_batch_committed: Callable[[int], None] | None = None,
    on_unchanged_count: Callable[[int], None] | None = None,
) -> tuple[int, int, int]:
    to_embed: list[tuple[_EmbeddingPayload, str]] = []
    unchanged = 0
    for payload in payloads:
        current_hash = text_sha256(payload.text)
        existing = repository.get_vector(
            model_id,
            payload.object_type,
            payload.object_id,
        )
        if (
            existing is not None
            and existing.text_sha256 == current_hash
            and existing.source_content_sha256 == payload.source_content_sha256
            and existing.model_fingerprint == encoder.model_fingerprint
            and existing.algorithm_version == payload.algorithm_version
            and existing.dimension == encoder.dimension
            and existing.dtype == FLOAT32_DTYPE
            and not force
        ):
            unchanged += 1
            continue
        to_embed.append((payload, current_hash))

    if on_unchanged_count is not None:
        on_unchanged_count(unchanged)

    embedded = 0
    sources_changed = 0
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
        records = [
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
                source_content_sha256=payload.source_content_sha256,
                model_fingerprint=encoder.model_fingerprint,
                algorithm_version=payload.algorithm_version,
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
            for (payload, current_hash), values in zip(
                batch, batch_vectors, strict=True
            )
        ]
        committed = 0
        repository.connection.execute("BEGIN IMMEDIATE")
        try:
            current_sources = repository.current_vector_source_ids(
                {
                    (record.object_type, record.object_id): (
                        record.source_content_sha256
                    )
                    for record in records
                }
            )
            for record in records:
                if (record.object_type, record.object_id) not in current_sources:
                    sources_changed += 1
                    continue
                repository.upsert_vector(record)
                committed += 1
            repository.connection.commit()
        except BaseException:
            repository.connection.rollback()
            raise
        embedded += committed
        if on_batch_committed is not None:
            on_batch_committed(committed)
    return embedded, unchanged, sources_changed


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


def _safe_error_message(error: BaseException, *, limit: int = 500) -> str:
    text = " ".join(str(error).split()) or type(error).__name__
    return text[:limit]
