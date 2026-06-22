from pathlib import Path

from paper_galaxy.indexer import index_corpus
from paper_galaxy.search import search_index
from tests.test_indexer import copy_tiny_corpus


def test_search_returns_neural_operator_documents(tmp_path: Path) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)

    results = search_index("neural operator", project_dir=tmp_path)

    assert results
    assert any("neural_operators" in result.relative_path for result in results)
    assert any("Neural Operator" in result.title for result in results)


def test_search_limit_is_respected(tmp_path: Path) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)

    results = search_index("numerical", project_dir=tmp_path, limit=2)

    assert len(results) <= 2


def test_search_excludes_missing_documents_by_default(tmp_path: Path) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    missing_path = corpus / "neural_operators" / "fourier_neural_operator.md"
    missing_path.unlink()
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)

    results = search_index("Fourier Neural", project_dir=tmp_path)

    assert all(
        result.relative_path != "neural_operators/fourier_neural_operator.md"
        for result in results
    )


def test_malformed_fts_query_does_not_raise_raw_sqlite_error(
    tmp_path: Path,
) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)

    results = search_index('"neural operator', project_dir=tmp_path)

    assert results
