from __future__ import annotations

import hashlib
import json
import sqlite3
import struct
from pathlib import Path

import pytest

import paper_galaxy.validation as validation_module
from paper_galaxy.embeddings.builder import (
    CHUNK_VECTOR_ALGORITHM_VERSION,
    DOCUMENT_VECTOR_ALGORITHM_VERSION,
    build_embeddings,
)
from paper_galaxy.embeddings.codec import encode_vector
from paper_galaxy.embeddings.models import LEGACY_UNKNOWN_PROVENANCE, text_sha256
from paper_galaxy.indexer import index_corpus
from paper_galaxy.storage.migrations import (
    CURRENT_SCHEMA_VERSION,
    initialize_database,
)
from paper_galaxy.storage.provenance import document_content_revision_sha256
from paper_galaxy.storage.sqlite import connect_database, resolve_database_path
from paper_galaxy.validation import validate_project

NOW = "2026-07-11T00:00:00+00:00"
MODEL_FINGERPRINT = "a" * 64
FINGERPRINT_ALGORITHM = "synthetic-validation-fingerprint-v1"


class _ValidationEncoder:
    model_name = "validation-local-model"
    dimension = 2
    model_fingerprint = MODEL_FINGERPRINT
    fingerprint_algorithm = FINGERPRINT_ALGORITHM

    def encode(
        self,
        texts: list[str],
        *,
        batch_size: int = 32,
        normalize: bool = True,
    ) -> list[list[float]]:
        del batch_size, normalize
        return [[1.0, 0.0] for _ in texts]


def _initialize_project(project_dir: Path) -> Path:
    connection = connect_database(project_dir)
    try:
        initialize_database(connection)
        connection.commit()
    finally:
        connection.close()
    return resolve_database_path(project_dir)


