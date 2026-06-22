"""Nearest neighbors from high-dimensional TF-IDF similarity."""

from __future__ import annotations

from typing import Any

from paper_galaxy.errors import MissingDependencyError
from paper_galaxy.models import Document, Neighbor


def compute_neighbors(
    matrix: Any,
    documents: list[Document],
    *,
    neighbor_count: int,
) -> dict[str, list[Neighbor]]:
    """Compute nearest neighbors from cosine similarity in TF-IDF space."""

    try:
        from sklearn.metrics.pairwise import cosine_similarity
    except ImportError as exc:
        raise MissingDependencyError("scikit-learn") from exc

    similarity = cosine_similarity(matrix)
    neighbors: dict[str, list[Neighbor]] = {}
    for row_index, document in enumerate(documents):
        candidates = [
            (other_index, float(score))
            for other_index, score in enumerate(similarity[row_index])
            if other_index != row_index
        ]
        candidates.sort(key=lambda item: (-item[1], documents[item[0]].relative_path))
        neighbors[document.id] = [
            Neighbor(
                document_id=documents[other_index].id,
                title=documents[other_index].title,
                relative_path=documents[other_index].relative_path,
                score=round(score, 4),
            )
            for other_index, score in candidates[: max(0, neighbor_count)]
        ]
    return neighbors
