from __future__ import annotations

import math

import pytest

from paper_galaxy.embeddings.codec import (
    FLOAT32_BYTES,
    cosine_similarity,
    decode_vector,
    encode_vector,
    normalize_vector,
)


def test_vector_codec_roundtrips_float32_blob() -> None:
    blob = encode_vector([1.0, 2.0, 3.0], normalize=False)

    decoded = decode_vector(blob, dimension=3)

    assert len(blob) == 3 * FLOAT32_BYTES
    assert decoded == pytest.approx([1.0, 2.0, 3.0])


def test_vector_codec_normalizes_for_cosine() -> None:
    normalized = normalize_vector([3.0, 4.0])
    blob = encode_vector([3.0, 4.0], normalize=True)

    assert normalized == pytest.approx([0.6, 0.8])
    assert decode_vector(blob, dimension=2) == pytest.approx([0.6, 0.8])
    assert math.isclose(cosine_similarity(normalized, [0.6, 0.8]), 1.0)


def test_vector_codec_rejects_dimension_mismatch() -> None:
    blob = encode_vector([1.0, 2.0], normalize=False)

    with pytest.raises(ValueError, match="expected 3"):
        encode_vector([1.0, 2.0], expected_dimension=3)
    with pytest.raises(ValueError, match="expected 12 bytes"):
        decode_vector(blob, dimension=3)
