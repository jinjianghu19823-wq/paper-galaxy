from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from paper_galaxy.embeddings import search as search_module
from paper_galaxy.embeddings.builder import (
    build_document_embedding_text,
    build_embeddings,
)
from paper_galaxy.embeddings.codec import encode_vector
from paper_galaxy.embeddings.models import SemanticResultSource, text_sha256
from paper_galaxy.embeddings.ranking import VectorCandidate
from paper_galaxy.embeddings.search import NoVectorsFoundError, semantic_search
from paper_galaxy.embeddings.sentence_transformers import ModelFingerprintError
from paper_galaxy.embeddings.similarity import (
    MAX_NEIGHBOR_RESULTS,
    _candidate_matches_document,
    _rank_neighbors,
    compare_neighbors,
)
from paper_galaxy.indexer import index_corpus
from paper_galaxy.records import IndexedDocument
from paper_galaxy.storage import sqlite as sqlite_storage
from paper_galaxy.storage.provenance import document_content_revision_sha256
from paper_galaxy.storage.repository import Repository
from paper_galaxy.storage.sqlite import resolve_database_path
from tests.test_indexer import copy_tiny_corpus


@dataclass
class FakeEncoder:
    model_name: str = "fake-local-encoder"
    dimension: int = 3
    model_fingerprint: str = "1" * 64
    fingerprint_algorithm: str = "synthetic-test-fingerprint-v1"

    def encode(
        self,
        texts: list[str],
        *,
        batch_size: int = 32,
        normalize: bool = True,
    ) -> list[list[float]]:
        del batch_size
        vectors = [_fake_vector(text) for text in texts]
        if not normalize:
            return vectors
        return [_normalize(vector) for vector in vectors]


def test_embedding_builder_embeds_skips_and_forces_vectors(tmp_path: Path) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    encoder = FakeEncoder()

    first = build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="both",
        limit=2,
        encoder=encoder,
    )
    second = build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="both",
        limit=2,
        encoder=encoder,
    )
    forced = build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="document",
        limit=2,
        force=True,
        encoder=encoder,
    )

    assert first.documents_seen == 2
    assert first.documents_embedded == 2
    assert first.chunks_seen == 2
    assert first.chunks_embedded == 2
    assert second.documents_unchanged == 2
    assert second.chunks_unchanged == 2
    assert forced.documents_embedded == 2


def test_semantic_search_uses_stored_document_vectors(tmp_path: Path) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    encoder = FakeEncoder()
    build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="document",
        encoder=encoder,
    )

    results = semantic_search(
        "neural operator",
        project_dir=tmp_path,
        model="unused",
        encoder=encoder,
        limit=5,
    )

    assert results
    assert results[0].score >= 0.99
    assert any("neural_operators" in result.relative_path for result in results)


def test_semantic_search_can_use_unnormalized_vector_identity(tmp_path: Path) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    encoder = FakeEncoder()
    build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="document",
        encoder=encoder,
        normalize=False,
    )

    results = semantic_search(
        "neural operator",
        project_dir=tmp_path,
        model="unused",
        encoder=encoder,
        limit=5,
        normalize=False,
    )

    assert results
    assert results[0].score >= 0.99


@pytest.mark.parametrize("object_type", ["document", "chunk"])
def test_semantic_search_uses_constant_query_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    object_type: str,
) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    encoder = FakeEncoder()
    build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type=object_type,
        encoder=encoder,
    )
    statements: list[str] = []
    connection = sqlite_storage.connect_read_only(tmp_path)
    connection.set_trace_callback(statements.append)
    monkeypatch.setattr(search_module, "connect_read_only", lambda _: connection)

    results = semantic_search(
        "neural operator",
        project_dir=tmp_path,
        model="unused",
        encoder=encoder,
        object_type=object_type,
        limit=5,
    )

    selects = [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith("SELECT")
    ]
    assert results
    assert len(selects) <= 2


