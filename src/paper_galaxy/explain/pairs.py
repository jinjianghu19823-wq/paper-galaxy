"""Local lexical explanations for why two documents are nearby."""

from __future__ import annotations

import re

from paper_galaxy.errors import MissingDependencyError
from paper_galaxy.explain.labels import GENERIC_TERMS
from paper_galaxy.explain.models import (
    ChunkMatch,
    DocumentSummary,
    PairExplanation,
    TermScore,
)
from paper_galaxy.records import IndexedChunk
from paper_galaxy.storage.repository import Repository

_WORD_RE = re.compile(r"[a-z0-9]+")


def explain_pair(
    repository: Repository,
    source_ref: str,
    target_ref: str,
    *,
    term_limit: int = 8,
    chunk_limit: int = 3,
    dense: bool = False,
    model_id: str | None = None,
) -> PairExplanation:
    """Explain a document pair using transparent TF-IDF and chunk evidence."""

    source = repository.get_document_by_id_or_relative_path(source_ref)
    if source is None:
        raise ValueError(f"No source document found for {source_ref}.")
    target = repository.get_document_by_id_or_relative_path(target_ref)
    if target is None:
        raise ValueError(f"No target document found for {target_ref}.")
    source_text = repository.get_document_text(source.id) or ""
    target_text = repository.get_document_text(target.id) or ""
    lexical_score, shared_terms = _shared_terms(
        source_text,
        target_text,
        term_limit=max(0, term_limit),
    )
    chunk_matches = _chunk_matches(
        repository.get_document_chunks(source.id, limit=1_000_000_000),
        repository.get_document_chunks(target.id, limit=1_000_000_000),
        limit=max(0, chunk_limit),
    )
    warnings: list[str] = []
    if dense or model_id:
        warnings.append(
            "Dense pair evidence is not available; showing lexical evidence."
        )
    if not shared_terms:
        warnings.append("No shared high-weight terms found for this pair.")
    if not chunk_matches:
        warnings.append("No matching chunk excerpts found for this pair.")
    return PairExplanation(
        source=DocumentSummary(
            document_id=source.id,
            title=source.title,
            relative_path=source.relative_path,
        ),
        target=DocumentSummary(
            document_id=target.id,
            title=target.title,
            relative_path=target.relative_path,
        ),
        lexical_score=round(lexical_score, 4),
        shared_terms=shared_terms,
        chunk_matches=chunk_matches,
        warnings=warnings,
    )


def pair_explanation_payload(explanation: PairExplanation) -> dict[str, object]:
    """Convert a pair explanation to an API-safe payload."""

    return {
        "source": _document_payload(explanation.source),
        "target": _document_payload(explanation.target),
        "lexical_score": explanation.lexical_score,
        "dense_score": explanation.dense_score,
        "hybrid_score": explanation.hybrid_score,
        "shared_terms": [
            {"term": term.term, "score": term.score}
            for term in explanation.shared_terms
        ],
        "chunk_matches": [
            {
                "source_chunk_id": match.source_chunk_id,
                "source_chunk_index": match.source_chunk_index,
                "target_chunk_id": match.target_chunk_id,
                "target_chunk_index": match.target_chunk_index,
                "score": match.score,
                "shared_terms": match.shared_terms,
                "source_excerpt": match.source_excerpt,
                "target_excerpt": match.target_excerpt,
            }
            for match in explanation.chunk_matches
        ],
        "warnings": explanation.warnings,
    }


