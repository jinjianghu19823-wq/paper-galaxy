from __future__ import annotations

import copy
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.build_demo_site as demo_builder

REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_FLOAT_DIGITS = 8


def test_demo_payload_public_floats_are_finite_and_normalized() -> None:
    payload = demo_builder.build_demo_payload(
        corpus_dir=REPO_ROOT / "examples" / "tiny_corpus"
    )
    floats = list(_float_values(payload))

    assert floats
    for path, value in floats:
        assert math.isfinite(value), path
        assert value == round(value, PUBLIC_FLOAT_DIGITS), path
        if value == 0.0:
            assert math.copysign(1.0, value) == 1.0, path


def test_public_float_normalizer_uses_eight_digits_and_positive_zero() -> None:
    normalizer = getattr(demo_builder, "_normalize_public_float", None)

    assert callable(normalizer)
    assert normalizer(0.1234567894) == 0.12345679
    normalized_zero = normalizer(-0.000000001)
    assert normalized_zero == 0.0
    assert math.copysign(1.0, normalized_zero) == 1.0


def test_public_float_normalizer_rejects_nonfinite_values() -> None:
    normalizer = getattr(demo_builder, "_normalize_public_float", None)

    assert callable(normalizer)
    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            normalizer(value)


def test_demo_payload_rejects_nested_nonfinite_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_index_corpus(*args: object, **kwargs: object) -> None:
        del args, kwargs

    def fake_build_map_payload(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        return _minimal_raw_payload(score=float("nan"))

    monkeypatch.setattr(demo_builder, "index_corpus", fake_index_corpus)
    monkeypatch.setattr(demo_builder, "build_map_payload", fake_build_map_payload)

    with pytest.raises(ValueError):
        demo_builder.build_demo_payload(
            corpus_dir=REPO_ROOT / "examples" / "tiny_corpus"
        )


def test_canonicalization_absorbs_cluster_permutation_and_axis_sign_flip() -> None:
    baseline = demo_builder.build_demo_payload(
        corpus_dir=REPO_ROOT / "examples" / "tiny_corpus"
    )
    permuted = copy.deepcopy(baseline)
    cluster_ids = sorted(int(cluster["cluster_id"]) for cluster in permuted["clusters"])
    remapped = dict(zip(cluster_ids, reversed(cluster_ids), strict=True))
    for cluster in permuted["clusters"]:
        cluster["cluster_id"] = remapped[int(cluster["cluster_id"])]
    for point in permuted["points"]:
        point["cluster_id"] = remapped[int(point["cluster_id"])]
        point["x"] = -float(point["x"])
        point["y"] = -float(point["y"])
    permuted["cluster_labels"] = {
        str(remapped[int(cluster_id)]): label
        for cluster_id, label in permuted["cluster_labels"].items()
    }

    canonical_baseline = demo_builder._canonicalize_demo_payload(
        baseline,
        explanations=baseline["explanations"],
    )
    canonical_permuted = demo_builder._canonicalize_demo_payload(
        permuted,
        explanations=permuted["explanations"],
    )

    assert demo_builder.serialize_demo_payload(
        canonical_baseline
    ) == demo_builder.serialize_demo_payload(canonical_permuted)


def test_actual_demo_json_is_identical_across_processes_and_corpus_paths(
    tmp_path: Path,
) -> None:
    first_corpus = tmp_path / "first-absolute-location" / "tiny_corpus"
    second_corpus = tmp_path / "second" / "different-location" / "tiny_corpus"
    shutil.copytree(REPO_ROOT / "examples" / "tiny_corpus", first_corpus)
    shutil.copytree(REPO_ROOT / "examples" / "tiny_corpus", second_corpus)

    first = _build_demo_in_subprocess(
        corpus=first_corpus,
        output=tmp_path / "first-site-dist",
        hash_seed="101",
    )
    second = _build_demo_in_subprocess(
        corpus=second_corpus,
        output=tmp_path / "second-site-dist",
        hash_seed="909",
    )

    assert first == second
    assert str(first_corpus).encode() not in first
    assert str(second_corpus).encode() not in first
    json.loads(first)


def _float_values(value: object, path: str = "$") -> list[tuple[str, float]]:
    if isinstance(value, float):
        return [(path, value)]
    if isinstance(value, dict):
        values: list[tuple[str, float]] = []
        for key, nested in value.items():
            values.extend(_float_values(nested, f"{path}.{key}"))
        return values
    if isinstance(value, list):
        values = []
        for index, nested in enumerate(value):
            values.extend(_float_values(nested, f"{path}[{index}]"))
        return values
    return []


def _minimal_raw_payload(*, score: float) -> dict[str, object]:
    return {
        "documents": [
            {
                "document_id": "raw-document-id",
                "id": "raw-document-id",
                "title": "Synthetic document",
                "relative_path": "synthetic/document.md",
                "updated_at": "dynamic",
            }
        ],
        "points": [
            {
                "document_id": "raw-document-id",
                "x": 0.0,
                "y": 0.0,
                "cluster_id": 0,
                "cluster_label": "Synthetic",
                "cluster_signature": "raw-cluster-signature",
                "top_terms": [],
                "nearest_neighbors": [],
            }
        ],
        "cluster_labels": {"0": "Synthetic"},
        "clusters": [
            {
                "cluster_id": 0,
                "cluster_signature": "raw-cluster-signature",
                "generated_label": "Synthetic",
                "display_label": "Synthetic",
                "source": "generated",
                "size": 1,
                "document_ids": ["raw-document-id"],
                "top_terms": [{"term": "synthetic", "score": score}],
                "representatives": [
                    {
                        "document_id": "raw-document-id",
                        "title": "Synthetic document",
                        "relative_path": "synthetic/document.md",
                        "score": 0.0,
                    }
                ],
                "warnings": [],
            }
        ],
        "stats": {},
        "warnings": [],
    }


def _build_demo_in_subprocess(*, corpus: Path, output: Path, hash_seed: str) -> bytes:
    environment = os.environ.copy()
    environment["PYTHONHASHSEED"] = hash_seed
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "build_demo_site.py"),
            "--site",
            str(REPO_ROOT / "site"),
            "--corpus",
            str(corpus),
            "--out",
            str(output),
        ],
        cwd=REPO_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return (output / "data" / "tiny-map.json").read_bytes()