def _connection(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    return connection


def _insert_corpus(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT INTO corpora(id, root_path, created_at, updated_at)
        VALUES ('corpus', '/synthetic/corpus', ?, ?)
        """,
        (NOW, NOW),
    )


def _insert_document(
    connection: sqlite3.Connection,
    document_id: str,
    *,
    text: str,
    with_chunk: bool = True,
    fts_text: str | None = None,
) -> str | None:
    relative_path = f"{document_id}.md"
    content_revision = document_content_revision_sha256(
        title=document_id,
        relative_path=relative_path,
        text=text,
    )
    connection.execute(
        """
        INSERT INTO documents(
          id, corpus_id, path, relative_path, file_type, title, sha256,
          content_revision_sha256, size_bytes, mtime_ns, char_count, status,
          first_seen_at, last_seen_at, updated_at
        )
        VALUES (?, 'corpus', ?, ?, '.md', ?, ?, ?, ?, 1, ?, 'active', ?, ?, ?)
        """,
        (
            document_id,
            f"/synthetic/corpus/{relative_path}",
            relative_path,
            document_id,
            hashlib.sha256(text.encode()).hexdigest(),
            content_revision,
            len(text.encode()),
            len(text),
            NOW,
            NOW,
            NOW,
        ),
    )
    connection.execute(
        "INSERT INTO document_texts(document_id, text) VALUES (?, ?)",
        (document_id, text),
    )
    chunk_id = None
    if with_chunk:
        chunk_id = f"chunk_{document_id}"
        connection.execute(
            """
            INSERT INTO chunks(
              id, document_id, chunk_index, text, char_count, text_sha256
            )
            VALUES (?, ?, 0, ?, ?, ?)
            """,
            (chunk_id, document_id, text, len(text), text_sha256(text)),
        )
    if fts_text is not None:
        connection.execute(
            """
            INSERT INTO documents_fts(document_id, title, relative_path, text)
            VALUES (?, ?, ?, ?)
            """,
            (document_id, document_id, relative_path, fts_text),
        )
    return chunk_id


def _issue_codes(report: dict[str, object]) -> set[str]:
    issues = report.get("issues")
    assert isinstance(issues, list)
    return {
        str(issue["code"])
        for issue in issues
        if isinstance(issue, dict) and "code" in issue
    }


def test_validation_never_initializes_or_modifies_an_existing_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = _initialize_project(tmp_path)
    before_bytes = database_path.read_bytes()
    before_hash = hashlib.sha256(before_bytes).hexdigest()
    before_mtime = database_path.stat().st_mtime_ns
    initialize_calls = 0

    def forbidden_initialize(connection: sqlite3.Connection) -> None:
        del connection
        nonlocal initialize_calls
        initialize_calls += 1
        raise AssertionError("validation must never initialize or migrate a database")

    monkeypatch.setattr(
        validation_module,
        "initialize_database",
        forbidden_initialize,
        raising=False,
    )

    report = validate_project(tmp_path, check_stale=False)

    assert initialize_calls == 0
    assert "schema_initialize_failed" not in _issue_codes(report)
    assert hashlib.sha256(database_path.read_bytes()).hexdigest() == before_hash
    assert database_path.read_bytes() == before_bytes
    assert database_path.stat().st_mtime_ns == before_mtime


def test_validation_does_not_create_a_missing_configured_database(
    tmp_path: Path,
) -> None:
    metadata_dir = tmp_path / ".paper-galaxy"
    metadata_dir.mkdir()
    (metadata_dir / "project.toml").write_text(
        "\n".join(
            [
                'project_name = "Validation test"',
                'database_path = ".paper-galaxy/nested/missing.sqlite3"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    database_path = metadata_dir / "nested" / "missing.sqlite3"

    report = validate_project(tmp_path, check_stale=False)

    assert report["status"] == "ERRORS"
    assert "database_missing" in _issue_codes(report)
    assert not database_path.exists()
    assert not database_path.parent.exists()


def test_validation_reports_sqlite_quick_check_and_foreign_key_check(
    tmp_path: Path,
) -> None:
    database_path = _initialize_project(tmp_path)
    connection = _connection(database_path)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            """
            INSERT INTO chunks(id, document_id, chunk_index, text, char_count)
            VALUES ('orphan_chunk', 'missing_document', 0, 'orphan', 6)
            """
        )
        connection.commit()
    finally:
        connection.close()

    report = validate_project(tmp_path, check_stale=False)
    integrity = report["integrity"]

    assert integrity["quick_check"] == {"ok": True, "messages": ["ok"]}
    assert integrity["foreign_key_check"]["ok"] is False
    assert integrity["foreign_key_check"]["violation_count"] == 1
    assert integrity["foreign_key_check"]["violations"][0]["table"] == "chunks"
    assert "foreign_key_check_failed" in _issue_codes(report)


def test_validation_reports_a_corrupt_database_without_raising(tmp_path: Path) -> None:
    database_path = _initialize_project(tmp_path)
    connection = _connection(database_path)
    try:
        connection.execute("PRAGMA writable_schema = ON")
        connection.execute(
            "UPDATE sqlite_schema SET rootpage = 999999 WHERE name = 'documents'"
        )
        connection.execute("PRAGMA writable_schema = OFF")
        connection.commit()
    finally:
        connection.close()

    report = validate_project(tmp_path, check_stale=False)

    assert report["status"] == "ERRORS"
    assert report["integrity"]["quick_check"]["ok"] is None
    assert report["integrity"]["quick_check"]["status"] == "not_run"
    assert report["integrity"]["quick_check"]["messages"]
    assert report["integrity"]["foreign_key_check"]["ok"] is None
    assert report["integrity"]["foreign_key_check"]["status"] == "not_run"
    assert "database_corrupt" in _issue_codes(report)


def test_validation_reports_missing_schema_capabilities(tmp_path: Path) -> None:
    database_path = _initialize_project(tmp_path)
    connection = _connection(database_path)
    try:
        connection.execute("ALTER TABLE scan_runs DROP COLUMN status")
        connection.commit()
    finally:
        connection.close()

    report = validate_project(tmp_path, check_stale=False)
    capability = report["schema_capability"]
    missing_columns = {
        (str(row["table"]), str(row["column"])) for row in capability["missing_columns"]
    }

    assert capability["ok"] is False
    assert ("scan_runs", "status") in missing_columns
    assert "schema_capability_missing" in _issue_codes(report)


def test_validation_capability_contract_includes_v8_provenance_columns() -> None:
    expected = {
        "scan_runs": {"owner_pid"},
        "documents": {"content_revision_sha256"},
        "chunks": {"text_sha256"},
        "embedding_models": {"model_fingerprint", "fingerprint_algorithm"},
        "vectors": {
            "source_content_sha256",
            "model_fingerprint",
            "algorithm_version",
        },
        "embedding_runs": {"sources_changed", "owner_pid"},
        "vector_indexes": {
            "model_fingerprint",
            "algorithm_version",
            "vector_set_sha256",
        },
        "zotero_import_runs": {"owner_pid"},
    }

    for table_name, columns in expected.items():
        assert columns <= validation_module.REQUIRED_COLUMNS[table_name]
    assert "faiss" not in {
        label for label, _module in validation_module.OPTIONAL_DEPENDENCIES
    }


def test_validation_reports_fts_document_and_chunk_inconsistency(
    tmp_path: Path,
) -> None:
    database_path = _initialize_project(tmp_path)
    connection = _connection(database_path)
    try:
        _insert_corpus(connection)
        _insert_document(
            connection,
            "missing_fts",
            text="document missing from FTS",
        )
        _insert_document(
            connection,
            "missing_chunk",
            text="document without a chunk",
            with_chunk=False,
            fts_text="document without a chunk",
        )
        _insert_document(
            connection,
            "wrong_fts_text",
            text="canonical document text",
            fts_text="stale FTS text",
        )
        connection.execute(
            """
            INSERT INTO documents_fts(document_id, title, relative_path, text)
            VALUES ('orphan_fts', 'Orphan', 'orphan.md', 'orphan text')
            """
        )
        connection.commit()
    finally:
        connection.close()

    report = validate_project(tmp_path, check_stale=False)
    fts = report["fts"]

    assert fts["available"] is True
    assert fts["documents_without_fts"] == 1
    assert fts["fts_without_documents"] == 1
    assert fts["documents_without_chunks"] == 1
    assert fts["content_mismatches"] == 1
    assert "fts_inconsistent" in _issue_codes(report)


def test_validation_rejects_plain_table_masquerading_as_fts5(tmp_path: Path) -> None:
    database_path = _initialize_project(tmp_path)
    connection = _connection(database_path)
    try:
        connection.execute("DROP TABLE documents_fts")
        connection.execute(
            """
            CREATE TABLE documents_fts (
              document_id TEXT,
              title TEXT,
              relative_path TEXT,
              text TEXT
            )
            """
        )
        connection.commit()
    finally:
        connection.close()

    report = validate_project(tmp_path, check_stale=False)

    assert report["status"] == "ERRORS"
    assert report["fts"]["available"] is False
    assert "FTS5 virtual table" in str(report["fts"]["error"])
    assert "fts_unavailable" in _issue_codes(report)


def test_validation_reports_orphan_stale_and_malformed_vectors(
    tmp_path: Path,
) -> None:
    database_path = _initialize_project(tmp_path)
    connection = _connection(database_path)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        _insert_corpus(connection)
        document_hashes: dict[str, str] = {}
        chunk_ids: dict[str, str] = {}
        for document_id in (
            "valid",
            "missing_model",
            "dimension_mismatch",
            "bad_blob",
            "inactive_document",
            "inactive_chunk",
            "legacy_provenance",
            "stale_document",
            "stale_chunk",
            "fingerprint_mismatch",
            "unknown_algorithm",
            "wrong_dtype",
            "nonfinite",
            "invalid_metadata",
            "bad_chunk_hash",
        ):
            value = f"text for {document_id}"
            document_hashes[document_id] = document_content_revision_sha256(
                title=document_id,
                relative_path=f"{document_id}.md",
                text=value,
            )
            chunk_id = _insert_document(connection, document_id, text=value)
            assert chunk_id is not None
            chunk_ids[document_id] = chunk_id
        connection.execute(
            "UPDATE documents SET status = 'missing' WHERE id = 'inactive_document'"
        )
        connection.execute(
            "UPDATE documents SET status = 'unindexed' WHERE id = 'inactive_chunk'"
        )
        connection.execute(
            "UPDATE chunks SET text_sha256 = ? WHERE id = ?",
            ("0" * 64, chunk_ids["bad_chunk_hash"]),
        )
        connection.execute(
            """
            INSERT INTO embedding_models(
              id, name, provider, dimension, distance, config_json,
              model_fingerprint, fingerprint_algorithm, created_at
            )
            VALUES (
              'model', 'synthetic', 'test', 2, 'cosine', '{}', ?, ?, ?
            )
            """,
            (MODEL_FINGERPRINT, FINGERPRINT_ALGORITHM, NOW),
        )

        def insert_vector(
            vector_id: str,
            *,
            model_id: str = "model",
            object_type: str,
            object_id: str,
            source_hash: str,
            dimension: int = 2,
            blob: bytes | None = None,
            dtype: str = "float32",
            model_fingerprint: str = MODEL_FINGERPRINT,
            algorithm_version: str | None = None,
            metadata_json: str = "{}",
        ) -> None:
            selected_algorithm = algorithm_version
            if selected_algorithm is None:
                selected_algorithm = (
                    CHUNK_VECTOR_ALGORITHM_VERSION
                    if object_type == "chunk"
                    else DOCUMENT_VECTOR_ALGORITHM_VERSION
                )
            connection.execute(
                """
                INSERT INTO vectors(
                  id, model_id, object_type, object_id, text_sha256,
                  source_content_sha256, model_fingerprint, algorithm_version,
                  dimension, dtype, vector, metadata_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 'weighted-input-hash', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    vector_id,
                    model_id,
                    object_type,
                    object_id,
                    source_hash,
                    model_fingerprint,
                    selected_algorithm,
                    dimension,
                    dtype,
                    blob if blob is not None else encode_vector([1.0, 0.0]),
                    metadata_json,
                    NOW,
                    NOW,
                ),
            )

        insert_vector(
            "vector_valid",
            object_type="document",
            object_id="valid",
            source_hash=document_hashes["valid"],
        )
        insert_vector(
            "vector_missing_document",
            object_type="document",
            object_id="absent_document",
            source_hash="0" * 64,
        )
        insert_vector(
            "vector_missing_chunk",
            object_type="chunk",
            object_id="absent_chunk",
            source_hash="0" * 64,
        )
        insert_vector(
            "vector_missing_model",
            model_id="absent_model",
            object_type="document",
            object_id="missing_model",
            source_hash=document_hashes["missing_model"],
        )
        insert_vector(
            "vector_dimension_mismatch",
            object_type="document",
            object_id="dimension_mismatch",
            source_hash=document_hashes["dimension_mismatch"],
            dimension=3,
            blob=encode_vector([1.0, 0.0, 0.0]),
        )
        insert_vector(
            "vector_bad_blob",
            object_type="document",
            object_id="bad_blob",
            source_hash=document_hashes["bad_blob"],
            blob=b"/private/secret-vector",
        )
        insert_vector(
            "vector_unknown_object",
            object_type="cluster",
            object_id="unsupported_target",
            source_hash="0" * 64,
        )
        insert_vector(
            "vector_inactive_document",
            object_type="document",
            object_id="inactive_document",
            source_hash=document_hashes["inactive_document"],
        )
        insert_vector(
            "vector_inactive_chunk",
            object_type="chunk",
            object_id=chunk_ids["inactive_chunk"],
            source_hash=text_sha256("text for inactive_chunk"),
        )
        insert_vector(
            "vector_stale_document",
            object_type="document",
            object_id="stale_document",
            source_hash="b" * 64,
        )
        insert_vector(
            "vector_stale_chunk",
            object_type="chunk",
            object_id=chunk_ids["stale_chunk"],
            source_hash="b" * 64,
        )
        insert_vector(
            "vector_fingerprint_mismatch",
            object_type="document",
            object_id="fingerprint_mismatch",
            source_hash=document_hashes["fingerprint_mismatch"],
            model_fingerprint="c" * 64,
        )
        insert_vector(
            "vector_legacy",
            object_type="document",
            object_id="legacy_provenance",
            source_hash=LEGACY_UNKNOWN_PROVENANCE,
            model_fingerprint=LEGACY_UNKNOWN_PROVENANCE,
            algorithm_version=LEGACY_UNKNOWN_PROVENANCE,
        )
        insert_vector(
            "vector_unknown_algorithm",
            object_type="document",
            object_id="unknown_algorithm",
            source_hash=document_hashes["unknown_algorithm"],
            algorithm_version="paper-galaxy-future-algorithm-v99",
        )
        insert_vector(
            "vector_wrong_dtype",
            object_type="document",
            object_id="wrong_dtype",
            source_hash=document_hashes["wrong_dtype"],
            dtype="float64",
        )
        insert_vector(
            "vector_nonfinite",
            object_type="document",
            object_id="nonfinite",
            source_hash=document_hashes["nonfinite"],
            blob=struct.pack("<2f", float("nan"), 0.0),
        )
        insert_vector(
            "vector_invalid_metadata",
            object_type="document",
            object_id="invalid_metadata",
            source_hash=document_hashes["invalid_metadata"],
            metadata_json='{broken:"/private/secret-metadata"}',
        )
        connection.execute(
            """
            INSERT INTO vector_indexes(
              id, model_id, object_type, index_path, vector_count,
              model_fingerprint, algorithm_version, vector_set_sha256,
              created_at, metadata_json
            ) VALUES (
              'stale-index', 'model', 'document', '/private/secret-index', 1,
              ?, ?, ?, ?, '{}'
            )
            """,
            (
                MODEL_FINGERPRINT,
                DOCUMENT_VECTOR_ALGORITHM_VERSION,
                LEGACY_UNKNOWN_PROVENANCE,
                NOW,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    report = validate_project(tmp_path, check_stale=False)
    vectors = report["vector_consistency"]

    assert vectors["unknown_object_types"] == 1
    assert vectors["vectors_without_targets"] == 2
    assert vectors["vectors_without_documents"] == 1
    assert vectors["vectors_without_chunks"] == 1
    assert vectors["vectors_without_models"] == 1
    assert vectors["vectors_for_inactive_targets"] == 2
    assert vectors["vectors_for_inactive_documents"] == 1
    assert vectors["vectors_for_inactive_chunks"] == 1
    assert vectors["document_source_hash_mismatches"] == 2
    assert vectors["chunk_source_hash_mismatches"] == 1
    assert vectors["source_hash_mismatches"] == 3
    assert vectors["chunk_text_hash_mismatches"] == 1
    assert vectors["model_fingerprint_mismatches"] == 2
    assert vectors["unknown_algorithm_versions"] == 2
    assert vectors["dimension_mismatches"] == 1
    assert vectors["dtype_mismatches"] == 1
    assert vectors["blob_size_mismatches"] == 1
    assert vectors["nonfinite_float32_vectors"] == 1
    assert vectors["invalid_vector_metadata"] == 1
    assert vectors["vector_index_provenance_mismatches"] == 1
    assert vectors["stale_vectors"] == 4
    assert vectors["unverifiable_vectors"] == 1
    assert "vector_consistency_failed" in _issue_codes(report)
    assert "vector_provenance_missing" in _issue_codes(report)
    vector_output = json.dumps(vectors, sort_keys=True)
    vector_issues = json.dumps(
        [
            issue
            for issue in report["issues"]
            if issue["code"]
            in {"vector_consistency_failed", "vector_provenance_missing"}
        ],
        sort_keys=True,
    )
    assert "/private/secret" not in vector_output
    assert "/private/secret" not in vector_issues


def test_validation_reports_zotero_cursor_and_profile_inconsistency(
    tmp_path: Path,
) -> None:
    database_path = _initialize_project(tmp_path)
    connection = _connection(database_path)
    try:
        connection.execute(
            """
            INSERT INTO zotero_sources(
              id, source_type, name, last_version, created_at, updated_at
            )
            VALUES ('behind', 'local_api', 'Behind cursor', 3, ?, ?),
                   ('negative', 'local_api', 'Invalid cursor', -1, ?, ?),
                   ('typed', 'local_api', 'Wrong cursor type', 'bad', ?, ?),
                   ('null_cursor', 'local_api', 'Missing cursor', NULL, ?, ?)
            """,
            (NOW, NOW, NOW, NOW, NOW, NOW, NOW, NOW),
        )
        connection.execute(
            """
            INSERT INTO zotero_items(
              id, source_id, zotero_key, version, item_type, title,
              reading_status, data_json, created_at, updated_at
            )
            VALUES (
              'item_newer_than_cursor', 'behind', 'ITEM1', 5,
              'journalArticle', 'Newer item', 'unknown', '{}', ?, ?
            )
            """,
            (NOW, NOW),
        )
        connection.execute(
            """
            INSERT INTO zotero_items(
              id, source_id, zotero_key, version, item_type, title,
              reading_status, data_json, child_manifest_json, created_at, updated_at
            ) VALUES (
              'item_without_cursor', 'null_cursor', 'ITEM3', 2,
              'journalArticle', 'Missing cursor item', 'unknown', '{}', '[1]', ?, ?
            )
            """,
            (NOW, NOW),
        )
        connection.execute(
            """
            INSERT INTO zotero_items(
              id, source_id, zotero_key, version, item_type, title,
              reading_status, data_json, created_at, updated_at
            ) VALUES (
              'item_bad_version', 'typed', 'ITEM2', 'bad',
              'journalArticle', 'Bad version', 'unknown', '{}', ?, ?
            )
            """,
            (NOW, NOW),
        )
        connection.execute(
            """
            INSERT INTO zotero_import_runs(
              id, source_id, started_at, finished_at, status,
              warnings_json, config_json
            )
            VALUES ('profile_mismatch', 'behind', ?, ?, 'completed', '[]', ?)
            """,
            (
                NOW,
                NOW,
                json.dumps(
                    {
                        "include_status": "all",
                        "since_version": 3,
                        "filters": {
                            "include_status": "to-read",
                            "since_version": 2,
                        },
                    },
                    sort_keys=True,
                ),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    report = validate_project(tmp_path, check_stale=False)
    zotero = report["zotero_consistency"]

    assert zotero["invalid_cursors"] == 2
    assert zotero["invalid_record_versions"] == 1
    assert zotero["cursor_behind_records"] == 2
    assert zotero["profile_mismatches"] == 1
    assert zotero["invalid_child_manifests"] == 1
    assert zotero["unknown_child_manifests"] == 2
    assert "zotero_consistency_failed" in _issue_codes(report)


def test_validation_reports_registered_source_and_job_inconsistency(
    tmp_path: Path,
) -> None:
    database_path = _initialize_project(tmp_path)
    connection = _connection(database_path)
    try:
        connection.execute(
            """
            INSERT INTO zotero_sources(
              id, source_type, local_api_url, library_id, library_type,
              name, created_at, updated_at
            ) VALUES (
              'zotero-source', 'local_api', 'http://localhost:23119/api',
              '0', 'user', 'Synthetic Zotero', ?, ?
            )
            """,
            (NOW, NOW),
        )
        connection.execute(
            """
            INSERT INTO registered_sources(
              id, kind, display_name, root_path, zotero_source_id,
              profile_signature, config_json, created_at, updated_at, removed_at
            ) VALUES (
              'removed-corpus', 'corpus_directory', 'Removed corpus',
              '/synthetic/removed', NULL, 'removed-signature', '{', ?, ?, ?
            ), (
              'zotero-profile', 'zotero_profile', 'Synthetic Zotero',
              NULL, 'zotero-source', 'zotero-signature', '{}', ?, ?, NULL
            )
            """,
            (NOW, NOW, NOW, NOW, NOW),
        )
        connection.execute(
            """
            INSERT INTO jobs(
              id, queue_sequence, kind, source_id, request_key, status,
              params_json, result_summary_json, created_at, updated_at
            ) VALUES (
              'missing-source', 1, 'index_corpus', NULL, 'request-1', 'queued',
              '{', '{}', ?, ?
            ), (
              'removed-source', 2, 'rebuild_analysis', 'removed-corpus',
              'request-2', 'queued', '{}', '[', ?, ?
            ), (
              'wrong-source-kind', 3, 'index_corpus', 'zotero-profile',
              'request-3', 'queued', '{}', '{}', ?, ?
            )
            """,
            (NOW, NOW, NOW, NOW, NOW, NOW),
        )
        connection.execute(
            """
            INSERT INTO jobs(
              id, queue_sequence, kind, source_id, request_key, status,
              params_json, result_summary_json, owner_pid, owner_instance_id,
              heartbeat_at, created_at, finished_at, updated_at
            ) VALUES (
              'stale-terminal-owner', 4, 'backup_project', NULL, 'request-4',
              'completed', '{}', '{}', 123, 'stale-worker', ?, ?, ?, ?
            )
            """,
            (NOW, NOW, NOW, NOW),
        )
        connection.commit()
    finally:
        connection.close()

    report = validate_project(tmp_path, check_stale=False)
    state = report["source_job_consistency"]

    assert report["tables"]["registered_sources"] is True
    assert report["tables"]["jobs"] is True
    assert report["counts"]["registered_sources"] == 2
    assert report["counts"]["jobs"] == 4
    assert state == {
        "check_errors": 0,
        "invalid_source_config_json": 1,
        "invalid_job_params_json": 1,
        "invalid_job_result_json": 1,
        "jobs_missing_required_source": 1,
        "jobs_with_unexpected_source": 1,
        "job_source_kind_mismatches": 1,
        "active_jobs_for_removed_sources": 1,
        "invalid_job_state_rows": 1,
    }
    assert "source_job_consistency_failed" in _issue_codes(report)
    assert "/synthetic/removed" not in json.dumps(state, sort_keys=True)


def test_vectors_built_by_current_pipeline_are_not_reported_stale(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "paper.md").write_text(
        "# Synthetic paper\n\nEvidence-first local analysis remains reproducible.",
        encoding="utf-8",
    )
    index_corpus(corpus, project_dir=tmp_path, min_chars=1)
    build_embeddings(
        project_dir=tmp_path,
        model="unused",
        encoder=_ValidationEncoder(),
    )

    report = validate_project(tmp_path, check_stale=False)

    assert report["vector_consistency"]["stale_vectors"] == 0
    assert "vector_consistency_failed" not in _issue_codes(report)


def test_validation_detects_stale_document_content_revision(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "paper.md").write_text(
        "# Original\n\nSynthetic evidence for revision validation.",
        encoding="utf-8",
    )
    index_corpus(corpus, project_dir=tmp_path, min_chars=1)
    build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="document",
        encoder=_ValidationEncoder(),
    )
    with sqlite3.connect(resolve_database_path(tmp_path)) as connection:
        connection.execute("UPDATE documents SET title = 'Tampered title'")
        connection.commit()

    report = validate_project(tmp_path, check_stale=False)

    assert report["vector_consistency"]["document_content_hash_mismatches"] == 1
    assert "vector_consistency_failed" in _issue_codes(report)


def test_validation_reports_real_dimension_and_odd_blob_without_crashing(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "paper.md").write_text(
        "# Synthetic paper\n\nA malformed vector must remain diagnosable.",
        encoding="utf-8",
    )
    index_corpus(corpus, project_dir=tmp_path, min_chars=1)
    build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="document",
        encoder=_ValidationEncoder(),
    )
    database_path = resolve_database_path(tmp_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute("UPDATE embedding_models SET dimension = 2.25")
        connection.execute(
            "UPDATE vectors SET dimension = 2.25, vector = ?",
            (sqlite3.Binary(b"123456789"),),
        )
        connection.commit()

    report = validate_project(tmp_path, check_stale=False)

    vectors = report["vector_consistency"]
    assert vectors["dimension_mismatches"] == 1
    assert vectors["blob_size_mismatches"] == 1
    assert vectors["nonfinite_float32_vectors"] == 0
    assert "vector_consistency_failed" in _issue_codes(report)


def test_validation_reports_tampered_content_addressed_model_identity(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "paper.md").write_text(
        "# Synthetic paper\n\nModel identity must remain content addressed.",
        encoding="utf-8",
    )
    index_corpus(corpus, project_dir=tmp_path, min_chars=1)
    encoder = _ValidationEncoder()
    build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="document",
        encoder=encoder,
    )
    tampered_config = {
        "normalize": False,
        "model_fingerprint": encoder.model_fingerprint,
        "fingerprint_algorithm": encoder.fingerprint_algorithm,
    }
    with sqlite3.connect(resolve_database_path(tmp_path)) as connection:
        connection.execute(
            "UPDATE embedding_models SET config_json = ?",
            (json.dumps(tampered_config, sort_keys=True),),
        )
        connection.commit()

    report = validate_project(tmp_path, check_stale=False)

    assert report["vector_consistency"]["model_identity_mismatches"] == 1
    assert "vector_consistency_failed" in _issue_codes(report)


def test_validation_reports_future_schema_without_calling_it_corrupt(
    tmp_path: Path,
) -> None:
    database_path = _initialize_project(tmp_path)
    connection = _connection(database_path)
    try:
        future = CURRENT_SCHEMA_VERSION + 1
        connection.execute(
            "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
            (str(future),),
        )
        connection.commit()
    finally:
        connection.close()

    report = validate_project(tmp_path, check_stale=False)

    assert report["schema_version"] == str(future)
    assert "future_schema" in _issue_codes(report)
    assert "quick_check_failed" not in _issue_codes(report)
    assert report["integrity"]["quick_check"]["status"] == "not_run"


def test_validation_reports_invalid_persisted_json(tmp_path: Path) -> None:
    database_path = _initialize_project(tmp_path)
    connection = _connection(database_path)
    try:
        connection.execute(
            """
            INSERT INTO zotero_sources(
              id, source_type, name, created_at, updated_at
            ) VALUES ('source', 'local_api', 'Synthetic', ?, ?)
            """,
            (NOW, NOW),
        )
        connection.execute(
            """
            INSERT INTO zotero_import_runs(
              id, source_id, started_at, status, warnings_json, config_json
            ) VALUES ('run', 'source', ?, 'failed', '[', '{}')
            """,
            (NOW,),
        )
        connection.commit()
    finally:
        connection.close()

    report = validate_project(tmp_path, check_stale=False)

    assert "stored_json_invalid" in _issue_codes(report)
    assert report["status"] == "ERRORS"


def test_validation_reports_repository_query_failures(tmp_path: Path) -> None:
    database_path = _initialize_project(tmp_path)
    connection = _connection(database_path)
    try:
        connection.execute("DROP INDEX idx_zotero_items_reading_status")
        connection.execute("ALTER TABLE zotero_items DROP COLUMN reading_status")
        connection.commit()
    finally:
        connection.close()

    report = validate_project(tmp_path, check_stale=False)

    assert report["status"] == "ERRORS"
    assert report["check_errors"] == 1
    assert report["zotero"] == {}
    failures = [
        issue
        for issue in report["issues"]
        if issue["code"] == "repository_check_failed"
    ]
    assert failures == [
        {
            "severity": "error",
            "code": "repository_check_failed",
            "message": (
                "Database validation check could not be completed: Zotero summary."
            ),
        }
    ]
    assert str(tmp_path) not in json.dumps(failures)


def test_validation_reports_incomplete_migration_history(tmp_path: Path) -> None:
    database_path = _initialize_project(tmp_path)
    connection = _connection(database_path)
    try:
        connection.execute("DELETE FROM schema_migrations")
        connection.commit()
    finally:
        connection.close()

    report = validate_project(tmp_path, check_stale=False)

    assert report["migration_history"]["ok"] is False
    assert "migration_history_invalid" in _issue_codes(report)
