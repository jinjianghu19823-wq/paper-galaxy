from __future__ import annotations

from pathlib import Path

import pytest

from paper_galaxy.embeddings.models import stable_embedding_model_id
from paper_galaxy.embeddings.sentence_transformers import (
    LOADED_STATE_FINGERPRINT_ALGORITHM,
    LOCAL_MODEL_FINGERPRINT_ALGORITHM,
    ModelDownloadDisabledError,
    ModelFingerprintError,
    load_sentence_transformer,
)
from paper_galaxy.errors import MissingDependencyError


class FakeSentenceTransformer:
    loaded_with: str | None = None
    state_payload = b"synthetic-state-v1"

    def __init__(self, model: str) -> None:
        self.loaded_with = model
        FakeSentenceTransformer.loaded_with = model

    def get_sentence_embedding_dimension(self) -> int:
        return 2

    def state_dict(self) -> dict[str, bytes]:
        return {"encoder.weight": self.state_payload}

    def encode(
        self,
        texts: list[str],
        *,
        batch_size: int,
        normalize_embeddings: bool,
        show_progress_bar: bool,
    ) -> list[list[float]]:
        del batch_size, normalize_embeddings, show_progress_bar
        return [[1.0, 0.0] for _ in texts]


def test_local_sentence_transformer_path_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model_dir = tmp_path / "local-model"
    model_dir.mkdir()
    (model_dir / "weights.bin").write_bytes(b"local-weights-v1")
    monkeypatch.setattr(
        "paper_galaxy.embeddings.sentence_transformers._sentence_transformer_class",
        lambda: FakeSentenceTransformer,
    )

    encoder = load_sentence_transformer(str(model_dir))

    assert encoder.model_name == str(model_dir.resolve())
    assert encoder.dimension == 2
    assert len(encoder.model_fingerprint) == 64
    assert encoder.fingerprint_algorithm == LOCAL_MODEL_FINGERPRINT_ALGORITHM
    assert FakeSentenceTransformer.loaded_with == str(model_dir.resolve())


def test_local_model_content_change_changes_fingerprint_at_same_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model_dir = tmp_path / "local-model"
    model_dir.mkdir()
    weights = model_dir / "weights.bin"
    weights.write_bytes(b"weights-one")
    monkeypatch.setattr(
        "paper_galaxy.embeddings.sentence_transformers._sentence_transformer_class",
        lambda: FakeSentenceTransformer,
    )

    first = load_sentence_transformer(str(model_dir))
    weights.write_bytes(b"weights-two")
    second = load_sentence_transformer(str(model_dir))

    assert first.model_name == second.model_name
    assert first.dimension == second.dimension
    assert first.model_fingerprint != second.model_fingerprint


def test_local_model_change_during_load_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model_dir = tmp_path / "local-model"
    model_dir.mkdir()
    weights = model_dir / "weights.bin"
    weights.write_bytes(b"weights-before-load")

    class MutatingSentenceTransformer(FakeSentenceTransformer):
        def __init__(self, model: str) -> None:
            super().__init__(model)
            weights.write_bytes(b"weights-after-load")

    monkeypatch.setattr(
        "paper_galaxy.embeddings.sentence_transformers._sentence_transformer_class",
        lambda: MutatingSentenceTransformer,
    )

    with pytest.raises(
        ModelFingerprintError, match="changed while the model was loading"
    ):
        load_sentence_transformer(str(model_dir))


def test_local_model_directory_symlink_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model_dir = tmp_path / "local-model"
    model_dir.mkdir()
    external_weights = tmp_path / "outside.bin"
    external_weights.write_bytes(b"external-weights")
    (model_dir / "weights.bin").symlink_to(external_weights)
    monkeypatch.setattr(
        "paper_galaxy.embeddings.sentence_transformers._sentence_transformer_class",
        lambda: FakeSentenceTransformer,
    )

    with pytest.raises(ModelFingerprintError, match="symbolic link"):
        load_sentence_transformer(str(model_dir))


def test_remote_model_name_is_rejected_by_default() -> None:
    with pytest.raises(ModelDownloadDisabledError, match="hidden downloads"):
        load_sentence_transformer("sentence-transformers/all-MiniLM-L6-v2")


def test_remote_model_name_is_allowed_only_when_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "paper_galaxy.embeddings.sentence_transformers._sentence_transformer_class",
        lambda: FakeSentenceTransformer,
    )

    encoder = load_sentence_transformer(
        "sentence-transformers/all-MiniLM-L6-v2",
        allow_model_download=True,
    )

    assert encoder.model_name == "sentence-transformers/all-MiniLM-L6-v2"
    assert len(encoder.model_fingerprint) == 64
    assert encoder.fingerprint_algorithm == LOADED_STATE_FINGERPRINT_ALGORITHM
    assert (
        FakeSentenceTransformer.loaded_with == "sentence-transformers/all-MiniLM-L6-v2"
    )


def test_remote_model_fingerprint_comes_from_loaded_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "paper_galaxy.embeddings.sentence_transformers._sentence_transformer_class",
        lambda: FakeSentenceTransformer,
    )
    monkeypatch.setattr(FakeSentenceTransformer, "state_payload", b"state-one")
    first = load_sentence_transformer("synthetic/remote", allow_model_download=True)
    monkeypatch.setattr(FakeSentenceTransformer, "state_payload", b"state-two")
    second = load_sentence_transformer("synthetic/remote", allow_model_download=True)

    assert first.model_name == second.model_name
    assert first.model_fingerprint != second.model_fingerprint


def test_remote_model_without_reliable_state_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StatelessSentenceTransformer:
        def __init__(self, model: str) -> None:
            del model

        def get_sentence_embedding_dimension(self) -> int:
            return 2

    monkeypatch.setattr(
        "paper_galaxy.embeddings.sentence_transformers._sentence_transformer_class",
        lambda: StatelessSentenceTransformer,
    )

    with pytest.raises(ModelFingerprintError, match="no reliable state_dict"):
        load_sentence_transformer("synthetic/stateless", allow_model_download=True)


def test_model_fingerprint_is_part_of_stable_model_identity() -> None:
    shared = {
        "provider": "sentence-transformers",
        "name": "/models/same-path",
        "dimension": 2,
        "distance": "cosine",
        "config": {"normalize": True},
        "fingerprint_algorithm": LOCAL_MODEL_FINGERPRINT_ALGORITHM,
    }

    first = stable_embedding_model_id(
        **shared,
        model_fingerprint="a" * 64,
    )
    second = stable_embedding_model_id(
        **shared,
        model_fingerprint="b" * 64,
    )

    assert first != second


def test_missing_sentence_transformers_dependency_is_helpful(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model_dir = tmp_path / "local-model"
    model_dir.mkdir()

    def raise_missing_dependency() -> object:
        raise MissingDependencyError("sentence-transformers")

    monkeypatch.setattr(
        "paper_galaxy.embeddings.sentence_transformers._sentence_transformer_class",
        raise_missing_dependency,
    )

    with pytest.raises(MissingDependencyError) as exc_info:
        load_sentence_transformer(str(model_dir))

    assert exc_info.value.dependency == "sentence-transformers"
