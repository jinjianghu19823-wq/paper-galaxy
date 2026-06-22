"""TF-IDF feature extraction."""

from __future__ import annotations

from typing import Any

from paper_galaxy.errors import MissingDependencyError


def compute_tfidf(texts: list[str]) -> tuple[Any, Any, list[str]]:
    """Compute an inspectable TF-IDF baseline for local documents."""

    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
    except ImportError as exc:
        raise MissingDependencyError("scikit-learn") from exc

    vectorizer = TfidfVectorizer(
        stop_words="english",
        ngram_range=(1, 2),
        min_df=1,
        max_df=1.0,
    )
    try:
        matrix = vectorizer.fit_transform(texts)
    except ValueError:
        vectorizer = TfidfVectorizer(
            stop_words=None,
            ngram_range=(1, 2),
            min_df=1,
            max_df=1.0,
            token_pattern=r"(?u)\b\w+\b",
        )
        matrix = vectorizer.fit_transform(texts)
    return (
        vectorizer,
        matrix,
        [str(term) for term in vectorizer.get_feature_names_out()],
    )


def top_terms_for_documents(
    matrix: Any, terms: list[str], *, limit: int = 8
) -> list[list[str]]:
    """Return top TF-IDF terms for each document row."""

    result: list[list[str]] = []
    for row_index in range(matrix.shape[0]):
        row = matrix.getrow(row_index)
        if row.nnz == 0:
            result.append([])
            continue
        ordered = row.data.argsort()[::-1][:limit]
        result.append([terms[int(row.indices[index])] for index in ordered])
    return result
