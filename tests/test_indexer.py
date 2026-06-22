from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

from paper_galaxy.indexer import index_corpus
from paper_galaxy.storage.sqlite import resolve_database_path


def copy_tiny_corpus(tmp_path: Path) -> Path:
    source = Path("examples/tiny_corpus")
    destination = tmp_path / "tiny_corpus"
    shutil.copytree(source, destination)
    return destination


def fetch_document(database_path: Path, relative_path: str) -> sqlite3.Row:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT * FROM documents WHERE relative_path = ?",
            (relative_path,),
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    return row


def scalar(database_path: Path, sql: str) -> int:
    connection = sqlite3.connect(database_path)
    try:
        row = connection.execute(sql).fetchone()
    finally:
        connection.close()
    assert row is not None
    return int(row[0])


def test_indexing_tiny_corpus_inserts_documents_chunks_and_run(
    tmp_path: Path,
) -> None:
    corpus = copy_tiny_corpus(tmp_path)

    summary = index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    database_path = resolve_database_path(tmp_path)

    assert summary.files_found == 8
    assert summary.documents_inserted == 8
    assert summary.documents_updated == 0
    assert summary.documents_unchanged == 0
    assert summary.skipped_files == 0
    assert (
        scalar(
            database_path,
            "SELECT COUNT(*) FROM documents WHERE status = 'active'",
        )
        == 8
    )
    assert scalar(database_path, "SELECT COUNT(*) FROM chunks") == 8
    assert scalar(database_path, "SELECT COUNT(*) FROM extraction_reports") == 8
    assert (
        scalar(
            database_path,
            "SELECT COUNT(*) FROM scan_runs WHERE status = 'completed'",
        )
        == 1
    )


def test_second_index_reports_unchanged_documents(tmp_path: Path) -> None:
    corpus = copy_tiny_corpus(tmp_path)

    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    summary = index_corpus(corpus, project_dir=tmp_path, min_chars=40)

    assert summary.documents_inserted == 0
    assert summary.documents_updated == 0
    assert summary.documents_unchanged == 8


def test_editing_file_updates_document_and_preserves_id(tmp_path: Path) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    relative_path = "neural_operators/fourier_neural_operator.md"

    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    database_path = resolve_database_path(tmp_path)
    before = fetch_document(database_path, relative_path)
    target = corpus / relative_path
    target.write_text(
        target.read_text(encoding="utf-8")
        + "\n\nAdditional neural operator notes about Fourier layers.",
        encoding="utf-8",
    )

    summary = index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    after = fetch_document(database_path, relative_path)

    assert summary.documents_updated == 1
    assert after["id"] == before["id"]
    assert after["sha256"] != before["sha256"]
    assert int(after["char_count"]) > int(before["char_count"])


def test_deleting_file_marks_document_missing(tmp_path: Path) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    relative_path = "thesis/literature_plan.txt"

    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    (corpus / relative_path).unlink()

    summary = index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    database_path = resolve_database_path(tmp_path)
    deleted = fetch_document(database_path, relative_path)

    assert summary.documents_missing == 1
    assert deleted["status"] == "missing"


def test_short_file_is_recorded_as_skipped(tmp_path: Path) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    (corpus / "short.txt").write_text("tiny", encoding="utf-8")

    summary = index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    database_path = resolve_database_path(tmp_path)

    assert summary.files_found == 9
    assert summary.skipped_files == 1
    assert scalar(database_path, "SELECT COUNT(*) FROM skipped_files") == 1
    assert (
        scalar(
            database_path,
            "SELECT COUNT(*) FROM extraction_reports WHERE status = 'unindexed'",
        )
        == 1
    )


def test_indexing_writes_extraction_report_json_without_text(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    secret_text = "Unique full extracted text that should stay out of reports."
    (corpus / "note.txt").write_text(secret_text, encoding="utf-8")
    report_path = tmp_path / "extraction-report.json"

    summary = index_corpus(
        corpus,
        project_dir=tmp_path,
        min_chars=10,
        extraction_report_json=report_path,
    )
    payload = json.loads(report_path.read_text(encoding="utf-8"))

    assert summary.extraction_report_json == report_path.resolve()
    assert payload["scan_run_id"] == summary.scan_run_id
    assert payload["counts"]["extracted_count"] == 1
    assert payload["files"][0]["relative_path"] == "note.txt"
    assert payload["files"][0]["method"] == "text"
    assert secret_text not in report_path.read_text(encoding="utf-8")


def test_indexing_records_image_ocr_unavailable_report(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "screen.png").write_bytes(b"image")

    summary = index_corpus(
        corpus,
        project_dir=tmp_path,
        include_images=True,
        ocr=False,
    )
    database_path = resolve_database_path(tmp_path)

    assert summary.files_found == 1
    assert summary.image_files_seen == 1
    assert summary.skipped_files == 1
    assert (
        scalar(
            database_path,
            "SELECT COUNT(*) FROM extraction_reports WHERE status = 'ocr_unavailable'",
        )
        == 1
    )
