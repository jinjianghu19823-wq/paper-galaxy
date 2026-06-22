from pathlib import Path

from paper_galaxy.indexer import index_corpus
from paper_galaxy.search import search_index
from paper_galaxy.storage.sqlite import resolve_database_path
from tests.test_indexer import copy_tiny_corpus, fetch_document, scalar


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
    relative_path = "neural_operators/fourier_neural_operator.md"
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    missing_path = corpus / relative_path
    missing_path.unlink()
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)

    results = search_index("Fourier Neural", project_dir=tmp_path)

    assert all(result.relative_path != relative_path for result in results)


def test_search_can_include_missing_documents(tmp_path: Path) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    relative_path = "neural_operators/fourier_neural_operator.md"
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    (corpus / relative_path).unlink()
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)

    default_results = search_index("Fourier Neural", project_dir=tmp_path)
    missing_results = search_index(
        "Fourier Neural", project_dir=tmp_path, include_missing=True
    )

    assert all(result.relative_path != relative_path for result in default_results)
    assert any(result.relative_path == relative_path for result in missing_results)


def test_unchanged_missing_document_reappears_active_and_searchable(
    tmp_path: Path,
) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    relative_path = "neural_operators/fourier_neural_operator.md"
    target = corpus / relative_path
    original = target.read_text(encoding="utf-8")

    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    database_path = resolve_database_path(tmp_path)
    before = fetch_document(database_path, relative_path)
    target.unlink()
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    missing = fetch_document(database_path, relative_path)
    target.write_text(original, encoding="utf-8")
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    after = fetch_document(database_path, relative_path)

    results = search_index("Fourier Neural", project_dir=tmp_path)

    assert missing["status"] == "missing"
    assert after["id"] == before["id"]
    assert after["status"] == "active"
    assert any(result.relative_path == relative_path for result in results)


def test_existing_document_that_becomes_too_short_is_not_searchable(
    tmp_path: Path,
) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    relative_path = "neural_operators/fourier_neural_operator.md"
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    database_path = resolve_database_path(tmp_path)
    target = corpus / relative_path
    target.write_text("tiny", encoding="utf-8")

    summary = index_corpus(corpus, project_dir=tmp_path, min_chars=40)
    row = fetch_document(database_path, relative_path)
    default_results = search_index("Fourier Neural", project_dir=tmp_path)
    missing_results = search_index(
        "Fourier Neural", project_dir=tmp_path, include_missing=True
    )

    assert summary.skipped_files == 1
    assert row["status"] == "unindexed"
    assert (
        scalar(
            database_path,
            "SELECT COUNT(*) FROM documents WHERE status = 'missing'",
        )
        == 0
    )
    assert all(result.relative_path != relative_path for result in default_results)
    assert all(result.relative_path != relative_path for result in missing_results)


def test_malformed_fts_query_does_not_raise_raw_sqlite_error(
    tmp_path: Path,
) -> None:
    corpus = copy_tiny_corpus(tmp_path)
    index_corpus(corpus, project_dir=tmp_path, min_chars=40)

    results = search_index('"neural operator', project_dir=tmp_path)

    assert results