def test_compare_neighbors_returns_tfidf_dense_and_hybrid_lists(
    tmp_path: Path,
) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    encoder = FakeEncoder()
    build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="document",
        encoder=encoder,
    )

    comparison = compare_neighbors(
        "neural_operators/fourier_neural_operator.md",
        project_dir=tmp_path,
        model="unused",
        encoder=encoder,
        limit=3,
    )

    assert comparison.target.relative_path.endswith("fourier_neural_operator.md")
    assert comparison.tfidf_neighbors
    assert comparison.dense_neighbors
    assert comparison.hybrid_neighbors


def test_compare_neighbors_can_use_unnormalized_vector_identity(
    tmp_path: Path,
) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    encoder = FakeEncoder()
    build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="document",
        encoder=encoder,
        normalize=False,
    )

    comparison = compare_neighbors(
        "neural_operators/fourier_neural_operator.md",
        project_dir=tmp_path,
        model="unused",
        encoder=encoder,
        limit=3,
        normalize=False,
    )

    assert comparison.dense_neighbors
    assert comparison.hybrid_neighbors


def test_compare_neighbors_retries_when_documents_change_between_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    target_source = corpus / "a.md"
    neighbor_source = corpus / "b.md"
    target_source.write_text(
        "# Before A\n\nshared bridge exactterm synthetic evidence",
        encoding="utf-8",
    )
    neighbor_source.write_text(
        "# Before B\n\nshared bridge exactterm synthetic evidence",
        encoding="utf-8",
    )
    index_corpus(corpus, project_dir=tmp_path, min_chars=1)
    encoder = FakeEncoder()
    build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="document",
        encoder=encoder,
    )
    original = Repository.list_documents_with_text
    triggered = False

    def reindex_after_first_document_snapshot(
        repository: Repository,
        *,
        statuses: set[str] | None = None,
        limit: int = 1000,
    ) -> list[tuple[IndexedDocument, str]]:
        nonlocal triggered
        rows = original(repository, statuses=statuses, limit=limit)
        if triggered:
            return rows
        triggered = True
        target_source.write_text(
            "# After A\n\ncurrent operator evidence",
            encoding="utf-8",
        )
        neighbor_source.write_text(
            "# After B\n\nastronomy telescope orbit galaxies",
            encoding="utf-8",
        )
        index_corpus(corpus, project_dir=tmp_path, min_chars=1)
        build_embeddings(
            project_dir=tmp_path,
            model="unused",
            object_type="document",
            encoder=encoder,
        )
        return rows

    monkeypatch.setattr(
        Repository,
        "list_documents_with_text",
        reindex_after_first_document_snapshot,
    )

    comparison = compare_neighbors(
        "a.md",
        project_dir=tmp_path,
        model="unused",
        encoder=encoder,
        limit=5,
    )

    assert triggered is True
    assert comparison.target.title == "After A"
    b_neighbor = next(
        item for item in comparison.tfidf_neighbors if item.relative_path == "b.md"
    )
    assert b_neighbor.score == 0.0


