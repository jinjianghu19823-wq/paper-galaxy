"""Small value objects for optional local embedding features."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from paper_galaxy.records import IndexedDocument

LEGACY_UNKNOWN_PROVENANCE = "legacy-unknown"


@dataclass(frozen=True)
class EmbeddingModelRecord:
    """A registered local embedding model."""

    id: str
    name: str
    provider: str
    dimension: int
    distance: str
    config: dict[str, Any]
    created_at: str
    model_fingerprint: str = LEGACY_UNKNOWN_PROVENANCE
    fingerprint_algorithm: str = LEGACY_UNKNOWN_PROVENANCE


@dataclass(frozen=True)
class VectorRecord:
    """One stored vector for a document or chunk."""

    id: str
    model_id: str
    object_type: str
    object_id: str
    text_sha256: str
    dimension: int
    dtype: str
    vector: bytes
    metadata: dict[str, Any]
    created_at: str
    updated_at: str
    source_content_sha256: str = LEGACY_UNKNOWN_PROVENANCE
    model_fingerprint: str = LEGACY_UNKNOWN_PROVENANCE
    algorithm_version: str = LEGACY_UNKNOWN_PROVENANCE


@dataclass(frozen=True)
class EmbeddingRunSummary:
    """Summary of one embedding build run."""

    id: str
    model_id: str
    model_name: str
    provider: str
    dimension: int
    database_path: Path
    started_at: str
    finished_at: str
    status: str
    documents_seen: int = 0
    documents_embedded: int = 0
    documents_unchanged: int = 0
    chunks_seen: int = 0
    chunks_embedded: int = 0
    chunks_unchanged: int = 0
    sources_changed: int = 0
    errors: int = 0
    owner_pid: int | None = None


@dataclass(frozen=True)
class SemanticSearchResult:
    """A dense semantic search hit."""

    rank: int
    object_type: str
    object_id: str
    document_id: str
    title: str
    relative_path: str
    file_type: str
    status: str
    score: float
    snippet: str
    chunk_index: int | None = None


@dataclass(frozen=True)
class SemanticResultSource:
    """Top-k display source joined to its current stored vector revision."""

    document: IndexedDocument
    text: str
    chunk_index: int | None
    source_content_sha256: str
    vector_id: str
    vector_text_sha256: str
    vector_updated_at: str
    vector_blob: bytes
    vector_dimension: int
    vector_dtype: str
    vector_model_fingerprint: str
    vector_algorithm_version: str


@dataclass(frozen=True)
class NeighborResult:
    """One neighbor in a similarity ranking."""

    rank: int
    document_id: str
    title: str
    relative_path: str
    score: float


@dataclass(frozen=True)
class SimilarityComparisonResult:
    """TF-IDF, dense, and hybrid neighbors for one document."""

    target: IndexedDocument
    tfidf_neighbors: list[NeighborResult]
    dense_neighbors: list[NeighborResult]
    hybrid_neighbors: list[NeighborResult]


def stable_embedding_model_id(
    *,
    provider: str,
    name: str,
    dimension: int,
    distance: str,
    config: Mapping[str, Any],
    model_fingerprint: str = LEGACY_UNKNOWN_PROVENANCE,
    fingerprint_algorithm: str = LEGACY_UNKNOWN_PROVENANCE,
) -> str:
    """Return a deterministic model id from the model identity."""

    payload = {
        "provider": provider,
        "name": name,
        "dimension": dimension,
        "distance": distance,
        "config": dict(sorted(config.items())),
        "model_fingerprint": model_fingerprint,
        "fingerprint_algorithm": fingerprint_algorithm,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"embed_model_{digest[:16]}"


def stable_vector_id(model_id: str, object_type: str, object_id: str) -> str:
    """Return a deterministic vector id for a model/object pair."""

    digest = hashlib.sha256(
        f"{model_id}\0{object_type}\0{object_id}".encode()
    ).hexdigest()
    return f"vec_{digest[:16]}"


def text_sha256(text: str) -> str:
    """Hash the exact text sent to an embedding model."""

    return hashlib.sha256(text.encode("utf-8")).hexdigest()
