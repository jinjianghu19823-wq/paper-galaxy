"""2D layout helpers."""

from __future__ import annotations

import math
from typing import Any

from paper_galaxy.errors import MissingDependencyError


def compute_layout(matrix: Any, *, seed: int) -> list[tuple[float, float]]:
    """Compute deterministic 2D coordinates, falling back for tiny corpora."""

    document_count = int(matrix.shape[0])
    feature_count = int(matrix.shape[1])
    if document_count <= 2 or feature_count < 2:
        return fallback_layout(document_count)

    try:
        from sklearn.decomposition import TruncatedSVD
    except ImportError as exc:
        raise MissingDependencyError("scikit-learn") from exc

    try:
        coords = TruncatedSVD(n_components=2, random_state=seed).fit_transform(matrix)
    except ValueError:
        return fallback_layout(document_count)

    points = [(float(row[0]), float(row[1])) for row in coords]
    if not all(math.isfinite(x) and math.isfinite(y) for x, y in points):
        return fallback_layout(document_count)
    return _normalize(points)


def fallback_layout(document_count: int) -> list[tuple[float, float]]:
    """Place documents on a deterministic circle or center point."""

    if document_count <= 0:
        return []
    if document_count == 1:
        return [(0.0, 0.0)]
    points: list[tuple[float, float]] = []
    for index in range(document_count):
        angle = 2.0 * math.pi * index / document_count
        points.append((math.cos(angle), math.sin(angle)))
    return points


def _normalize(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max(max_x - min_x, 1e-12)
    span_y = max(max_y - min_y, 1e-12)
    return [
        (((x - min_x) / span_x) * 2.0 - 1.0, ((y - min_y) / span_y) * 2.0 - 1.0)
        for x, y in points
    ]