def test_changed_document_prunes_document_chunk_vectors_and_index_metadata(
    tmp_path: Path,
) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    encoder = FakeEncoder()
    build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="both",
        encoder=encoder,
    )
    database_path = resolve_database_path(tmp_path)
    relative_path = "neural_operators/fourier_neural_operator.md"
    with sqlite3.connect(database_path) as connection:
        document_id = connection.execute(
            "SELECT id FROM documents WHERE relative_path = ?",
            (relative_path,),
        ).fetchone()[0]
        model_id = connection.execute("SELECT id FROM embedding_models").fetchone()[0]
        old_chunk_ids = {
            row[0]
            for row in connection.execute(
                "SELECT id FROM chunks WHERE document_id = ?", (document_id,)
            ).fetchall()
        }
        connection.executemany(
            """
            INSERT INTO vector_indexes(
              id, model_id, object_type, index_path, vector_count,
              created_at, metadata_json
            ) VALUES (?, ?, ?, ?, 1, '2026-07-11T00:00:00+00:00', '{}')
            """,
            [
                ("index_document", model_id, "document", "document.index"),
                ("index_chunk", model_id, "chunk", "chunk.index"),
            ],
        )
        connection.commit()

    source = corpus / relative_path
    source.write_text(
        source.read_text(encoding="utf-8")
        + "\n\nA changed synthetic paragraph invalidates old dense vectors.\n",
        encoding="utf-8",
    )
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)

    with sqlite3.connect(database_path) as connection:
        document_vectors = connection.execute(
            """
            SELECT COUNT(*) FROM vectors
            WHERE object_type = 'document' AND object_id = ?
            """,
            (document_id,),
        ).fetchone()[0]
        old_chunk_vectors = connection.execute(
            f"""
            SELECT COUNT(*) FROM vectors
            WHERE object_type = 'chunk'
              AND object_id IN ({", ".join("?" for _ in old_chunk_ids)})
            """,
            tuple(old_chunk_ids),
        ).fetchone()[0]
        remaining_vectors = connection.execute(
            "SELECT COUNT(*) FROM vectors"
        ).fetchone()[0]
        vector_indexes = connection.execute(
            "SELECT COUNT(*) FROM vector_indexes"
        ).fetchone()[0]
    assert document_vectors == 0
    assert old_chunk_vectors == 0
    assert remaining_vectors > 0
    assert vector_indexes == 0


def test_model_content_fingerprint_changes_identity_and_rebuilds_vectors(
    tmp_path: Path,
) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    first_encoder = FakeEncoder(model_fingerprint="a" * 64)
    second_encoder = FakeEncoder(model_fingerprint="b" * 64)

    first = build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="document",
        encoder=first_encoder,
    )
    second = build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="document",
        encoder=second_encoder,
    )

    assert first.model_id != second.model_id
    assert second.documents_embedded == second.documents_seen
    assert second.documents_unchanged == 0


def test_embedding_rejects_unverifiable_encoder_identity(tmp_path: Path) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)

    with pytest.raises(ModelFingerprintError, match="deterministic model fingerprint"):
        build_embeddings(
            project_dir=tmp_path,
            model="unused",
            object_type="document",
            encoder=FakeEncoder(model_fingerprint="legacy-unknown"),
        )


def test_embedding_commit_uses_source_revision_compare_and_swap(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    source = corpus / "paper.md"
    source.write_text(
        "# Before\n\nThe initial synthetic text is long enough to index.",
        encoding="utf-8",
    )
    index_corpus(corpus, project_dir=tmp_path, min_chars=1)

    @dataclass
    class ReindexingEncoder(FakeEncoder):
        model_fingerprint: str = "c" * 64
        triggered: bool = False

        def encode(
            self,
            texts: list[str],
            *,
            batch_size: int = 32,
            normalize: bool = True,
        ) -> list[list[float]]:
            if not self.triggered:
                self.triggered = True
                source.write_text(
                    "# After\n\nThe replacement text must invalidate "
                    "in-flight vectors.",
                    encoding="utf-8",
                )
                index_corpus(corpus, project_dir=tmp_path, min_chars=1)
            return super().encode(
                texts,
                batch_size=batch_size,
                normalize=normalize,
            )

    summary = build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="document",
        encoder=ReindexingEncoder(),
    )

    database_path = resolve_database_path(tmp_path)
    with sqlite3.connect(database_path) as connection:
        vector_count = connection.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
        run = connection.execute(
            "SELECT status, sources_changed FROM embedding_runs WHERE id = ?",
            (summary.id,),
        ).fetchone()
    assert vector_count == 0
    assert summary.sources_changed == 1
    assert run == ("completed", 1)


