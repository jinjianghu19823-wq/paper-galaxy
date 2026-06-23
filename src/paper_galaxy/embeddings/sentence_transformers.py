"""Strict, lazy Sentence Transformers loading for local embeddings."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from paper_galaxy.embeddings.codec import normalize_vector
from paper_galaxy.errors import MissingDependencyError

REMOTE_MODEL_DISABLED_MESSAGE = (
    "Remote/cached model names are disabled by default to avoid hidden downloads. "
    "Use a local model path or pass --allow-model-download."
)


class ModelDownloadDisabledError(RuntimeError):
    """Raised when a non-local model name would trigger hidden resolution."""


class EmbeddingEncoder(Protocol):
    """Small protocol shared by real and fake embedding encoders."""

    model_name: str
    dimension: int

    def encode(
        self,
        texts: list[str],
        *,
        batch_size: int = 32,
        normalize: bool = True,
    ) -> list[list[float]]:
        """Encode a batch of texts."""


@dataclass
class SentenceTransformerEncoder:
    """Adapter around a lazily imported Sentence Transformer model."""

    model_name: str
    dimension: int
    _model: Any

    def encode(
        self,
        texts: list[str],
        *,
        batch_size: int = 32,
        normalize: bool = True,
    ) -> list[list[float]]:
        """Encode texts and return JSON-serializable Python float lists."""

        encoded = self._model.encode(
            list(texts),
            batch_size=batch_size,
            normalize_embeddings=normalize,
            show_progress_bar=False,
        )
        raw_vectors = encoded.tolist() if hasattr(encoded, "tolist") else encoded
        vectors = [[float(value) for value in vector] for vector in raw_vectors]
        if normalize:
            return [normalize_vector(vector) for vector in vectors]
        return vectors


def load_sentence_transformer(
    model: str,
    *,
    allow_model_download: bool = False,
) -> SentenceTransformerEncoder:
    """Load a local Sentence Transformer model unless downloads are explicit."""

    model_path = Path(model).expanduser()
    if model_path.exists():
        model_identity = str(model_path.resolve())
        model_arg = model_identity
    else:
        if not allow_model_download:
            raise ModelDownloadDisabledError(REMOTE_MODEL_DISABLED_MESSAGE)
        model_identity = model
        model_arg = model

    sentence_transformer_class = _sentence_transformer_class()
    loaded_model = sentence_transformer_class(model_arg)
    dimension = _model_dimension(loaded_model)
    return SentenceTransformerEncoder(
        model_name=model_identity,
        dimension=dimension,
        _model=loaded_model,
    )


def _sentence_transformer_class() -> Any:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise MissingDependencyError("sentence-transformers") from exc
    return SentenceTransformer


def _model_dimension(model: Any) -> int:
    dimension = model.get_sentence_embedding_dimension()
    if dimension is None:
        encoded = model.encode(
            ["Paper Galaxy dimension probe"],
            batch_size=1,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        raw_vectors = encoded.tolist() if hasattr(encoded, "tolist") else encoded
        dimension = len(raw_vectors[0])
    return int(dimension)
