from __future__ import annotations

from pathlib import Path

import pytest

from paper_galaxy.embeddings.sentence_transformers import (
    ModelDownloadDisabledError,
    load_sentence_transformer,
)
from paper_galaxy.errors import MissingDependencyError


class FakeSentenceTransformer:
    loaded_with: str | None = None

    def __init__(self, model: str) -> None:
        self.loaded_with = model
        FakeSentenceTransformer.loaded_with = model

    def get_sentence_embedding_dimension(self) -> int:
        return 2

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
    monkeypatch.setattr(
        "paper_galaxy.embeddings.sentence_transformers._sentence_transformer_class",
        lambda: FakeSentenceTransformer,
    )

    encoder = load_sentence_transformer(str(model_dir))

    assert encoder.model_name == str(model_dir.resolve())
    assert encoder.dimension == 2
    assert FakeSentenceTransformer.loaded_with == str(model_dir.resolve())


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
    assert (
        FakeSentenceTransformer.loaded_with == "sentence-transformers/all-MiniLM-L6-v2"
    )


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