def test_embedding_cas_rejects_changed_extraction_with_same_file_hash(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    source = corpus / "paper.md"
    source.write_text(
        "# Stable bytes\n\nSynthetic content whose file hash does not change.",
        encoding="utf-8",
    )
    index_corpus(corpus, project_dir=tmp_path, min_chars=1)
    database_path = resolve_database_path(tmp_path)

    @dataclass
    class ReextractingEncoder(FakeEncoder):
        model_fingerprint: str = "d" * 64
        triggered: bool = False

        def encode(
            self,
            texts: list[str],
            *,
            batch_size: int = 32,
            normalize: bool = True,
        ) -> list[list[float]]:
            if not self.triggered:
                self.triggered = True
                replacement_title = "Re-extracted title"
                replacement_text = "A replacement extraction from identical bytes."
                replacement_revision = document_content_revision_sha256(
                    title=replacement_title,
                    relative_path="paper.md",
                    text=replacement_text,
                )
                with sqlite3.connect(database_path) as writer:
                    original_sha = writer.execute(
                        "SELECT sha256 FROM documents"
                    ).fetchone()[0]
                    writer.execute(
                        """
                        UPDATE documents
                        SET title = ?, content_revision_sha256 = ?
                        """,
                        (replacement_title, replacement_revision),
                    )
                    writer.execute(
                        "UPDATE document_texts SET text = ?",
                        (replacement_text,),
                    )
                    writer.commit()
                    assert (
                        writer.execute("SELECT sha256 FROM documents").fetchone()[0]
                        == original_sha
                    )
            return super().encode(
                texts,
                batch_size=batch_size,
                normalize=normalize,
            )

    summary = build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="document",
        encoder=ReextractingEncoder(),
    )

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM vectors").fetchone()[0] == 0
    assert summary.documents_embedded == 0
    assert summary.sources_changed == 1


def test_embedding_source_cas_holds_writer_lock_before_revision_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    original = Repository.current_vector_source_ids
    observed_transactions: list[bool] = []

    def assert_locked(
        repository: Repository,
        sources: dict[tuple[str, str], str],
    ) -> set[tuple[str, str]]:
        observed_transactions.append(repository.connection.in_transaction)
        return original(repository, sources)

    monkeypatch.setattr(Repository, "current_vector_source_ids", assert_locked)

    build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="document",
        batch_size=2,
        encoder=FakeEncoder(),
    )

    assert observed_transactions
    assert all(observed_transactions)


def test_semantic_search_drops_result_if_source_changes_after_ranking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    source_path = corpus / "paper.md"
    source_path.write_text("# Before\n\nSynthetic neural evidence.", encoding="utf-8")
    index_corpus(corpus, project_dir=tmp_path, min_chars=1)
    encoder = FakeEncoder()
    build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="document",
        encoder=encoder,
    )
    original = Repository.get_semantic_result_sources
    database_path = resolve_database_path(tmp_path)

    def reindex_before_source_load(
        repository: Repository,
        **kwargs: object,
    ) -> dict[str, SemanticResultSource]:
        with sqlite3.connect(database_path) as writer:
            writer.execute("UPDATE documents SET title = 'After re-extraction'")
            writer.execute(
                "UPDATE document_texts SET text = ?",
                ("Completely replaced privacy content.",),
            )
            writer.execute("DELETE FROM vectors")
            writer.commit()
        return original(repository, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        Repository,
        "get_semantic_result_sources",
        reindex_before_source_load,
    )

    results = semantic_search(
        "neural evidence",
        project_dir=tmp_path,
        model="unused",
        encoder=encoder,
    )

    assert results == []


