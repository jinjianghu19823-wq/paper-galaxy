"""Transparent c-TF-IDF-style cluster labeling."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from paper_galaxy.errors import MissingDependencyError
from paper_galaxy.explain.models import ClusterLabel, DocumentSummary, TermScore
from paper_galaxy.models import Document

GENERIC_TERMS = {
    "abstract",
    "analysis",
    "and",
    "approach",
    "are",
    "based",
    "between",
    "chapter",
    "conclusion",
    "data",
    "document",
    "documents",
    "example",
    "examples",
    "experiment",
    "experiments",
    "figure",
    "file",
    "introduction",
    "method",
    "methods",
    "model",
    "models",
    "note",
    "notes",
    "paper",
    "papers",
    "present",
    "problem",
    "results",
    "section",
    "show",
    "shown",
    "study",
    "table",
    "text",
    "that",
    "the",
    "their",
    "this",
    "through",
    "using",
    "when",
    "where",
    "which",
    "with",
    "work",
}

_WORD_RE = re.compile(r"[a-z0-9]+")


def cluster_signature(document_ids: list[str] | tuple[str, ...]) -> str:
    """Return a stable signature from sorted active document ids."""

    payload = json.dumps(sorted(document_ids), separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"cluster_{digest[:16]}"


def validate_manual_label(label: str) -> str:
    """Validate and normalize a local manual cluster label."""

    cleaned = " ".join(label.split())
    if not cleaned:
        raise ValueError("Cluster label cannot be empty.")
    if len(cleaned) > 120:
        raise ValueError("Cluster label must be 120 characters or fewer.")
    return cleaned


def label_clusters_ctfidf(
    documents: list[Document],
    cluster_ids: list[int],
    *,
    overrides: Mapping[str, str] | None = None,
    term_limit: int = 6,
    representative_limit: int = 3,
) -> list[ClusterLabel]:
    """Create inspectable labels from real cluster terms and local overrides."""

    if len(documents) != len(cluster_ids):
        raise ValueError("Document and cluster id counts must match.")
    if not documents:
        return []
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
    except ImportError as exc:
        raise MissingDependencyError("scikit-learn") from exc

    cluster_documents: dict[int, list[Document]] = {}
    for document, cluster_id in zip(documents, cluster_ids, strict=True):
        cluster_documents.setdefault(cluster_id, []).append(document)

    ordered_cluster_ids = sorted(cluster_documents)
    cluster_texts = [
        "\n".join(
            f"{document.title}\n{document.relative_path}\n{document.text}"
            for document in cluster_documents[cluster_id]
        )
        for cluster_id in ordered_cluster_ids
    ]
    vectorizer = TfidfVectorizer(
        stop_words="english",
        ngram_range=(1, 2),
        min_df=1,
        max_df=1.0,
        token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z0-9_-]{1,}\b",
    )
    matrix = vectorizer.fit_transform(cluster_texts)
    terms = [str(term) for term in vectorizer.get_feature_names_out()]
    labels: list[ClusterLabel] = []
    for row_index, cluster_id in enumerate(ordered_cluster_ids):
        row = matrix.getrow(row_index)
        top_terms = _top_terms(row, terms, limit=term_limit)
        signature = cluster_signature(
            [document.id for document in cluster_documents[cluster_id]]
        )
        generated_label = _generated_label(cluster_id, top_terms)
        manual_label = (overrides or {}).get(signature)
        labels.append(
            ClusterLabel(
                cluster_id=cluster_id,
                cluster_signature=signature,
                generated_label=generated_label,
                display_label=manual_label or generated_label,
                source="manual" if manual_label else "generated",
                document_ids=sorted(
                    document.id for document in cluster_documents[cluster_id]
                ),
                top_terms=top_terms,
                representatives=_representatives(
                    cluster_documents[cluster_id],
                    top_terms,
                    limit=representative_limit,
                ),
            )
        )
    return labels


def apply_label_overrides(
    labels: list[ClusterLabel], overrides: Mapping[str, str]
) -> list[ClusterLabel]:
    """Apply manual display labels while preserving generated evidence."""

    patched: list[ClusterLabel] = []
    for label in labels:
        manual = overrides.get(label.cluster_signature)
        patched.append(
            replace(
                label,
                display_label=manual or label.generated_label,
                source="manual" if manual else "generated",
            )
        )
    return patched


def fallback_cluster_labels(
    documents: list[Document],
    cluster_ids: list[int],
    generated_labels: Mapping[int, str],
) -> list[ClusterLabel]:
    """Build minimal labels if richer labeling fails."""

    labels: list[ClusterLabel] = []
    for cluster_id in sorted(set(cluster_ids)):
        cluster_docs = [
            document
            for document, assigned in zip(documents, cluster_ids, strict=True)
            if assigned == cluster_id
        ]
        signature = cluster_signature([document.id for document in cluster_docs])
        generated = generated_labels.get(cluster_id) or f"Cluster {cluster_id}"
        labels.append(
            ClusterLabel(
                cluster_id=cluster_id,
                cluster_signature=signature,
                generated_label=generated,
                display_label=generated,
                source="generated",
                document_ids=sorted(document.id for document in cluster_docs),
                representatives=[
                    DocumentSummary(
                        document_id=document.id,
                        title=document.title,
                        relative_path=document.relative_path,
                    )
                    for document in cluster_docs[:3]
                ],
                warnings=["Fell back to simple TF-IDF cluster labels."],
            )
        )
    return labels


def _top_terms(row: Any, terms: list[str], *, limit: int) -> list[TermScore]:
    scores = row.toarray()[0]
    ordered = scores.argsort()[::-1]
    result: list[TermScore] = []
    seen: set[str] = set()
    for index in ordered:
        score = float(scores[int(index)])
        if score <= 0:
            break
        term = terms[int(index)]
        if not _is_informative_term(term):
            continue
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(TermScore(term=term, score=round(score, 4)))
        if len(result) >= limit:
            break
    return result


def _generated_label(cluster_id: int, terms: list[TermScore]) -> str:
    selected = [term.term for term in terms[:3]]
    return " / ".join(selected) if selected else f"Cluster {cluster_id}"


def _representatives(
    documents: list[Document],
    terms: list[TermScore],
    *,
    limit: int,
) -> list[DocumentSummary]:
    term_values = [term.term.lower() for term in terms]
    scored: list[DocumentSummary] = []
    for document in documents:
        searchable = (
            f"{document.title} {document.relative_path} {document.text}".lower()
        )
        score = sum(1.0 for term in term_values if term in searchable)
        score += min(document.char_count / 10000, 0.5)
        scored.append(
            DocumentSummary(
                document_id=document.id,
                title=document.title,
                relative_path=document.relative_path,
                score=round(score, 4),
            )
        )
    return sorted(scored, key=lambda item: (-item.score, item.relative_path))[
        : max(0, limit)
    ]


def _is_informative_term(term: str) -> bool:
    lowered = term.lower().strip()
    if len(lowered) < 3 or lowered in GENERIC_TERMS:
        return False
    tokens = _WORD_RE.findall(lowered)
    if not tokens or all(token.isdigit() for token in tokens):
        return False
    if all(token in GENERIC_TERMS for token in tokens):
        return False
    if len(tokens) == 1:
        return tokens[0] not in GENERIC_TERMS and len(tokens[0]) > 2
    return any(token not in GENERIC_TERMS and len(token) > 2 for token in tokens)
