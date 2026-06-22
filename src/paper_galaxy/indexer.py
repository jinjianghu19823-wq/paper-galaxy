"""Phase 2 local SQLite indexing orchestration."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from paper_galaxy.chunking import chunk_text
from paper_galaxy.extract import extract_file
from paper_galaxy.ingest.scanner import IMAGE_EXTENSIONS, discover_files, relative_path
from paper_galaxy.models import ExtractedContent
from paper_galaxy.records import (
    ExtractionReport,
    IndexedChunk,
    IndexedDocument,
    IndexRunSummary,
)
from paper_galaxy.storage.migrations import initialize_database
from paper_galaxy.storage.repository import Repository
from paper_galaxy.storage.sqlite import connect_database, resolve_database_path

EXTRACTOR_VERSION = "4"


def index_corpus(
    corpus_dir: Path,
    *,
    project_dir: Path,
    min_chars: int = 80,
    include_pdf: bool = True,
    include_images: bool = False,
    ocr: bool = False,
    ocr_language: str = "eng",
    extraction_report_json: Path | None = None,
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
            include_images=include_images,
            ocr=ocr,
            ocr_language=ocr_language,
            extraction_report_json=extraction_report_json,
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


def stable_extraction_report_id(
    scan_run_id: str, relative_document_path: str, status: str
) -> str:
    """Return a stable report id within one scan run."""

    digest = hashlib.sha256(
        f"{scan_run_id}\0{relative_document_path}\0{status}".encode()
    ).hexdigest()
    return f"extract_{digest[:16]}"


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
    include_images: bool,
    ocr: bool,
    ocr_language: str,
    extraction_report_json: Path | None,
    force_reextract: bool,
    chunk_size: int,
    chunk_overlap: int,
) -> IndexRunSummary:
    now = _utc_now()
    corpus_id = stable_corpus_id(corpus_path)
    scan_run_id = f"scan_{uuid4().hex[:16]}"
    discovered_files = discover_files(
        corpus_path, include_pdf=include_pdf, include_images=include_images
    )
    extraction_fingerprint = _extraction_fingerprint(
        include_pdf=include_pdf,
        include_images=include_images,
        ocr=ocr,
        ocr_language=ocr_language,
    )
    seen_document_ids: set[str] = set()
    report_payloads: list[dict[str, object]] = []

    documents_inserted = 0
    documents_updated = 0
    documents_unchanged = 0
    skipped_files = 0
    chunks_written = 0
    extracted_count = 0
    warning_count = 0
    ocr_count = 0
    scanned_pdf_candidates = 0
    image_files_seen = 0
    low_text_count = 0

    with repository.connection:
        repository.upsert_corpus(corpus_id, str(corpus_path), now)
        repository.create_scan_run(scan_run_id, corpus_id, str(corpus_path), now)

        for path in discovered_files:
            rel_path = relative_path(path, corpus_path)
            document_id = stable_document_id(corpus_id, rel_path)
            file_type = path.suffix.lower().lstrip(".")
            if path.suffix.lower() in IMAGE_EXTENSIONS:
                image_files_seen += 1
            existing = repository.get_document_by_relative_path(corpus_id, rel_path)
            stat = path.stat()
            digest = file_sha256(path)
            latest_fingerprint = repository.latest_extraction_fingerprint(
                corpus_id, rel_path
            )
            if (
                existing is not None
                and existing.sha256 == digest
                and existing.status in {"active", "missing"}
                and latest_fingerprint == extraction_fingerprint
                and not force_reextract
            ):
                repository.touch_document(existing.id, now)
                report = _build_extraction_report(
                    scan_run_id=scan_run_id,
                    document_id=existing.id,
                    corpus_id=corpus_id,
                    relative_path=rel_path,
                    file_type=file_type,
                    method="unchanged",
                    status="extracted",
                    char_count=existing.char_count,
                    warnings=(),
                    metadata={
                        "unchanged": True,
                        "extraction_fingerprint": extraction_fingerprint,
                    },
                    created_at=now,
                )
                repository.record_extraction_report(report)
                report_payloads.append(_report_payload(report))
                seen_document_ids.add(existing.id)
                documents_unchanged += 1
                continue

            extracted, skip_reason = extract_file(
                path,
                include_pdf=include_pdf,
                include_images=include_images,
                ocr=ocr,
                ocr_language=ocr_language,
            )
            if skip_reason is not None or extracted is None:
                status = _skip_status(path, skip_reason or "unknown")
                report = _build_extraction_report(
                    scan_run_id=scan_run_id,
                    document_id=existing.id if existing is not None else None,
                    corpus_id=corpus_id,
                    relative_path=rel_path,
                    file_type=file_type,
                    method=_method_for_skip(path),
                    status=status,
                    char_count=0,
                    warnings=(skip_reason or "unknown",),
                    metadata={
                        "skipped_reason": skip_reason or "unknown",
                        "extraction_fingerprint": extraction_fingerprint,
                    },
                    created_at=now,
                )
                repository.record_extraction_report(report)
                report_payloads.append(_report_payload(report))
                warning_count += 1
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
                reason = f"extracted text shorter than {min_chars} characters"
                report_status = (
                    "scanned_pdf_candidate"
                    if _is_scanned_pdf_candidate(extracted)
                    else "unindexed"
                )
                report = _report_for_content(
                    extracted,
                    scan_run_id=scan_run_id,
                    document_id=existing.id if existing is not None else None,
                    corpus_id=corpus_id,
                    relative_path=rel_path,
                    file_type=file_type,
                    status=report_status,
                    extraction_fingerprint=extraction_fingerprint,
                    created_at=now,
                    extra_metadata={"skipped_reason": reason},
                    extra_warnings=(reason,),
                )
                repository.record_extraction_report(report)
                report_payloads.append(_report_payload(report))
                low_text_count += 1
                warning_count += len(report.warnings)
                if _is_scanned_pdf_candidate(extracted):
                    scanned_pdf_candidates += 1
                repository.record_skipped_file(
                    scan_run_id=scan_run_id,
                    corpus_id=corpus_id,
                    relative_path=rel_path,
                    reason=reason,
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
                file_type=file_type,
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
            report = _report_for_content(
                extracted,
                scan_run_id=scan_run_id,
                document_id=document_id,
                corpus_id=corpus_id,
                relative_path=rel_path,
                file_type=file_type,
                status="extracted",
                extraction_fingerprint=extraction_fingerprint,
                created_at=now,
            )
            repository.record_extraction_report(report)
            report_payloads.append(_report_payload(report))
            seen_document_ids.add(document_id)
            chunks_written += len(chunks)
            extracted_count += 1
            warning_count += len(report.warnings)
            if extracted.method.startswith("image-ocr"):
                ocr_count += 1
            if _is_scanned_pdf_candidate(extracted):
                scanned_pdf_candidates += 1
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
            extracted_count=extracted_count,
            warning_count=warning_count,
            ocr_count=ocr_count,
            scanned_pdf_candidates=scanned_pdf_candidates,
            image_files_seen=image_files_seen,
            low_text_count=low_text_count,
            extraction_report_json=(
                extraction_report_json.expanduser().resolve()
                if extraction_report_json is not None
                else None
            ),
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
    if extraction_report_json is not None:
        _write_extraction_report_json(
            extraction_report_json.expanduser().resolve(),
            scan_run_id=scan_run_id,
            corpus_path=corpus_path,
            summary=summary,
            files=report_payloads,
        )
    return summary


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _extraction_fingerprint(
    *, include_pdf: bool, include_images: bool, ocr: bool, ocr_language: str
) -> str:
    payload = {
        "extractor_version": EXTRACTOR_VERSION,
        "include_pdf": include_pdf,
        "include_images": include_images,
        "ocr": ocr,
        "ocr_language": ocr_language,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return digest[:16]


def _build_extraction_report(
    *,
    scan_run_id: str,
    document_id: str | None,
    corpus_id: str,
    relative_path: str,
    file_type: str,
    method: str,
    status: str,
    char_count: int,
    warnings: tuple[str, ...],
    metadata: dict[str, object],
    created_at: str,
) -> ExtractionReport:
    return ExtractionReport(
        id=stable_extraction_report_id(scan_run_id, relative_path, status),
        scan_run_id=scan_run_id,
        document_id=document_id,
        corpus_id=corpus_id,
        relative_path=relative_path,
        file_type=file_type,
        method=method,
        status=status,
        char_count=char_count,
        warnings=warnings,
        metadata=metadata,
        created_at=created_at,
    )


def _report_for_content(
    extracted: ExtractedContent,
    *,
    scan_run_id: str,
    document_id: str | None,
    corpus_id: str,
    relative_path: str,
    file_type: str,
    status: str,
    extraction_fingerprint: str,
    created_at: str,
    extra_metadata: dict[str, object] | None = None,
    extra_warnings: tuple[str, ...] = (),
) -> ExtractionReport:
    metadata = dict(extracted.metadata)
    metadata["extraction_fingerprint"] = extraction_fingerprint
    metadata["sections"] = list(extracted.sections)
    metadata["links"] = list(extracted.links)
    if extra_metadata:
        metadata.update(extra_metadata)
    return _build_extraction_report(
        scan_run_id=scan_run_id,
        document_id=document_id,
        corpus_id=corpus_id,
        relative_path=relative_path,
        file_type=file_type,
        method=extracted.method,
        status=status,
        char_count=len(extracted.text),
        warnings=tuple([*extracted.warnings, *extra_warnings]),
        metadata=metadata,
        created_at=created_at,
    )


def _report_payload(report: ExtractionReport) -> dict[str, object]:
    payload: dict[str, object] = {
        "relative_path": report.relative_path,
        "file_type": report.file_type,
        "status": report.status,
        "method": report.method,
        "char_count": report.char_count,
        "warnings": list(report.warnings),
        "metadata": report.metadata,
    }
    skipped_reason = report.metadata.get("skipped_reason")
    if skipped_reason:
        payload["skipped_reason"] = str(skipped_reason)
    return payload


def _write_extraction_report_json(
    path: Path,
    *,
    scan_run_id: str,
    corpus_path: Path,
    summary: IndexRunSummary,
    files: list[dict[str, object]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "scan_run_id": scan_run_id,
        "corpus_path": str(corpus_path),
        "counts": {
            "files_found": summary.files_found,
            "documents_inserted": summary.documents_inserted,
            "documents_updated": summary.documents_updated,
            "documents_unchanged": summary.documents_unchanged,
            "documents_missing": summary.documents_missing,
            "skipped_files": summary.skipped_files,
            "chunks_written": summary.chunks_written,
            "extracted_count": summary.extracted_count,
            "warning_count": summary.warning_count,
            "ocr_count": summary.ocr_count,
            "scanned_pdf_candidates": summary.scanned_pdf_candidates,
            "image_files_seen": summary.image_files_seen,
            "low_text_count": summary.low_text_count,
        },
        "files": files,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _skip_status(path: Path, reason: str) -> str:
    if path.suffix.lower() in IMAGE_EXTENSIONS and "OCR" in reason:
        return "ocr_unavailable"
    if "failed" in reason.lower():
        return "failed"
    return "skipped"


def _method_for_skip(path: Path) -> str:
    if path.suffix.lower() in IMAGE_EXTENSIONS:
        return "image-ocr-tesseract"
    if path.suffix.lower() == ".pdf":
        return "pdf-pypdf"
    return "unknown"


def _is_scanned_pdf_candidate(extracted: ExtractedContent) -> bool:
    return bool(extracted.metadata.get("scanned_pdf_candidate"))