def test_semantic_search_recomputes_document_embedding_input_hash(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "paper.md").write_text(
        "# Before\n\nSynthetic neural evidence.",
        encoding="utf-8",
    )
    index_corpus(corpus, project_dir=tmp_path, min_chars=1)
    encoder = FakeEncoder()
    build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="document",
        encoder=encoder,
    )
    replacement_title = "After"
    replacement_text = "Completely replaced privacy content."
    replacement_revision = document_content_revision_sha256(
        title=replacement_title,
        relative_path="paper.md",
        text=replacement_text,
    )
    with sqlite3.connect(resolve_database_path(tmp_path)) as connection:
        connection.execute(
            """
            UPDATE documents
            SET title = ?, content_revision_sha256 = ?
            """,
            (replacement_title, replacement_revision),
        )
        connection.execute(
            "UPDATE document_texts SET text = ?",
            (replacement_text,),
        )
        connection.execute(
            "UPDATE vectors SET source_content_sha256 = ?",
            (replacement_revision,),
        )
        connection.commit()

    results = semantic_search(
        "neural evidence",
        project_dir=tmp_path,
        model="unused",
        encoder=encoder,
    )

    assert results == []


def test_semantic_search_requires_exact_fingerprint_algorithm(tmp_path: Path) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    encoder = FakeEncoder()
    build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="document",
        encoder=encoder,
    )
    with sqlite3.connect(resolve_database_path(tmp_path)) as connection:
        connection.execute(
            "UPDATE embedding_models SET fingerprint_algorithm = 'untrusted'"
        )
        connection.commit()

    with pytest.raises(NoVectorsFoundError, match="No current vectors"):
        semantic_search(
            "neural operator",
            project_dir=tmp_path,
            model="unused",
            encoder=encoder,
        )
    tampered_config = {
        "normalize": False,
        "model_fingerprint": encoder.model_fingerprint,
        "fingerprint_algorithm": encoder.fingerprint_algorithm,
    }
    with sqlite3.connect(resolve_database_path(tmp_path)) as connection:
        connection.execute(
            """
            UPDATE embedding_models
            SET fingerprint_algorithm = ?, config_json = ?
            """,
            (
                encoder.fingerprint_algorithm,
                json.dumps(tampered_config, sort_keys=True),
            ),
        )
        connection.commit()

    with pytest.raises(NoVectorsFoundError, match="No current vectors"):
        semantic_search(
            "neural operator",
            project_dir=tmp_path,
            model="unused",
            encoder=encoder,
        )


def test_compare_neighbors_validates_weights_before_loading_project(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="finite, non-negative"):
        compare_neighbors(
            "paper.md",
            project_dir=tmp_path,
            model="unused",
            dense_weight=math.nan,
            encoder=FakeEncoder(),
        )
    with pytest.raises(ValueError, match="finite, non-negative"):
        compare_neighbors(
            "paper.md",
            project_dir=tmp_path,
            model="unused",
            dense_weight=1.79e308,
            tfidf_weight=1.79e308,
            encoder=FakeEncoder(),
        )


def test_neighbor_ranking_caps_output_and_breaks_all_ties_by_document_id() -> None:
    documents = {
        f"doc-{index:03d}": IndexedDocument(
            id=f"doc-{index:03d}",
            corpus_id="corpus",
            path=f"/synthetic/{index}.md",
            relative_path="same.md",
            file_type=".md",
            title=f"Document {index}",
            sha256=f"{index:064x}",
            size_bytes=1,
            mtime_ns=1,
            char_count=1,
            status="active",
            first_seen_at="2026-01-01",
            last_seen_at="2026-01-01",
            updated_at="2026-01-01",
        )
        for index in reversed(range(150))
    }
    scores = {document_id: 1.0 for document_id in reversed(tuple(documents))}

    ranked = _rank_neighbors(scores, documents, limit=10_000)

    assert len(ranked) == MAX_NEIGHBOR_RESULTS
    assert [item.document_id for item in ranked] == sorted(documents)[:100]


