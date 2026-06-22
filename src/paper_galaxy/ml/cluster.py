"""K-means clustering helpers."""

from __future__ import annotations

import math
import os
import warnings
from typing import Any

from paper_galaxy.errors import MissingDependencyError


def choose_cluster_count(document_count: int, requested: int | None) -> int:
    """Choose and clamp a conservative cluster count."""

    if document_count <= 1:
        return max(document_count, 0)
    if requested is not None:
        return max(1, min(requested, document_count))
    if document_count < 3:
        return 1
    return min(8, max(2, round(math.sqrt(document_count))), document_count)


def compute_clusters(matrix: Any, *, requested: int | None, seed: int) -> list[int]:
    """Cluster documents with k-means when there are enough documents."""

    document_count = int(matrix.shape[0])
    cluster_count = choose_cluster_count(document_count, requested)
    if cluster_count <= 1:
        return [0 for _ in range(document_count)]

    try:
        from sklearn.cluster import KMeans
    except ImportError as exc:
        raise MissingDependencyError("scikit-learn") from exc

    os.environ.setdefault("LOKY_MAX_CPU_COUNT", "1")
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Could not find the number of physical cores.*",
            category=UserWarning,
        )
        labels = KMeans(
            n_clusters=cluster_count,
            random_state=seed,
            n_init="auto",
        ).fit_predict(matrix)
    return [int(label) for label in labels]
