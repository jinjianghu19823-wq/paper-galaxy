from __future__ import annotations

import copy
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

import paper_galaxy.embeddings.builder as embedding_builder
import paper_galaxy.indexer as indexer
import paper_galaxy.zotero.importers as zotero_importers
from paper_galaxy.embeddings.builder import build_embeddings
from paper_galaxy.indexer import index_corpus
from paper_galaxy.models import ExtractedContent
from paper_galaxy.storage.sqlite import (
    connect_read_write,
    resolve_database_path,
)
from paper_galaxy.zotero.importers import import_from_zotero
from tests.test_zotero_integration import FakeZoteroClient


@dataclass
class _RecordingEncoder:
    transaction_probe: Callable[[], bool]
    fail: bool = False
    model_name: str = "transaction-boundary-test-encoder"
    dimension: int = 3
    model_fingerprint: str = "2" * 64
    fingerprint_algorithm: str = "synthetic-test-fingerprint-v1"
    transaction_states: list[bool] = field(default_factory=list)

    def encode(
        self,
        texts: list[str],
        *,
        batch_size: int = 32,
        normalize: bool = True,
    ) -> list[list[float]]:
        del batch_size, normalize
        self.transaction_states.append(self.transaction_probe())
        if self.fail:
            raise RuntimeError("synthetic encoder failure")
        return [[1.0, 0.0, 0.0] for _ in texts]


def test_index_hash_and_extraction_run_outside_write_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    corpus_dir = _one_file_corpus(tmp_path)
    writers, probe = _capture_writers(monkeypatch, indexer)
    hash_states: list[bool] = []
    extraction_states: list[bool] = []
    real_file_sha256 = indexer.file_sha256
    real_extract_file = indexer.extract_file

    def recording_hash(path: Path) -> str:
        hash_states.append(probe())
        return real_file_sha256(path)

    def recording_extract(
        path: Path,
        **kwargs: object,
    ) -> tuple[ExtractedContent | None, str | None]:
        extraction_states.append(probe())
        return real_extract_file(path, **kwargs)

    monkeypatch.setattr(indexer, "file_sha256", recording_hash)
    monkeypatch.setattr(indexer, "extract_file", recording_extract)

    summary = index_corpus(corpus_dir, project_dir=project_dir, min_chars=1)

    assert writers
    assert summary.documents_inserted == 1
    assert hash_states == [False]
    assert extraction_states == [False]