def _shared_terms(
    source_text: str, target_text: str, *, term_limit: int
) -> tuple[float, list[TermScore]]:
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
    except ImportError as exc:
        raise MissingDependencyError("scikit-learn") from exc

    vectorizer = TfidfVectorizer(
        stop_words="english",
        ngram_range=(1, 2),
        min_df=1,
        max_df=1.0,
        token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z0-9_-]{1,}\b",
    )
    matrix = vectorizer.fit_transform([source_text, target_text])
    terms = [str(term) for term in vectorizer.get_feature_names_out()]
    score = float(cosine_similarity(matrix[0], matrix[1])[0][0])
    source_values = matrix.getrow(0).toarray()[0]
    target_values = matrix.getrow(1).toarray()[0]
    shared_scores = source_values * target_values
    ordered = shared_scores.argsort()[::-1]
    result: list[TermScore] = []
    for index in ordered:
        value = float(shared_scores[int(index)])
        if value <= 0:
            break
        term = terms[int(index)]
        if not _is_informative_term(term):
            continue
        result.append(TermScore(term=term, score=round(value, 4)))
        if len(result) >= term_limit:
            break
    return score, result


def _chunk_matches(
    source_chunks: list[IndexedChunk],
    target_chunks: list[IndexedChunk],
    *,
    limit: int,
) -> list[ChunkMatch]:
    if not source_chunks or not target_chunks or limit <= 0:
        return []
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
    except ImportError as exc:
        raise MissingDependencyError("scikit-learn") from exc

    texts = [chunk.text for chunk in source_chunks] + [
        chunk.text for chunk in target_chunks
    ]
    vectorizer = TfidfVectorizer(
        stop_words="english",
        ngram_range=(1, 2),
        min_df=1,
        max_df=1.0,
        token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z0-9_-]{1,}\b",
    )
    matrix = vectorizer.fit_transform(texts)
    source_matrix = matrix[: len(source_chunks)]
    target_matrix = matrix[len(source_chunks) :]
    scores = cosine_similarity(source_matrix, target_matrix)
    candidates: list[tuple[float, int, int]] = []
    for source_index in range(scores.shape[0]):
        for target_index in range(scores.shape[1]):
            score = float(scores[source_index][target_index])
            if score > 0:
                candidates.append((score, source_index, target_index))
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    matches: list[ChunkMatch] = []
    for score, source_index, target_index in candidates[:limit]:
        source = source_chunks[source_index]
        target = target_chunks[target_index]
        shared = _shared_chunk_terms(source.text, target.text)
        matches.append(
            ChunkMatch(
                source_chunk_id=source.id,
                source_chunk_index=source.chunk_index,
                target_chunk_id=target.id,
                target_chunk_index=target.chunk_index,
                score=round(score, 4),
                shared_terms=shared[:6],
                source_excerpt=_excerpt(source.text, shared),
                target_excerpt=_excerpt(target.text, shared),
            )
        )
    return matches


def _shared_chunk_terms(source_text: str, target_text: str) -> list[str]:
    source_words = _informative_words(source_text)
    target_words = _informative_words(target_text)
    shared = source_words & target_words
    ordered = sorted(shared, key=lambda word: (-len(word), word))
    return ordered[:10]


def _informative_words(text: str) -> set[str]:
    return {
        word
        for word in _WORD_RE.findall(text.lower())
        if len(word) > 2 and word not in GENERIC_TERMS and not word.isdigit()
    }


def _excerpt(text: str, shared_terms: list[str], *, limit: int = 220) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    lowered = compact.lower()
    hit = 0
    for term in shared_terms:
        index = lowered.find(term.lower())
        if index >= 0:
            hit = index
            break
    start = max(0, hit - limit // 3)
    end = min(len(compact), start + limit)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(compact) else ""
    return prefix + compact[start:end].strip() + suffix


def _document_payload(document: DocumentSummary) -> dict[str, object]:
    return {
        "document_id": document.document_id,
        "title": document.title,
        "relative_path": document.relative_path,
        "score": document.score,
    }


def _is_informative_term(term: str) -> bool:
    tokens = _WORD_RE.findall(term.lower())
    if not tokens:
        return False
    if all(token in GENERIC_TERMS for token in tokens):
        return False
    return any(len(token) > 2 and token not in GENERIC_TERMS for token in tokens)
