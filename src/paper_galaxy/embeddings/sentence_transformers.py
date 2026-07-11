"""Strict, lazy Sentence Transformers loading for local embeddings."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from paper_galaxy.embeddings.codec import normalize_vector
from paper_galaxy.errors import MissingDependencyError

REMOTE_MODEL_DISABLED_MESSAGE = (
    "Remote/cached model names are disabled by default to avoid hidden downloads. "
    "Use a local model path or pass --allow-model-download."
)
LOCAL_MODEL_FINGERPRINT_ALGORITHM = "sha256-model-files-v1"
LOADED_STATE_FINGERPRINT_ALGORITHM = "sha256-state-dict-v1"


class ModelDownloadDisabledError(RuntimeError):
    """Raised when a non-local model name would trigger hidden resolution."""


class ModelFingerprintError(RuntimeError):
    """Raised when the exact loaded model identity cannot be proven locally."""


class EmbeddingEncoder(Protocol):
    """Small protocol shared by real and fake embedding encoders."""

    model_name: str
    dimension: int
    model_fingerprint: str
    fingerprint_algorithm: str

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
    model_fingerprint: str
    fingerprint_algorithm: str
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
    local_model_path: Path | None = None
    if model_path.exists():
        local_model_path = model_path.resolve()
        model_identity = str(local_model_path)
        model_arg = model_identity
    else:
        if not allow_model_download:
            raise ModelDownloadDisabledError(REMOTE_MODEL_DISABLED_MESSAGE)
        model_identity = model
        model_arg = model

    fingerprint_before_load = (
        _local_model_fingerprint(local_model_path)
        if local_model_path is not None
        else None
    )
    sentence_transformer_class = _sentence_transformer_class()
    loaded_model = sentence_transformer_class(model_arg)
    dimension = _model_dimension(loaded_model)
    if local_model_path is not None:
        fingerprint = _local_model_fingerprint(local_model_path)
        if fingerprint != fingerprint_before_load:
            raise ModelFingerprintError(
                "Local embedding model files changed while the model was loading; "
                "retry only after the model directory is stable."
            )
        fingerprint_algorithm = LOCAL_MODEL_FINGERPRINT_ALGORITHM
    else:
        fingerprint = _loaded_state_fingerprint(loaded_model)
        fingerprint_algorithm = LOADED_STATE_FINGERPRINT_ALGORITHM
    return SentenceTransformerEncoder(
        model_name=model_identity,
        dimension=dimension,
        model_fingerprint=fingerprint,
        fingerprint_algorithm=fingerprint_algorithm,
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


def _local_model_fingerprint(model_path: Path) -> str:
    """Hash every model file by relative path and exact bytes."""

    digest = hashlib.sha256(b"paper-galaxy-local-model\0v1")
    if model_path.is_file():
        entries = [(".", model_path)]
    elif model_path.is_dir():
        entries = []
        for entry in sorted(model_path.rglob("*"), key=lambda path: path.as_posix()):
            if entry.is_symlink():
                raise ModelFingerprintError(
                    "Local embedding model contains a symbolic link; use a "
                    "fully materialized model directory so its weights can be "
                    "fingerprinted safely."
                )
            if entry.is_dir():
                continue
            if not entry.is_file():
                raise ModelFingerprintError(
                    "Local embedding model contains an unreadable or special entry; "
                    "use regular model files only."
                )
            entries.append((entry.relative_to(model_path).as_posix(), entry))
    else:
        raise ModelFingerprintError(
            "Local embedding model must be a regular file or directory."
        )

    try:
        for relative_path, entry in entries:
            _hash_field(digest, relative_path.encode("utf-8"))
            _hash_field(digest, str(entry.stat().st_size).encode("ascii"))
            with entry.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
    except OSError as exc:
        raise ModelFingerprintError(
            "Local embedding model changed or became unreadable while its content "
            "fingerprint was being computed."
        ) from exc
    return digest.hexdigest()


def _loaded_state_fingerprint(model: Any) -> str:
    """Hash a loaded remote model state or refuse unverifiable reuse."""

    state_function = getattr(model, "state_dict", None)
    if not callable(state_function):
        raise ModelFingerprintError(
            "The explicitly loaded embedding model exposes no reliable state_dict; "
            "Paper Galaxy refuses to persist vectors without a model fingerprint."
        )
    try:
        state = state_function()
    except Exception as exc:
        raise ModelFingerprintError(
            "The explicitly loaded embedding model state could not be read safely."
        ) from exc
    if not isinstance(state, Mapping) or not state:
        raise ModelFingerprintError(
            "The explicitly loaded embedding model returned no verifiable state; "
            "Paper Galaxy refuses to persist vectors without a model fingerprint."
        )

    digest = hashlib.sha256(b"paper-galaxy-loaded-model-state\0v1")
    for key in sorted(state, key=str):
        _hash_field(digest, str(key).encode("utf-8"))
        metadata, payload = _state_value_bytes(state[key])
        _hash_field(digest, metadata)
        _hash_field(digest, payload)
    return digest.hexdigest()


def _state_value_bytes(value: object) -> tuple[bytes, bytes]:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return b"bytes", bytes(value)

    try:
        detached = value.detach()  # type: ignore[attr-defined]
        on_cpu = detached.cpu()
        contiguous = on_cpu.contiguous()
        array = contiguous.numpy()
        dtype = str(array.dtype)
        shape = tuple(int(part) for part in array.shape)
        if getattr(array.dtype, "hasobject", False):
            raise TypeError("object arrays are not stable model state")
        payload = array.tobytes(order="C")
    except Exception as exc:
        raise ModelFingerprintError(
            "The explicitly loaded embedding model contains state that cannot be "
            "serialized deterministically for fingerprinting."
        ) from exc
    metadata = f"tensor\0{dtype}\0{shape!r}".encode()
    return metadata, payload


def _hash_field(digest: Any, value: bytes) -> None:
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)
