from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from paper_galaxy.cli import app
from paper_galaxy.embeddings import maintenance as maintenance_module
from paper_galaxy.embeddings.builder import build_embeddings
from paper_galaxy.embeddings.maintenance import prune_stale_vectors
from paper_galaxy.embeddings.search import NoVectorsFoundError, semantic_search
from paper_galaxy.indexer import index_corpus
from paper_galaxy.storage.sqlite import resolve_database_path
from tests.test_embedding_builder_search import FakeEncoder

runner = CliRunner()


def test_prune_stale_vectors_is_dry_run_by_default_and_requires_yes(
    tmp_path: Path,
) -> None:
    corpus, source = _single_document_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=1)
    summary = build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="document",
        encoder=FakeEncoder(),
    )
    database_path = resolve_database_path(tmp_path)
    source_before = source.read_bytes()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE vectors SET source_content_sha256 = ?",
            ("f" * 64,),
        )
        connection.execute(
            """
            INSERT INTO vector_indexes(
              id, model_id, object_type, index_path, vector_count,
              model_fingerprint, algorithm_version, vector_set_sha256,
              created_at, metadata_json
            )
            VALUES (
              'stale-index', ?, 'document', 'stale.index', 1,
              ?, 'paper-galaxy-weighted-text-v1', 'legacy-unknown', ?, '{}'
            )
            """,
            (
                summary.model_id,
                FakeEncoder().model_fingerprint,
                summary.started_at,
            ),
        )
        connection.commit()

    report = prune_stale_vectors(tmp_path)

    assert report.dry_run is True
    assert report.vectors_scanned == 1
    assert report.stale_vectors == 1
    assert report.vectors_deleted == 0
    assert report.reasons["source_hash_mismatch"] == 1
    assert report.stale_index_metadata == 1
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM vectors").fetchone()[0] == 1
        assert (
            connection.execute("SELECT COUNT(*) FROM vector_indexes").fetchone()[0] == 1
        )

    with pytest.raises(ValueError, match="explicit --yes"):
        prune_stale_vectors(tmp_path, dry_run=False)

    applied = prune_stale_vectors(tmp_path, dry_run=False, yes=True)

    assert applied.dry_run is False
    assert applied.vectors_deleted == 1
    assert applied.index_metadata_deleted == 1
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM vectors").fetchone()[0] == 0
        assert (
            connection.execute("SELECT COUNT(*) FROM vector_indexes").fetchone()[0] == 0
        )
        assert connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
    assert source.read_bytes() == source_before


def test_semantic_search_rejects_vectors_with_unverifiable_provenance(
    tmp_path: Path,
) -> None:
    corpus, _ = _single_document_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=1)
    encoder = FakeEncoder()
    build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="document",
        encoder=encoder,
    )
    with sqlite3.connect(resolve_database_path(tmp_path)) as connection:
        connection.execute("UPDATE vectors SET model_fingerprint = 'legacy-unknown'")
        connection.commit()

    with pytest.raises(NoVectorsFoundError, match="No current vectors"):
        semantic_search(
            "synthetic evidence",
            project_dir=tmp_path,
            model="unused",
            encoder=encoder,
        )

    report = prune_stale_vectors(tmp_path)
    assert report.reasons["unknown_model_fingerprint"] == 1


def test_prune_stale_vectors_cli_requires_apply_and_yes(tmp_path: Path) -> None:
    corpus, _ = _single_document_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=1)
    build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="document",
        encoder=FakeEncoder(),
    )
    database_path = resolve_database_path(tmp_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute("UPDATE vectors SET algorithm_version = 'legacy-unknown'")
        connection.commit()

    dry_run = runner.invoke(
        app,
        ["prune-stale-vectors", "--project-dir", str(tmp_path)],
    )
    missing_confirmation = runner.invoke(
        app,
        ["prune-stale-vectors", "--project-dir", str(tmp_path), "--apply"],
    )
    applied = runner.invoke(
        app,
        [
            "prune-stale-vectors",
            "--project-dir",
            str(tmp_path),
            "--apply",
            "--yes",
        ],
    )

    assert dry_run.exit_code == 0
    assert "dry-run" in dry_run.output
    assert missing_confirmation.exit_code == 1
    assert "explicit --yes" in missing_confirmation.output
    assert applied.exit_code == 0
    assert "applied" in applied.output
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM vectors").fetchone()[0] == 0


def test_vector_maintenance_streams_across_bounded_pages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus, _ = _single_document_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=1)
    encoder = FakeEncoder()
    summary = build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="document",
        encoder=encoder,
    )
    database_path = resolve_database_path(tmp_path)
    with sqlite3.connect(database_path) as connection:
        connection.executemany(
            """
            INSERT INTO vectors(
              id, model_id, object_type, object_id, text_sha256,
              source_content_sha256, model_fingerprint, algorithm_version,
              dimension, dtype, vector, metadata_json, created_at, updated_at
            )
            VALUES (?, ?, 'unsupported', ?, ?, ?, ?, 'legacy-unknown',
                    3, 'float32', ?, '{}', ?, ?)
            """,
            (
                (
                    f"invalid-vector-{index:04d}",
                    summary.model_id,
                    f"object-{index:04d}",
                    "a" * 64,
                    "b" * 64,
                    encoder.model_fingerprint,
                    sqlite3.Binary(b"\0" * 12),
                    summary.started_at,
                    summary.finished_at,
                )
                for index in range(300)
            ),
        )
        connection.commit()
    monkeypatch.setattr(maintenance_module, "_AUDIT_BATCH_SIZE", 17)

    report = prune_stale_vectors(tmp_path)
    applied = prune_stale_vectors(tmp_path, dry_run=False, yes=True)

    assert report.vectors_scanned == 301
    assert report.stale_vectors == 300
    assert report.reasons["unknown_object_type"] == 300
    assert applied.vectors_deleted == 300
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM vectors").fetchone()[0] == 1


def test_text_storage_vector_is_filtered_and_pruned_without_type_error(
    tmp_path: Path,
) -> None:
    corpus, _ = _single_document_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=1)
    encoder = FakeEncoder()
    build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="document",
        encoder=encoder,
    )
    database_path = resolve_database_path(tmp_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute("UPDATE vectors SET vector = 'abcdefghijkl'")
        connection.commit()

    with pytest.raises(NoVectorsFoundError, match="No current vectors"):
        semantic_search(
            "synthetic evidence",
            project_dir=tmp_path,
            model="unused",
            encoder=encoder,
        )
    report = prune_stale_vectors(tmp_path)
    applied = prune_stale_vectors(tmp_path, dry_run=False, yes=True)

    assert report.reasons["blob_type_mismatch"] == 1
    assert applied.vectors_deleted == 1


def test_model_config_tampering_is_reported_and_pruned(tmp_path: Path) -> None:
    corpus, _ = _single_document_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=1)
    encoder = FakeEncoder()
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

    report = prune_stale_vectors(tmp_path)
    applied = prune_stale_vectors(tmp_path, dry_run=False, yes=True)

    assert report.reasons["model_identity_config_mismatch"] == 1
    assert applied.vectors_deleted == 1


def _single_document_corpus(tmp_path: Path) -> tuple[Path, Path]:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    source = corpus / "paper.md"
    source.write_text(
        "# Synthetic evidence\n\nA local-only paper with enough text to index.",
        encoding="utf-8",
    )
    return corpus, source
