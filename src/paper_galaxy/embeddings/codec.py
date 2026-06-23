"""SQLite BLOB encoding for local float32 vectors."""

from __future__ import annotations

import math
import struct
from collections.abc import Sequence

FLOAT32_DTYPE = "float32"
FLOAT32_BYTES = 4


def normalize_vector(values: Sequence[float]) -> list[float]:
    """Return an L2-normalized copy of a vector."""

    vector = [float(value) for value in values]
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return vector
    return [value / norm for value in vector]


def encode_vector(
    values: Sequence[float],
    *,
    expected_dimension: int | None = None,
    normalize: bool = True,
) -> bytes:
    """Encode a vector as little-endian float32 bytes."""

    vector = (
        normalize_vector(values) if normalize else [float(value) for value in values]
    )
    if expected_dimension is not None and len(vector) != expected_dimension:
        raise ValueError(
            "Vector dimension mismatch: "
            f"expected {expected_dimension}, got {len(vector)}."
        )
    if not vector:
        raise ValueError("Vector must contain at least one value.")
    if any(not math.isfinite(value) for value in vector):
        raise ValueError("Vector contains a non-finite value.")
    return struct.pack(f"<{len(vector)}f", *vector)


def decode_vector(blob: bytes, *, dimension: int) -> list[float]:
    """Decode little-endian float32 bytes from SQLite."""

    if dimension <= 0:
        raise ValueError("Vector dimension must be positive.")
    expected_size = dimension * FLOAT32_BYTES
    if len(blob) != expected_size:
        raise ValueError(
            f"Vector BLOB has {len(blob)} bytes, expected {expected_size} bytes "
            f"for dimension {dimension}."
        )
    return [float(value) for value in struct.unpack(f"<{dimension}f", blob)]


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Compute cosine similarity for two vectors."""

    if len(left) != len(right):
        raise ValueError(f"Vector dimension mismatch: {len(left)} != {len(right)}.")
    left_norm = math.sqrt(sum(float(value) * float(value) for value in left))
    right_norm = math.sqrt(sum(float(value) * float(value) for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    dot = sum(
        float(l_value) * float(r_value)
        for l_value, r_value in zip(left, right, strict=True)
    )
    return dot / (left_norm * right_norm)
