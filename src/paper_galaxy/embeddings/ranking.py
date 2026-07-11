"""Bounded-memory cosine ranking for locally stored float32 vectors."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from paper_galaxy.embeddings.codec import FLOAT32_BYTES
from paper_galaxy.errors import MissingDependencyError

InvalidCandidatePolicy = Literal["raise", "skip"]


@dataclass(frozen=True)
class VectorCandidate:
    """Metadata and one little-endian float32 vector BLOB to rank."""

    object_id: str
    relative_path: str
    chunk_index: int | None
    dimension: int
    blob: bytes
    source_content_sha256: str = ""
    vector_id: str = ""
    text_sha256: str = ""
    updated_at: str = ""
    model_fingerprint: str = ""
    algorithm_version: str = ""
    dtype: str = ""
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ScoredVectorCandidate:
    """One candidate paired with its exact, unrounded cosine score."""

    candidate: VectorCandidate
    score: float


class InvalidVectorCandidateError(ValueError):
    """Raised when a stored candidate cannot be ranked safely."""


def iter_cosine_scores(
    query_vector: Sequence[float],
    candidates: Iterable[VectorCandidate],
    *,
    block_size: int = 1024,
    invalid: InvalidCandidatePolicy = "raise",
) -> Iterator[ScoredVectorCandidate]:
    """Yield cosine scores in input order using bounded NumPy blocks.

    Candidate BLOBs must contain exactly ``dimension`` little-endian float32
    values, and that dimension must match the finite query vector. Zero query
    or candidate vectors receive a score of ``0.0``. Malformed, mismatched, or
    non-finite candidates raise :class:`InvalidVectorCandidateError` by default;
    callers performing best-effort maintenance searches may choose
    ``invalid="skip"`` instead.
    """

    numpy = _load_numpy()
    _validate_options(block_size=block_size, invalid=invalid)
    query = numpy.asarray(tuple(float(value) for value in query_vector), dtype="<f8")
    if query.ndim != 1 or query.size == 0:
        raise ValueError("Query vector must contain at least one value.")
    if not bool(numpy.isfinite(query).all()):
        raise ValueError("Query vector contains a non-finite value.")
    query_norm_squared = float(
        numpy.einsum("i,i->", query, query, dtype="<f8", optimize=True)
    )
    query_norm = query_norm_squared**0.5
    dimension = int(query.size)

    for candidate_block in _blocks(candidates, block_size):
        valid_candidates: list[VectorCandidate] = []
        matrix = numpy.empty((len(candidate_block), dimension), dtype="<f4")
        for candidate in candidate_block:
            try:
                values = _decode_candidate(
                    numpy,
                    candidate,
                    expected_dimension=dimension,
                )
            except InvalidVectorCandidateError:
                if invalid == "skip":
                    continue
                raise
            matrix[len(valid_candidates)] = values
            valid_candidates.append(candidate)

        if not valid_candidates:
            continue
        vectors = matrix[: len(valid_candidates)]
        dots = numpy.einsum("ij,j->i", vectors, query, dtype="<f8", optimize=True)
        squared_norms = numpy.einsum(
            "ij,ij->i", vectors, vectors, dtype="<f8", optimize=True
        )
        denominators = numpy.sqrt(squared_norms) * query_norm
        scores = numpy.divide(
            dots,
            denominators,
            out=numpy.zeros(len(valid_candidates), dtype="<f8"),
            where=denominators > 0.0,
        )
        if not bool(numpy.isfinite(scores).all()):
            raise InvalidVectorCandidateError(
                "Cosine calculation produced a non-finite score."
            )
        for candidate, score in zip(valid_candidates, scores, strict=True):
            yield ScoredVectorCandidate(candidate=candidate, score=float(score))


def blockwise_cosine_top_k(
    query_vector: Sequence[float],
    candidates: Iterable[VectorCandidate],
    *,
    limit: int,
    block_size: int = 1024,
    invalid: InvalidCandidatePolicy = "raise",
) -> list[ScoredVectorCandidate]:
    """Return deterministic cosine top-k with bounded ``O((block+k)*D)`` memory."""

    if limit <= 0:
        return []
    _validate_options(block_size=block_size, invalid=invalid)
    best: list[ScoredVectorCandidate] = []
    pending: list[ScoredVectorCandidate] = []
    for scored in iter_cosine_scores(
        query_vector,
        candidates,
        block_size=block_size,
        invalid=invalid,
    ):
        pending.append(scored)
        if len(pending) == block_size:
            best = _merge_best(best, pending, limit=limit)
            pending = []
    if pending:
        best = _merge_best(best, pending, limit=limit)
    return best


def _decode_candidate(
    numpy: Any,
    candidate: VectorCandidate,
    *,
    expected_dimension: int,
) -> Any:
    if candidate.dimension != expected_dimension:
        raise InvalidVectorCandidateError(
            f"Vector {candidate.object_id!r} has dimension {candidate.dimension}; "
            f"expected {expected_dimension}."
        )
    if candidate.dimension <= 0:
        raise InvalidVectorCandidateError(
            f"Vector {candidate.object_id!r} must have a positive dimension."
        )
    expected_size = candidate.dimension * FLOAT32_BYTES
    if len(candidate.blob) != expected_size:
        raise InvalidVectorCandidateError(
            f"Vector {candidate.object_id!r} has {len(candidate.blob)} bytes; "
            f"expected {expected_size}."
        )
    values = numpy.frombuffer(candidate.blob, dtype="<f4")
    if not bool(numpy.isfinite(values).all()):
        raise InvalidVectorCandidateError(
            f"Vector {candidate.object_id!r} contains a non-finite value."
        )
    return values


def _merge_best(
    current: list[ScoredVectorCandidate],
    pending: list[ScoredVectorCandidate],
    *,
    limit: int,
) -> list[ScoredVectorCandidate]:
    combined = [*current, *pending]
    combined.sort(key=_rank_key)
    return combined[:limit]


def _rank_key(item: ScoredVectorCandidate) -> tuple[float, str, int, str]:
    candidate = item.candidate
    chunk_index = -1 if candidate.chunk_index is None else candidate.chunk_index
    return (-item.score, candidate.relative_path, chunk_index, candidate.object_id)


def _blocks(
    candidates: Iterable[VectorCandidate], block_size: int
) -> Iterator[list[VectorCandidate]]:
    block: list[VectorCandidate] = []
    for candidate in candidates:
        block.append(candidate)
        if len(block) == block_size:
            yield block
            block = []
    if block:
        yield block


def _validate_options(*, block_size: int, invalid: InvalidCandidatePolicy) -> None:
    if block_size <= 0:
        raise ValueError("block_size must be positive.")
    if invalid not in {"raise", "skip"}:
        raise ValueError("invalid must be 'raise' or 'skip'.")


def _load_numpy() -> Any:
    try:
        import numpy
    except ImportError as exc:
        raise MissingDependencyError("numpy") from exc
    return numpy