def test_index_file_failure_completes_with_per_file_error_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    corpus_dir = _one_file_corpus(tmp_path)

    def fail_extract(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("synthetic extraction failure")

    monkeypatch.setattr(indexer, "extract_file", fail_extract)

    summary = index_corpus(corpus_dir, project_dir=project_dir, min_chars=1)

    row = _single_run(
        resolve_database_path(project_dir),
        "scan_runs",
    )
    assert summary.skipped_files == 1
    assert summary.warning_count == 1
    assert row["status"] == "completed"
    assert row["finished_at"]
    with sqlite3.connect(resolve_database_path(project_dir)) as connection:
        connection.row_factory = sqlite3.Row
        report = connection.execute(
            """
            SELECT method, status, warnings_json, metadata_json
            FROM extraction_reports
            """
        ).fetchone()
    assert report is not None
    assert report["method"] == "failed"
    assert report["status"] == "failed"
    assert "synthetic extraction failure" in report["warnings_json"]
    assert "RuntimeError" in report["metadata_json"]


def test_index_report_write_failure_marks_scan_failed(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    corpus_dir = _one_file_corpus(tmp_path)
    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_text("sentinel", encoding="utf-8")

    with pytest.raises(FileExistsError):
        index_corpus(
            corpus_dir,
            project_dir=project_dir,
            min_chars=1,
            extraction_report_json=blocked_parent / "report.json",
        )

    row = _single_run(resolve_database_path(project_dir), "scan_runs")
    assert row["status"] == "failed"
    assert row["finished_at"]
    assert row["error_code"] == "FileExistsError"
    assert row["error_message"]
    assert blocked_parent.read_text(encoding="utf-8") == "sentinel"


def test_index_storage_corruption_fails_run_without_downgrading_document(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    corpus_dir = _one_file_corpus(tmp_path)
    index_corpus(corpus_dir, project_dir=project_dir, min_chars=1)
    database_path = resolve_database_path(project_dir)
    with sqlite3.connect(database_path) as connection:
        connection.execute("UPDATE extraction_reports SET metadata_json = '['")
        connection.commit()

    with pytest.raises(ValueError) as caught:
        index_corpus(corpus_dir, project_dir=project_dir, min_chars=1)

    assert caught.value.code == "invalid_json"  # type: ignore[attr-defined]
    with sqlite3.connect(database_path) as connection:
        statuses = [
            row[0]
            for row in connection.execute(
                "SELECT status FROM scan_runs ORDER BY started_at, rowid"
            ).fetchall()
        ]
        document_status = connection.execute("SELECT status FROM documents").fetchone()
    assert statuses == ["completed", "failed"]
    assert document_status == ("active",)


def test_embedding_inference_runs_outside_write_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    corpus_dir = _one_file_corpus(tmp_path)
    index_corpus(corpus_dir, project_dir=project_dir, min_chars=1)
    writers, probe = _capture_writers(monkeypatch, embedding_builder)
    encoder = _RecordingEncoder(probe)

    summary = build_embeddings(
        project_dir=project_dir,
        model="unused",
        object_type="document",
        encoder=encoder,
    )

    assert writers
    assert summary.documents_embedded == 1
    assert encoder.transaction_states == [False]


def test_embedding_failure_leaves_a_failed_auditable_run(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    corpus_dir = _one_file_corpus(tmp_path)
    index_corpus(corpus_dir, project_dir=project_dir, min_chars=1)
    encoder = _RecordingEncoder(lambda: False, fail=True)

    with pytest.raises(RuntimeError, match="synthetic encoder failure"):
        build_embeddings(
            project_dir=project_dir,
            model="unused",
            object_type="document",
            encoder=encoder,
        )

    row = _single_run(
        resolve_database_path(project_dir),
        "embedding_runs",
    )
    assert row["status"] == "failed"
    assert row["finished_at"]
    assert row["errors"] == 1
    assert row["error_code"]
    assert row["error_message"]


def test_embedding_batch_validation_failure_does_not_overcount_or_write(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    for name in ("one", "two"):
        (corpus_dir / f"{name}.txt").write_text(
            f"Synthetic evidence document {name} with enough local text.",
            encoding="utf-8",
        )
    index_corpus(corpus_dir, project_dir=project_dir, min_chars=1)

    class InvalidSecondVectorEncoder:
        model_name = "invalid-second-vector"
        dimension = 3
        model_fingerprint = "3" * 64
        fingerprint_algorithm = "synthetic-test-fingerprint-v1"

        def encode(
            self,
            texts: list[str],
            *,
            batch_size: int = 32,
            normalize: bool = True,
        ) -> list[list[float]]:
            del batch_size, normalize
            assert len(texts) == 2
            return [[1.0, 0.0, 0.0], [1.0, 0.0]]

    with pytest.raises(ValueError, match="dimension"):
        build_embeddings(
            project_dir=project_dir,
            model="unused",
            object_type="document",
            batch_size=2,
            encoder=InvalidSecondVectorEncoder(),
        )

    database_path = resolve_database_path(project_dir)
    row = _single_run(database_path, "embedding_runs")
    assert row["status"] == "failed"
    assert row["documents_embedded"] == 0
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM vectors").fetchone() == (0,)


def test_embedding_failed_run_counts_only_committed_batches(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    for name in ("one", "two"):
        (corpus_dir / f"{name}.txt").write_text(
            f"Synthetic committed-batch document {name} with local evidence.",
            encoding="utf-8",
        )
    index_corpus(corpus_dir, project_dir=project_dir, min_chars=1)

    class FailSecondBatchEncoder:
        model_name = "fail-second-batch"
        dimension = 2
        model_fingerprint = "4" * 64
        fingerprint_algorithm = "synthetic-test-fingerprint-v1"
        calls = 0

        def encode(
            self,
            texts: list[str],
            *,
            batch_size: int = 32,
            normalize: bool = True,
        ) -> list[list[float]]:
            del batch_size, normalize
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("synthetic second batch failure")
            return [[1.0, 0.0] for _ in texts]

    with pytest.raises(RuntimeError, match="second batch failure"):
        build_embeddings(
            project_dir=project_dir,
            model="unused",
            object_type="document",
            batch_size=1,
            encoder=FailSecondBatchEncoder(),
        )

    database_path = resolve_database_path(project_dir)
    row = _single_run(database_path, "embedding_runs")
    assert row["status"] == "failed"
    assert row["documents_embedded"] == 1
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM vectors").fetchone() == (1,)


def test_embedding_failed_run_preserves_unchanged_count(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    for name in ("one", "two"):
        (corpus_dir / f"{name}.txt").write_text(
            f"Synthetic unchanged-count document {name} with evidence.",
            encoding="utf-8",
        )
    index_corpus(corpus_dir, project_dir=project_dir, min_chars=1)
    good_encoder = _RecordingEncoder(lambda: False)
    build_embeddings(
        project_dir=project_dir,
        model="unused",
        object_type="document",
        limit=1,
        encoder=good_encoder,
    )
    failing_encoder = _RecordingEncoder(lambda: False, fail=True)

    with pytest.raises(RuntimeError, match="synthetic encoder failure"):
        build_embeddings(
            project_dir=project_dir,
            model="unused",
            object_type="document",
            encoder=failing_encoder,
        )

    database_path = resolve_database_path(project_dir)
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT * FROM embedding_runs
            ORDER BY started_at DESC, rowid DESC
            LIMIT 1
            """
        ).fetchone()
    assert row is not None
    assert row["status"] == "failed"
    assert row["documents_seen"] == 2
    assert row["documents_unchanged"] == 1
    assert row["documents_embedded"] == 0


def test_zotero_pdf_extraction_runs_outside_write_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    data_dir = _fake_zotero_data_dir(tmp_path)
    writers, probe = _capture_writers(monkeypatch, zotero_importers)
    transaction_states: list[bool] = []

    def recording_extract(
        path: Path,
    ) -> tuple[ExtractedContent | None, str | None]:
        assert path.name == "fourier.pdf"
        transaction_states.append(probe())
        return (
            ExtractedContent(
                title="Synthetic PDF",
                text="Synthetic extracted PDF text for transaction testing.",
                method="pdf-test",
            ),
            None,
        )

    monkeypatch.setattr(zotero_importers, "extract_pdf_file", recording_extract)

    summary = import_from_zotero(
        project_dir=project_dir,
        data_dir=data_dir,
        client=FakeZoteroClient(),
        build_reading_map=False,
        min_chars=1,
    )

    assert writers
    assert summary.pdfs_extracted == 1
    assert transaction_states == [False]


def test_zotero_pdf_failure_preserves_cursor_and_records_failed_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    data_dir = _fake_zotero_data_dir(tmp_path)
    import_from_zotero(
        project_dir=project_dir,
        data_dir=data_dir,
        client=FakeZoteroClient(),
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )
    database_path = resolve_database_path(project_dir)
    assert _source_cursor(database_path) == 7
    _, probe = _capture_writers(monkeypatch, zotero_importers)
    extraction_states: list[bool] = []

    def fail_extract(path: Path) -> None:
        assert path.name == "fourier.pdf"
        extraction_states.append(probe())
        raise RuntimeError("synthetic Zotero PDF failure")

    monkeypatch.setattr(zotero_importers, "extract_pdf_file", fail_extract)

    with pytest.raises(RuntimeError, match="synthetic Zotero PDF failure"):
        import_from_zotero(
            project_dir=project_dir,
            data_dir=data_dir,
            client=_AdvancedVersionZoteroClient(),
            build_reading_map=False,
            min_chars=1,
        )

    assert extraction_states == [False]
    assert _source_cursor(database_path) == 7
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT status, finished_at, error_code, error_message
            FROM zotero_import_runs
            ORDER BY started_at, id
            """
        ).fetchall()
    assert [row["status"] for row in rows] == ["completed", "failed"]
    assert rows[-1]["finished_at"]
    assert rows[-1]["error_code"]
    assert rows[-1]["error_message"]


def test_zotero_fetch_failure_is_audited_before_any_cursor_advance(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"

    class FailingFetchClient(FakeZoteroClient):
        def collections(self) -> list[dict[str, object]]:
            raise RuntimeError("synthetic Zotero fetch failure")

    with pytest.raises(RuntimeError, match="synthetic Zotero fetch failure"):
        import_from_zotero(
            project_dir=project_dir,
            client=FailingFetchClient(),
            build_reading_map=False,
            min_chars=1,
        )

    database_path = resolve_database_path(project_dir)
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT status, finished_at, error_code, error_message
            FROM zotero_import_runs
            """
        ).fetchone()
        cursor = connection.execute(
            "SELECT last_version FROM zotero_sources"
        ).fetchone()
    assert row is not None
    assert row["status"] == "failed"
    assert row["finished_at"]
    assert row["error_code"] == "RuntimeError"
    assert cursor is not None and cursor[0] is None


def test_post_import_map_failure_is_reported_without_failing_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"

    def fail_map(**kwargs: object) -> None:
        del kwargs
        raise RuntimeError("synthetic map failure")

    monkeypatch.setattr(
        zotero_importers,
        "build_and_store_zotero_reading_map",
        fail_map,
    )

    summary = import_from_zotero(
        project_dir=project_dir,
        client=FakeZoteroClient(),
        build_reading_map=True,
        min_chars=1,
    )

    assert summary.last_version_after == 7
    assert summary.map_run_id is None
    assert any("sync completed" in warning for warning in summary.warnings)
    assert _source_cursor(resolve_database_path(project_dir)) == 7


def test_zotero_cursor_never_moves_backward(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    import_from_zotero(
        project_dir=project_dir,
        client=FakeZoteroClient(),
        build_reading_map=False,
        min_chars=1,
    )
    database_path = resolve_database_path(project_dir)
    with sqlite3.connect(database_path) as connection:
        connection.execute("UPDATE zotero_sources SET last_version = 10")
        connection.commit()

    summary = import_from_zotero(
        project_dir=project_dir,
        client=FakeZoteroClient(),
        build_reading_map=False,
        min_chars=1,
    )

    assert summary.last_version_before == 10
    assert summary.last_version_after == 10
    assert _source_cursor(database_path) == 10


def test_identical_zotero_sync_preserves_chunks_and_vectors(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    import_from_zotero(
        project_dir=project_dir,
        client=FakeZoteroClient(),
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )
    build_embeddings(
        project_dir=project_dir,
        model="unused",
        object_type="both",
        encoder=_RecordingEncoder(lambda: False),
    )
    database_path = resolve_database_path(project_dir)
    with sqlite3.connect(database_path) as connection:
        before_chunks = connection.execute(
            "SELECT id, document_id, chunk_index, text FROM chunks ORDER BY id"
        ).fetchall()
        before_vectors = connection.execute(
            """
            SELECT id, object_type, object_id, text_sha256, vector, updated_at
            FROM vectors
            ORDER BY id
            """
        ).fetchall()

    summary = import_from_zotero(
        project_dir=project_dir,
        client=FakeZoteroClient(),
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )

    with sqlite3.connect(database_path) as connection:
        after_chunks = connection.execute(
            "SELECT id, document_id, chunk_index, text FROM chunks ORDER BY id"
        ).fetchall()
        after_vectors = connection.execute(
            """
            SELECT id, object_type, object_id, text_sha256, vector, updated_at
            FROM vectors
            ORDER BY id
            """
        ).fetchall()
    assert summary.items_unchanged == 3
    assert after_chunks == before_chunks
    assert after_vectors == before_vectors


def test_zotero_older_item_cannot_overwrite_newer_record_or_document(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"

    class VersionedClient(FakeZoteroClient):
        def __init__(self, version: int, title: str) -> None:
            super().__init__()
            self.version = version
            self.title = title

        def top_items(
            self,
            *,
            limit: int | None = None,
            start: int = 0,
            since: int | None = None,
        ) -> list[dict[str, object]]:
            rows = copy.deepcopy(
                super().top_items(limit=limit, start=start, since=since)
            )
            assert rows
            rows[0]["version"] = self.version
            data = rows[0]["data"]
            assert isinstance(data, dict)
            data["version"] = self.version
            data["title"] = self.title
            return rows[:1]

    import_from_zotero(
        project_dir=project_dir,
        client=VersionedClient(11, "Newer title"),
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )
    summary = import_from_zotero(
        project_dir=project_dir,
        client=VersionedClient(10, "Older title"),
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )

    database_path = resolve_database_path(project_dir)
    with sqlite3.connect(database_path) as connection:
        item = connection.execute(
            "SELECT version, title FROM zotero_items WHERE zotero_key = 'AAAA1111'"
        ).fetchone()
        text = connection.execute(
            """
            SELECT dt.text
            FROM document_texts dt
            JOIN zotero_document_links zdl ON zdl.document_id = dt.document_id
            JOIN zotero_items zi ON zi.id = zdl.zotero_item_id
            WHERE zi.zotero_key = 'AAAA1111'
            """
        ).fetchone()
    assert summary.last_version_after == 11
    assert item == (11, "Newer title")
    assert text is not None and "Newer title" in text[0]
    assert "Older title" not in text[0]
    assert _source_cursor(database_path) == 11


@pytest.mark.parametrize(
    "conflict_kind",
    [
        "item",
        "collection",
        "attachment",
        "note",
        "annotation",
        "omitted_child",
        "missing_collection",
    ],
)
def test_zotero_mixed_version_payload_fails_without_advancing_cursor(
    tmp_path: Path,
    conflict_kind: str,
) -> None:
    project_dir = tmp_path / "project"
    import_from_zotero(
        project_dir=project_dir,
        client=_VersionGraphClient(),
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )

    with pytest.raises(RuntimeError, match="regressive or divergent"):
        import_from_zotero(
            project_dir=project_dir,
            client=_VersionGraphClient(conflict_kind=conflict_kind),
            pdf_policy="metadata",
            build_reading_map=False,
            min_chars=1,
        )

    database_path = resolve_database_path(project_dir)
    with sqlite3.connect(database_path) as connection:
        item = connection.execute(
            "SELECT version, title FROM zotero_items WHERE zotero_key = 'AAAA1111'"
        ).fetchone()
        attachment = connection.execute(
            """
            SELECT version, filename
            FROM zotero_attachments
            WHERE zotero_key = 'ATTACH11'
            """
        ).fetchone()
        collection = connection.execute(
            """
            SELECT version, name
            FROM zotero_collections
            WHERE zotero_key = 'COLLREAD'
            """
        ).fetchone()
        text = connection.execute(
            """
            SELECT dt.text
            FROM document_texts dt
            JOIN zotero_document_links zdl ON zdl.document_id = dt.document_id
            JOIN zotero_items zi ON zi.id = zdl.zotero_item_id
            WHERE zi.zotero_key = 'AAAA1111'
            """
        ).fetchone()
        runs = connection.execute(
            """
            SELECT status, error_code
            FROM zotero_import_runs
            ORDER BY started_at, rowid
            """
        ).fetchall()
    assert item == (11, "NEW PARENT")
    assert attachment == (11, "NEW_ATTACHMENT.pdf")
    assert collection == (11, "NEW COLLECTION")
    assert text is not None
    assert all(
        marker in text[0]
        for marker in (
            "NEW PARENT",
            "NEW COLLECTION",
            "NEW_ATTACHMENT.pdf",
            "NEW NOTE",
            "NEW ANNOTATION",
        )
    )
    assert "OLD" not in text[0]
    assert runs == [("completed", None), ("failed", "ZoteroVersionConflictError")]
    assert _source_cursor(database_path) == 11


def test_zotero_newer_child_with_unchanged_parent_is_accepted(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    import_from_zotero(
        project_dir=project_dir,
        client=_VersionGraphClient(),
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )

    summary = import_from_zotero(
        project_dir=project_dir,
        client=_VersionGraphClient(newer_attachment=True),
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )

    database_path = resolve_database_path(project_dir)
    with sqlite3.connect(database_path) as connection:
        attachment = connection.execute(
            """
            SELECT version, filename
            FROM zotero_attachments
            WHERE zotero_key = 'ATTACH11'
            """
        ).fetchone()
        text = connection.execute("SELECT text FROM document_texts").fetchone()
    assert summary.last_version_after == 11
    assert attachment == (12, "NEWER_ATTACHMENT.pdf")
    assert text is not None and "NEWER_ATTACHMENT.pdf" in text[0]
    assert _source_cursor(database_path) == 11


def test_zotero_unknown_legacy_child_state_requires_reconciliation(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    import_from_zotero(
        project_dir=project_dir,
        client=_VersionGraphClient(),
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
    )
    database_path = resolve_database_path(project_dir)
    with sqlite3.connect(database_path) as connection:
        connection.execute("UPDATE zotero_items SET child_manifest_json = NULL")
        connection.commit()

    with pytest.raises(RuntimeError, match="legacy child state"):
        import_from_zotero(
            project_dir=project_dir,
            client=_VersionGraphClient(conflict_kind="omitted_child"),
            pdf_policy="metadata",
            build_reading_map=False,
            min_chars=1,
        )

    with sqlite3.connect(database_path) as connection:
        item = connection.execute(
            "SELECT version, title, child_manifest_json FROM zotero_items"
        ).fetchone()
        text = connection.execute("SELECT text FROM document_texts").fetchone()
    assert item == (11, "NEW PARENT", None)
    assert text is not None and "NEW NOTE" in text[0]
    assert _source_cursor(database_path) == 11

    baseline = import_from_zotero(
        project_dir=project_dir,
        client=_VersionGraphClient(conflict_kind="parent_update"),
        pdf_policy="metadata",
        build_reading_map=False,
        min_chars=1,
        force=True,
    )
    assert baseline.last_version_after == 12
    assert any(
        "legacy child-version baseline" in warning for warning in baseline.warnings
    )
    with sqlite3.connect(database_path) as connection:
        manifest = connection.execute(
            "SELECT child_manifest_json FROM zotero_items"
        ).fetchone()
    assert manifest is not None and manifest[0]

    with pytest.raises(RuntimeError, match="regressive or divergent"):
        import_from_zotero(
            project_dir=project_dir,
            client=_VersionGraphClient(conflict_kind="omitted_child"),
            pdf_policy="metadata",
            build_reading_map=False,
            min_chars=1,
        )
    assert _source_cursor(database_path) == 12


class _VersionGraphClient(FakeZoteroClient):
    def __init__(
        self,
        *,
        conflict_kind: str | None = None,
        newer_attachment: bool = False,
    ) -> None:
        super().__init__()
        self.conflict_kind = conflict_kind
        self.newer_attachment = newer_attachment

    def collections(self, *, limit: int | None = None) -> list[dict[str, object]]:
        rows = copy.deepcopy(super().collections(limit=limit))
        if self.conflict_kind == "missing_collection":
            return rows[1:]
        row = rows[0]
        data = row["data"]
        assert isinstance(data, dict)
        version = 10 if self.conflict_kind == "collection" else 11
        name = (
            "OLD COLLECTION" if self.conflict_kind == "collection" else "NEW COLLECTION"
        )
        row["version"] = version
        data["version"] = version
        data["name"] = name
        return rows

    def top_items(
        self,
        *,
        limit: int | None = None,
        start: int = 0,
        since: int | None = None,
    ) -> list[dict[str, object]]:
        rows = copy.deepcopy(super().top_items(limit=limit, start=start, since=since))[
            :1
        ]
        row = rows[0]
        data = row["data"]
        assert isinstance(data, dict)
        version = 11 if self.conflict_kind == "item" or self.newer_attachment else 12
        if self.conflict_kind is None and not self.newer_attachment:
            version = 11
        title = "OLD PARENT" if self.conflict_kind == "item" else "NEW PARENT"
        row["version"] = version
        data["version"] = version
        data["title"] = title
        return rows

    def item_children(self, item_key: str) -> list[dict[str, object]]:
        rows = copy.deepcopy(super().item_children(item_key))
        if self.conflict_kind == "omitted_child":
            return []
        attachment = rows[0]
        attachment_data = attachment["data"]
        assert isinstance(attachment_data, dict)
        attachment_version = 10 if self.conflict_kind == "attachment" else 11
        attachment_filename = (
            "OLD_ATTACHMENT.pdf"
            if self.conflict_kind == "attachment"
            else "NEW_ATTACHMENT.pdf"
        )
        if self.newer_attachment:
            attachment_version = 12
            attachment_filename = "NEWER_ATTACHMENT.pdf"
        attachment["version"] = attachment_version
        attachment_data["version"] = attachment_version
        attachment_data["filename"] = attachment_filename

        note = rows[1]
        note_data = note["data"]
        assert isinstance(note_data, dict)
        note_version = 10 if self.conflict_kind == "note" else 11
        note["version"] = note_version
        note_data["version"] = note_version
        note_data["note"] = (
            "<p>OLD NOTE</p>" if self.conflict_kind == "note" else "<p>NEW NOTE</p>"
        )

        annotation_version = 10 if self.conflict_kind == "annotation" else 11
        annotation_text = (
            "OLD ANNOTATION" if self.conflict_kind == "annotation" else "NEW ANNOTATION"
        )
        rows.append(
            {
                "key": "ANNOT111",
                "version": annotation_version,
                "data": {
                    "key": "ANNOT111",
                    "version": annotation_version,
                    "itemType": "annotation",
                    "parentItem": item_key,
                    "annotationType": "highlight",
                    "annotationText": annotation_text,
                },
            }
        )
        return rows


class _AdvancedVersionZoteroClient(FakeZoteroClient):
    def top_items(
        self,
        *,
        limit: int | None = None,
        start: int = 0,
        since: int | None = None,
    ) -> list[dict[str, object]]:
        rows = copy.deepcopy(super().top_items(limit=limit, start=start, since=since))
        if rows:
            rows[0]["version"] = 9
            data = rows[0].get("data")
            assert isinstance(data, dict)
            data["version"] = 9
        return rows


def _capture_writers(
    monkeypatch: pytest.MonkeyPatch,
    module: object,
) -> tuple[list[sqlite3.Connection], Callable[[], bool]]:
    writers: list[sqlite3.Connection] = []

    def recording_connect(project_dir: Path | str) -> sqlite3.Connection:
        connection = connect_read_write(project_dir)
        writers.append(connection)
        return connection

    monkeypatch.setattr(module, "connect_read_write", recording_connect)

    def any_writer_in_transaction() -> bool:
        return any(_in_transaction(connection) for connection in writers)

    return writers, any_writer_in_transaction


def _in_transaction(connection: sqlite3.Connection) -> bool:
    try:
        return connection.in_transaction
    except sqlite3.ProgrammingError:
        return False


def _one_file_corpus(tmp_path: Path) -> Path:
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    (corpus_dir / "paper.txt").write_text(
        "A synthetic paper about local evidence and reproducible indexing.",
        encoding="utf-8",
    )
    return corpus_dir


def _fake_zotero_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "Zotero"
    pdf_dir = data_dir / "storage" / "ATTACH11"
    pdf_dir.mkdir(parents=True)
    (pdf_dir / "fourier.pdf").write_bytes(b"synthetic local PDF fixture")
    return data_dir


def _single_run(database_path: Path, table: str) -> sqlite3.Row:
    assert table in {"scan_runs", "embedding_runs"}
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(f"SELECT * FROM {table}").fetchall()
    assert len(rows) == 1
    return rows[0]


def _source_cursor(database_path: Path) -> int | None:
    with sqlite3.connect(database_path) as connection:
        row = connection.execute("SELECT last_version FROM zotero_sources").fetchone()
    assert row is not None
    return int(row[0]) if row[0] is not None else None
