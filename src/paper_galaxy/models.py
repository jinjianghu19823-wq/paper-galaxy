"""Small data models for the Phase 1 static galaxy pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ExtractedContent:
    """Text and title extracted from a local document."""

    title: str
    text: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class Document:
    """A local source document included in a galaxy build."""

    id: str
    path: Path
    relative_path: str
    file_type: str
    title: str
    text: str
    char_count: int


@dataclass(frozen=True)
class SkippedFile:
    """A source file skipped during scanning or extraction."""

    path: Path
    relative_path: str
    reason: str


@dataclass(frozen=True)
class Neighbor:
    """Nearest-neighbor summary based on high-dimensional similarity."""

    document_id: str
    title: str
    relative_path: str
    score: float


@dataclass(frozen=True)
class MapPoint:
    """A 2D map point plus inspectable document metadata."""

    document_id: str
    x: float
    y: float
    cluster_id: int
    cluster_label: str
    nearest_neighbors: list[Neighbor] = field(default_factory=list)
    top_terms: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GalaxyBuildResult:
    """Result of building a static local galaxy."""

    corpus_path: Path
    files_found: int
    documents: list[Document]
    skipped_files: list[SkippedFile]
    points: list[MapPoint]
    cluster_labels: dict[int, str]
    output_path: Path
