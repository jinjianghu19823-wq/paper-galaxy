from __future__ import annotations

import builtins
import math
import struct
from collections.abc import Sequence

import pytest

from paper_galaxy.embeddings.codec import (
    cosine_similarity,
    decode_vector,
    encode_vector,
)
from paper_galaxy.embeddings.ranking import (
    InvalidVectorCandidateError,
    ScoredVectorCandidate,
    VectorCandidate,
    blockwise_cosine_top_k,
    iter_cosine_scores,
)
from paper_galaxy.errors import MissingDependencyError


def test_blockwise_top_k_matches_naive_across_multiple_blocks() -> None:
    query = [0.7, -0.2, 0.4, 0.1]
    candidates = [
        _candidate(
            f"doc-{index:02d}",
            [
                float((index * 3) % 11 - 5),
                float((index * 5) % 13 - 6),
                float((index * 7) % 17 - 8),
                float(index % 4 - 2),
            ],
            relative_path=f"topic/{22 - index:02d}.md",
        )
        for index in range(23)
    ]

    actual = blockwise_cosine_top_k(
        query,
        candidates,
        limit=7,
        block_size=5,
    )
    expected = _naive_top_k(query, candidates, limit=7)

    assert [item.candidate.object_id for item in actual] == [
        item.candidate.object_id for item in expected
    ]
    assert [item.score for item in actual] == pytest.approx(
        [item.score for item in expected]
    )


def test_top_k_is_identical_when_candidate_insertion_order_is_reversed() -> None:
    query = [1.0, 1.0, 0.0]
    candidates = [
        _candidate(
            f"doc-{index}",
            [float(index + 1), float(7 - index), float(index % 2)],
            relative_path=f"collection/{index % 3}.md",
            chunk_index=index % 2,
        )
        for index in range(7)
    ]

    forward = blockwise_cosine_top_k(query, candidates, limit=5, block_size=2)
    reverse = blockwise_cosine_top_k(
        query,
        reversed(candidates),
        limit=5,
        block_size=3,
    )

    assert [item.candidate.object_id for item in forward] == [
        item.candidate.object_id for item in reverse
    ]
    assert [item.score for item in forward] == pytest.approx(
        [item.score for item in reverse]
    )


def test_equal_scores_use_path_chunk_and_object_id_as_stable_keys() -> None:
    query = [1.0, 0.0]
    candidates = [
        _candidate("z", [1.0, 0.0], relative_path="b.md", chunk_index=None),
        _candidate("c", [2.0, 0.0], relative_path="a.md", chunk_index=1),
        _candidate("b", [3.0, 0.0], relative_path="a.md", chunk_index=0),
        _candidate("a", [4.0, 0.0], relative_path="a.md", chunk_index=0),
        _candidate("n", [5.0, 0.0], relative_path="a.md", chunk_index=None),
    ]

    ranked = blockwise_cosine_top_k(query, candidates, limit=5, block_size=2)

    assert [item.candidate.object_id for item in ranked] == ["n", "a", "b", "c", "z"]
    assert all(item.score == pytest.approx(1.0) for item in ranked)


def test_zero_vectors_score_zero_and_scores_stream_in_input_order() -> None:
    candidates = [
        _candidate("zero", [0.0, 0.0]),
        _candidate("unit", [1.0, 0.0]),
    ]

    zero_query = list(iter_cosine_scores([0.0, 0.0], candidates, block_size=1))
    nonzero_query = list(iter_cosine_scores([1.0, 0.0], candidates, block_size=2))

    assert [item.candidate.object_id for item in zero_query] == ["zero", "unit"]
    assert [item.score for item in zero_query] == [0.0, 0.0]
    assert [item.candidate.object_id for item in nonzero_query] == ["zero", "unit"]
    assert [item.score for item in nonzero_query] == pytest.approx([0.0, 1.0])


def test_malformed_mismatched_and_nonfinite_candidates_raise_or_skip() -> None:
    valid = _candidate("valid", [1.0, 0.0])
    wrong_dimension = VectorCandidate(
        object_id="wrong-dimension",
        relative_path="wrong-dimension.md",
        chunk_index=None,
        dimension=3,
        blob=encode_vector([1.0, 0.0, 0.0], normalize=False),
    )
    malformed = VectorCandidate(
        object_id="malformed",
        relative_path="malformed.md",
        chunk_index=None,
        dimension=2,
        blob=b"bad",
    )
    nonfinite = VectorCandidate(
        object_id="nonfinite",
        relative_path="nonfinite.md",
        chunk_index=None,
        dimension=2,
        blob=struct.pack("<2f", math.nan, 0.0),
    )
    invalid_candidates = [wrong_dimension, malformed, nonfinite]

    for candidate in invalid_candidates:
        with pytest.raises(InvalidVectorCandidateError, match=candidate.object_id):
            list(iter_cosine_scores([1.0, 0.0], [candidate]))

    scores = list(
        iter_cosine_scores(
            [1.0, 0.0],
            [wrong_dimension, valid, malformed, nonfinite],
            block_size=2,
            invalid="skip",
        )
    )
    assert [item.candidate.object_id for item in scores] == ["valid"]


def test_nonfinite_query_and_invalid_options_are_rejected() -> None:
    candidate = _candidate("valid", [1.0, 0.0])

    with pytest.raises(ValueError, match="non-finite"):
        list(iter_cosine_scores([math.inf, 0.0], [candidate]))
    with pytest.raises(ValueError, match="block_size"):
        list(iter_cosine_scores([1.0, 0.0], [candidate], block_size=0))
    with pytest.raises(ValueError, match="invalid"):
        list(
            iter_cosine_scores(
                [1.0, 0.0],
                [candidate],
                invalid="ignore",  # type: ignore[arg-type]
            )
        )


def test_missing_numpy_raises_structured_dependency_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = builtins.__import__

    def without_numpy(
        name: str,
        globals: object = None,
        locals: object = None,
        fromlist: Sequence[str] = (),
        level: int = 0,
    ) -> object:
        if name == "numpy":
            raise ImportError("synthetic missing NumPy")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", without_numpy)

    with pytest.raises(MissingDependencyError) as caught:
        blockwise_cosine_top_k(
            [1.0, 0.0],
            [_candidate("doc", [1.0, 0.0])],
            limit=1,
        )
    assert caught.value.dependency == "numpy"


def _candidate(
    object_id: str,
    values: list[float],
    *,
    relative_path: str | None = None,
    chunk_index: int | None = None,
) -> VectorCandidate:
    return VectorCandidate(
        object_id=object_id,
        relative_path=relative_path or f"{object_id}.md",
        chunk_index=chunk_index,
        dimension=len(values),
        blob=encode_vector(values, normalize=False),
    )


def _naive_top_k(
    query: list[float], candidates: list[VectorCandidate], *, limit: int
) -> list[ScoredVectorCandidate]:
    scores = [
        ScoredVectorCandidate(
            candidate=candidate,
            score=cosine_similarity(
                query,
                decode_vector(candidate.blob, dimension=candidate.dimension),
            ),
        )
        for candidate in candidates
    ]
    scores.sort(
        key=lambda item: (
            -item.score,
            item.candidate.relative_path,
            -1 if item.candidate.chunk_index is None else item.candidate.chunk_index,
            item.candidate.object_id,
        )
    )
    return scores[:limit]
