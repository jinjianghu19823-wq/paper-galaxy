from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

import paper_galaxy.indexer as indexer_module
from paper_galaxy.indexer import IndexingCancelled, IndexingSourceChanged, index_corpus
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


def test_indexing_cancels_at_a_document_boundary_and_marks_run_interrupted(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.md").write_text("# A\n\n" + "a" * 80, encoding="utf-8")
    (corpus / "b.md").write_text("# B\n\n" + "b" * 80, encoding="utf-8")
    cancel = False

    def progress(current: int, total: int, message: str) -> None:
        nonlocal cancel
        assert total == 2
        assert message.startswith("Indexing local document")
        if current == 0:
            cancel = True

    with pytest.raises(IndexingCancelled, match="document boundary"):
        index_corpus(
            corpus,
            project_dir=tmp_path,
            min_chars=1,
            cancel_requested=lambda: cancel,
            progress_callback=progress,
        )

    database_path = resolve_database_path(tmp_path)
    assert scalar(database_path, "SELECT COUNT(*) FROM documents") == 1
    connection = sqlite3.connect(database_path)
    try:
        status = connection.execute(
            "SELECT status FROM scan_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()[0]
    finally:
        connection.close()
    assert status == "interrupted"


def test_indexing_rejects_corpus_root_replacement_before_reading_new_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "paper.md").write_text("# Public\n\n" + "a" * 80, encoding="utf-8")
    secret = tmp_path / "secret"
    secret.mkdir()
    secret_text = "# Private\n\nDO NOT INDEX " + "z" * 80
    (secret / "paper.md").write_text(secret_text, encoding="utf-8")
    project = tmp_path / "project"
    displaced = tmp_path / "displaced-corpus"
    real_discover = indexer_module.discover_files

    def replace_after_discovery(
        root: Path,
        *,
        include_pdf: bool,
        include_images: bool,
    ) -> list[Path]:
        discovered = real_discover(
            root,
            include_pdf=include_pdf,
            include_images=include_images,
        )
        root.rename(displaced)
        root.symlink_to(secret, target_is_directory=True)
        return discovered

    monkeypatch.setattr(indexer_module, "discover_files", replace_after_discovery)

    with pytest.raises(IndexingSourceChanged, match=r"corpus root changed"):
        index_corpus(corpus, project_dir=project, min_chars=1)

    database = resolve_database_path(project)
    assert scalar(database, "SELECT COUNT(*) FROM documents") == 0
    with sqlite3.connect(database) as connection:
        stored_text = " ".join(
            str(row[0]) for row in connection.execute("SELECT text FROM documents_fts")
        )
        status = connection.execute(
            "SELECT status FROM scan_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()[0]
    assert "DO NOT INDEX" not in stored_text
    assert status == "failed"


def test_indexing_rejects_custom_database_inside_corpus_before_creation(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    sentinel = corpus / "keep.txt"
    sentinel.write_bytes(b"private source")
    project = tmp_path / "project"
    metadata = project / ".paper-galaxy"
    metadata.mkdir(parents=True)
    database = corpus / "paper-galaxy.sqlite3"
    (metadata / "project.toml").write_text(
        "\n".join(
            [
                'project_name = "Synthetic"',
                'created_by = "test"',
                "map_seed = 42",
                "corpus_dirs = []",
                f'database_path = "{database.as_posix()}"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"database cannot be stored inside"):
        index_corpus(corpus, project_dir=project, min_chars=1)

    assert sentinel.read_bytes() == b"private source"
    assert not database.exists()