def test_compare_candidate_rejects_changed_extraction_with_same_file_hash() -> None:
    document = IndexedDocument(
        id="document",
        corpus_id="corpus",
        path="/synthetic/paper.md",
        relative_path="paper.md",
        file_type=".md",
        title="Synthetic",
        sha256="a" * 64,
        size_bytes=1,
        mtime_ns=1,
        char_count=20,
        status="active",
        first_seen_at="2026-01-01",
        last_seen_at="2026-01-01",
        updated_at="2026-01-01",
    )
    before = "Original neural extraction."
    content_revision = document_content_revision_sha256(
        title=document.title,
        relative_path=document.relative_path,
        text=before,
    )
    document = replace(document, content_revision_sha256=content_revision)
    candidate = VectorCandidate(
        object_id=document.id,
        relative_path=document.relative_path,
        chunk_index=None,
        dimension=3,
        blob=encode_vector([1.0, 0.0, 0.0]),
        source_content_sha256=content_revision,
        text_sha256=text_sha256(build_document_embedding_text(document, before)),
        metadata={"max_document_chars": 8000},
    )

    assert _candidate_matches_document(candidate, document, before) is True
    assert (
        _candidate_matches_document(
            candidate,
            document,
            "Completely replaced privacy extraction.",
        )
        is False
    )


def test_compare_neighbors_rejects_ambiguous_cross_corpus_relative_path(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    for name in ("first", "second"):
        corpus = tmp_path / name
        corpus.mkdir()
        (corpus / "paper.md").write_text(
            f"# {name}\n\nSynthetic evidence for {name} corpus.",
            encoding="utf-8",
        )
        index_corpus(corpus, project_dir=project, min_chars=1)

    with pytest.raises(ValueError, match="ambiguous across corpora"):
        compare_neighbors(
            "paper.md",
            project_dir=project,
            model="unused",
            encoder=FakeEncoder(),
        )


def test_chunking_configuration_change_forces_rechunk_and_vector_prune(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    source = corpus / "paper.md"
    source.write_text("# Synthetic\n\n" + ("evidence " * 120), encoding="utf-8")
    first = index_corpus(
        corpus,
        project_dir=tmp_path,
        min_chars=1,
        chunk_size=200,
        chunk_overlap=20,
    )
    build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="chunk",
        encoder=FakeEncoder(),
    )
    database_path = resolve_database_path(tmp_path)
    with sqlite3.connect(database_path) as connection:
        first_chunks = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

    second = index_corpus(
        corpus,
        project_dir=tmp_path,
        min_chars=1,
        chunk_size=80,
        chunk_overlap=10,
    )

    with sqlite3.connect(database_path) as connection:
        second_chunks = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        vector_count = connection.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
    assert first.documents_inserted == 1
    assert second.documents_unchanged == 0
    assert second.documents_updated == 1
    assert second_chunks != first_chunks
    assert vector_count == 0


def test_vector_upsert_invalidates_matching_index_metadata(tmp_path: Path) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    encoder = FakeEncoder()
    first = build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="document",
        limit=1,
        encoder=encoder,
    )
    database_path = resolve_database_path(tmp_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO vector_indexes(
              id, model_id, object_type, index_path, vector_count,
              created_at, metadata_json
            ) VALUES ('legacy-index', ?, 'document', 'legacy.index', 1, ?, '{}')
            """,
            (first.model_id, first.started_at),
        )
        connection.commit()

    build_embeddings(
        project_dir=tmp_path,
        model="unused",
        object_type="document",
        limit=2,
        encoder=encoder,
    )

    with sqlite3.connect(database_path) as connection:
        index_count = connection.execute(
            "SELECT COUNT(*) FROM vector_indexes"
        ).fetchone()[0]
    assert index_count == 0


def _fake_vector(text: str) -> list[float]:
    lowered = text.lower()
    if "neural" in lowered or "operator" in lowered:
        return [4.0, 0.0, 0.0]
    if "privacy" in lowered or "local" in lowered:
        return [0.0, 4.0, 0.0]
    return [0.0, 0.0, 4.0]


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector]
