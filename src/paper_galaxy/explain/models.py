"""Value objects for local explainability payloads."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TermScore:
    """One inspectable term and its local evidence score."""

    term: str
    score: float


@dataclass(frozen=True)
class DocumentSummary:
    """Compact document metadata safe for API and CLI explanations."""

    document_id: str
    title: str
    relative_path: str
    score: float = 0.0


@dataclass(frozen=True)
class ClusterLabel:
    """Generated and display label metadata for one map cluster."""

    cluster_id: int
    cluster_signature: str
    generated_label: str
    display_label: str
    source: str
    document_ids: list[str]
    top_terms: list[TermScore] = field(default_factory=list)
    representatives: list[DocumentSummary] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ChunkMatch:
    """A short local evidence match between two document chunks."""

    source_chunk_id: str
    source_chunk_index: int
    target_chunk_id: str
    target_chunk_index: int
    score: float
    shared_terms: list[str]
    source_excerpt: str
    target_excerpt: str


@dataclass(frozen=True)
class PairExplanation:
    """Local answer to why two documents are near each other."""

    source: DocumentSummary
    target: DocumentSummary
    lexical_score: float
    shared_terms: list[TermScore]
    chunk_matches: list[ChunkMatch]
    dense_score: float | None = None
    hybrid_score: float | None = None
    warnings: list[str] = field(default_factory=list)
