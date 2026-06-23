from pathlib import Path

import pytest

from paper_galaxy.explain.pairs import explain_pair, pair_explanation_payload
from paper_galaxy.indexer import index_corpus
from paper_galaxy.storage.migrations import initialize_database
from paper_galaxy.storage.repository import Repository
from paper_galaxy.storage.sqlite import connect_database, resolve_database_path
from tests.test_indexer import copy_tiny_corpus


def test_pair_explanation_returns_shared_terms_and_short_excerpts(
    tmp_path: Path,
) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    repository = _repository(tmp_path)
    try:
        explanation = explain_pair(
            repository,
            "neural_operators/fourier_neural_operator.md",
            "neural_operators/deep_operator_network.txt",
            term_limit=6,
            chunk_limit=2,
        )
    finally:
        repository.connection.close()

    assert explanation.lexical_score > 0
    assert explanation.shared_terms
    assert any(
        "operator" in term.term or "neural" in term.term
        for term in explanation.shared_terms
    )
    assert explanation.chunk_matches
    assert all(len(match.source_excerpt) <= 230 for match in explanation.chunk_matches)
    assert all(len(match.target_excerpt) <= 230 for match in explanation.chunk_matches)


def test_pair_payload_does_not_include_full_document_text(tmp_path: Path) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    repository = _repository(tmp_path)
    try:
        full_text = repository.get_document_text(
            repository.get_document_by_id_or_relative_path(
                "neural_operators/fourier_neural_operator.md"
            ).id
        )
        explanation = explain_pair(
            repository,
            "neural_operators/fourier_neural_operator.md",
            "neural_operators/deep_operator_network.txt",
        )
    finally:
        repository.connection.close()

    payload = pair_explanation_payload(explanation)
    assert full_text
    assert full_text not in str(payload)
    assert "source_excerpt" in payload["chunk_matches"][0]


def test_pair_explanation_reports_unknown_documents(tmp_path: Path) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    repository = _repository(tmp_path)
    try:
        with pytest.raises(ValueError, match="No source document found"):
            explain_pair(repository, "missing", "also-missing")
    finally:
        repository.connection.close()


def _repository(project_dir: Path) -> Repository:
    connection = connect_database(project_dir)
    initialize_database(connection)
    return Repository(connection, resolve_database_path(project_dir))
