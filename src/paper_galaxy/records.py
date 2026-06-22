"""Persistent record models for Phase 2 indexing and search."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class IndexedDocument:
    """A document row persisted in SQLite."""

    id: str
    corpus_id: str
    path: str
    relative_path: str
    file_type: str
    title: str
    sha256: str
    size_bytes: int
    mtime_ns: int
    char_count: int
    status: str
    first_seen_at: str
    last_seen_at: str
    updated_at: str


@dataclass(frozen=True)
class IndexedChunk:
    """A persisted text chunk."""

    id: str
    document_id: str
    chunk_index: int
    text: str
    char_count: int


@dataclass(frozen=True)
class IndexRunSummary:
    """Summary for one local indexing run."""

    scan_run_id: str
    corpus_id: str
    corpus_path: Path
    project_dir: Path
    database_path: Path
    files_found: int
    documents_inserted: int
    documents_updated: int
    documents_unchanged: int
    documents_missing: int
    skipped_files: int
    chunks_written: int
    extracted_count: int = 0
    warning_count: int = 0
    ocr_count: int = 0
    scanned_pdf_candidates: int = 0
    image_files_seen: int = 0
    low_text_count: int = 0
    extraction_report_json: Path | None = None


@dataclass(frozen=True)
class ExtractionReport:
    """Compact extraction-quality record persisted per indexing run."""

    id: str
    scan_run_id: str
    document_id: str | None
    corpus_id: str
    relative_path: str
    file_type: str
    method: str
    status: str
    char_count: int
    warnings: tuple[str, ...]
    metadata: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class SearchResult:
    """A local full-text search result."""

    rank: int
    document_id: str
    title: str
    relative_path: str
    file_type: str
    char_count: int
    updated_at: str
    snippet: str
    score: float


@dataclass(frozen=True)
class DatabaseStats:
    """Summary statistics for a Paper Galaxy SQLite database."""

    database_path: Path
    documents: int
    active_documents: int
    missing_documents: int
    unindexed_documents: int
    chunks: int
    scan_runs: int
    last_scan_time: str | None
    total_indexed_characters: int
