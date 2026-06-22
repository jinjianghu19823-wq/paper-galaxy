"""Phase 2 local SQLite indexing orchestration."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from paper_galaxy.chunking import chunk_text
from paper_galaxy.extract import extract_file
from paper_galaxy.ingest.scanner import discover_files, relative_path
from paper_galaxy.records import IndexedChunk, IndexedDocument, IndexRunSummary
from paper_galaxy.storage.migrations import initialize_database
from paper_galaxy.storage.repository import Repository
from paper_galaxy.storage.sqlite import connect_database, resolve_database_path


def index_corpus(
    corpus_dir: Path,
    *,
    project_dir: Path,
    min_chars: int = 80,
    include_pdf: bool = True,
    force_reextract: bool = False,
    chunk_size: int = 2000,
    chunk_overlap: int = 200,
    verbose: bool = False,
) -> IndexRunSummary:
    """Index a local corpus into the project's SQLite database."""

    del verbose
    corpus_path = corpus_dir.expanduser().resolve()
    resolved_project_dir = project_dir.expanduser().resolve()
    database_path = resolve_database_path(resolved_project_dir)
    connection = connect_database(resolved_project_dir)
    try:
        initialize_database(connection)
        repository = Repository(connection, database_path)
        return _index_with_repository(
            repository,
            corpus_path=corpus_path,
            project_dir=resolved_project_dir,
            database_path=database_path,
            min_chars=min_chars,
            include_pdf=include_pdf,
            force_reextract=force_reextract,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
    finally:
        connection.close()


def stable_corpus_id(corpus_path: Path) -> str:
    """Return a stable local corpus id derived from its resolved root path."""

    digest = hashlib.sha256(str(corpus_path.resolve()).encode("utf-8")).hexdigest()
    return f"corpus_{digest[:16]}"


def stable_document_id(corpus_id: str, relative_document_path: str) -> str:
    """Return a stable document id for a corpus-relative path."""

    digest = hashlib.sha256(
        f"{corpus_id}\0{relative_document_path}".encode()
    ).hexdigest()
    return f"doc_{digest[:16]}"


def stable_chunk_id(document_id: str, chunk_index: int) -> str:
    """Return a stable chunk id for a document and chunk index."""

    return f"{document_id}_chunk_{chunk_index:06d}"


def file_sha256(path: Path) -> str:
    """Compute a file SHA-256 hash."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _index_with_repository(
    repository: Repository,
    *,
    corpus_path: Path,
    project_dir: Path,
    database_path: Path,
    min_chars: int,
    include_pdf: bool,
    force_reextract: bool,
    chunk_size: int,
    chunk_overlap: int,
) -> IndexRunSummary:
    now = _utc_now()
    corpus_id = stable_corpus_id(corpus_path)
    scan_run_id = f"scan_{uuid4().hex[:16]}"
    discovered_files = discover_files(corpus_path, include_pdf=include_pdf)
    seen_document_ids: set[str] = set()

    documents_inserted = 0
    documents_updated = 0
    documents_unchanged = 0
    skipped_files = 0
    chunks_written = 0

    with repository.connection:
        repository.upsert_corpus(corpus_id, str(corpus_path), now)
        repository.create_scan_run(scan_run_id, corpus_id, str(corpus_path), now)

        for path in discovered_files:
            rel_path = relative_path(path, corpus_path)
            document_id = stable_document_id(corpus_id, rel_path)
            existing = repository.get_document_by_relative_path(corpus_id, rel_path)
            stat = path.stat()
            digest = file_sha256(path)
            if (
                existing is not None
                and existing.sha256 == digest
                and existing.status in {"active", "missing"}
                and not force_reextract
            ):
                repository.touch_document(existing.id, now)
                seen_document_ids.add(existing.id)
                documents_unchanged += 1
                continue

            extracted, skip_reason = extract_file(path, include_pdf=include_pdf)
            if skip_reason is not None or extracted is None:
                repository.record_skipped_file(
                    scan_run_id=scan_run_id,
                    corpus_id=corpus_id,
                    relative_path=rel_path,
                    reason=skip_reason or "unknown",
                    created_at=now,
                )
                if existing is not None:
                    repository.mark_document_unindexed(
                        existing.id,
                        path=str(path.resolve()),
                        sha256=digest,
                        size_bytes=stat.st_size,
                        mtime_ns=stat.st_mtime_ns,
                        now=now,
                    )
                    seen_document_ids.add(existing.id)
                skipped_files += 1
                continue
            if len(extracted.text) < min_chars:
                repository.record_skipped_file(
                    scan_run_id=scan_run_id,
                    corpus_id=corpus_id,
                    relative_path=rel_path,
                    reason=f"extracted text shorter than {min_chars} characters",
                    created_at=now,
                )
                if existing is not None:
                    repository.mark_document_unindexed(
                        existing.id,
                        path=str(path.resolve()),
                        sha256=digest,
                        size_bytes=stat.st_size,
                        mtime_ns=stat.st_mtime_ns,
                        now=now,
                    )
                    seen_document_ids.add(existing.id)
                skipped_files += 1
                continue

            first_seen_at = existing.first_seen_at if existing is not None else now
            document = IndexedDocument(
                id=document_id,
                corpus_id=corpus_id,
                path=str(path.resolve()),
                relative_path=rel_path,
                file_type=path.suffix.lower().lstrip("."),
                title=extracted.title,
                sha256=digest,
                size_bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                char_count=len(extracted.text),
                status="active",
                first_seen_at=first_seen_at,
                last_seen_at=now,
                updated_at=now,
            )
            chunks = [
                IndexedChunk(
                    id=stable_chunk_id(document_id, index),
                    document_id=document_id,
                    chunk_index=index,
                    text=chunk,
                    char_count=len(chunk),
                )
                for index, chunk in enumerate(
                    chunk_text(
                        extracted.text,
                        chunk_size=chunk_size,
                        chunk_overlap=chunk_overlap,
                    )
                )
            ]
            repository.upsert_document(document, extracted.text, chunks)
            seen_document_ids.add(document_id)
            chunks_written += len(chunks)
            if existing is None:
                documents_inserted += 1
            else:
                documents_updated += 1

        finished_at = _utc_now()
        documents_missing = repository.mark_missing_documents(
            corpus_id, seen_document_ids, finished_at
        )
        summary = IndexRunSummary(
            scan_run_id=scan_run_id,
            corpus_id=corpus_id,
            corpus_path=corpus_path,
            project_dir=project_dir,
            database_path=database_path,
            files_found=len(discovered_files),
            documents_inserted=documents_inserted,
            documents_updated=documents_updated,
            documents_unchanged=documents_unchanged,
            documents_missing=documents_missing,
            skipped_files=skipped_files,
            chunks_written=chunks_written,
        )
        repository.finish_scan_run(
            scan_run_id,
            finished_at=finished_at,
            files_found=summary.files_found,
            documents_inserted=documents_inserted,
            documents_updated=documents_updated,
            documents_unchanged=documents_unchanged,
            documents_missing=documents_missing,
            skipped_files=skipped_files,
            chunks_written=chunks_written,
        )
    return summary


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
