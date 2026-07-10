from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from scripts.build_demo_site import build_demo_payload, build_demo_site
from scripts.check_demo_site import check_demo_site


def test_build_demo_site_creates_static_output(tmp_path: Path) -> None:
    site_copy = tmp_path / "site"
    dist = tmp_path / "site_dist"
    shutil.copytree(Path("site"), site_copy)
    source_data = site_copy / "data" / "tiny-map.json"
    source_fixture = b'{"source_fixture":"must-not-change"}\n'
    source_data.write_bytes(source_fixture)

    output = build_demo_site(site_dir=site_copy, output_dir=dist)

    assert output == dist.resolve()
    assert source_data.read_bytes() == source_fixture
    assert (dist / "index.html").exists()
    assert (dist / "demo" / "index.html").exists()
    assert (dist / "zh-cn" / "index.html").exists()
    assert (dist / "zh-cn" / "demo" / "index.html").exists()
    assert (dist / "data" / "tiny-map.json").exists()


def test_refresh_source_data_is_explicit(tmp_path: Path) -> None:
    site_copy = tmp_path / "site"
    dist = tmp_path / "site_dist"
    shutil.copytree(Path("site"), site_copy)
    source_data = site_copy / "data" / "tiny-map.json"
    source_data.write_text('{"stale":true}\n', encoding="utf-8")

    build_demo_site(
        site_dir=site_copy,
        output_dir=dist,
        refresh_source_data=True,
    )

    assert source_data.read_bytes() == (dist / "data" / "tiny-map.json").read_bytes()
    assert (
        json.loads(source_data.read_text(encoding="utf-8"))["metadata"][
            "synthetic_only"
        ]
        is True
    )


def test_demo_payload_is_safe_and_graph_shaped() -> None:
    payload = build_demo_payload(corpus_dir=Path("examples/tiny_corpus"))
    raw = json.dumps(payload, sort_keys=True)

    assert payload["metadata"]["synthetic_only"] is True
    assert payload["metadata"]["contains_real_zotero_data"] is False
    assert payload["metadata"]["zotero_demo"] == "synthetic-feature-description-only"
    assert len(payload["documents"]) == 8
    assert payload["points"]
    assert payload["clusters"]
    assert payload["explanations"]
    assert "/Users/" not in raw
    assert "/private/" not in raw
    assert ".paper-galaxy" not in raw
    assert ".sqlite3" not in raw
    assert all(
        str(document["relative_path"]).startswith(
            (
                "neural_operators/",
                "numerical_pdes/",
                "randomized_nla/",
                "thesis/",
            )
        )
        for document in payload["documents"]
    )


def test_demo_payload_is_identical_across_absolute_corpus_paths(
    tmp_path: Path,
) -> None:
    first_corpus = tmp_path / "first-location" / "tiny_corpus"
    second_corpus = tmp_path / "different" / "absolute-location" / "tiny_corpus"
    first_corpus.parent.mkdir(parents=True)
    second_corpus.parent.mkdir(parents=True)
    shutil.copytree(Path("examples/tiny_corpus"), first_corpus)
    shutil.copytree(Path("examples/tiny_corpus"), second_corpus)

    first = _serialize_payload(build_demo_payload(corpus_dir=first_corpus))
    second = _serialize_payload(build_demo_payload(corpus_dir=second_corpus))

    assert first == second
    assert str(tmp_path).encode() not in first
    assert b".paper-galaxy" not in first
    assert b".sqlite3" not in first
    assert b"zotero.sqlite" not in first


def test_demo_payload_is_identical_across_repeated_builds(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    shutil.copytree(Path("examples/tiny_corpus"), corpus)

    first = _serialize_payload(build_demo_payload(corpus_dir=corpus))
    second = _serialize_payload(build_demo_payload(corpus_dir=corpus))

    assert first == second


def test_demo_payload_uses_only_demo_namespaced_external_ids() -> None:
    payload = build_demo_payload(corpus_dir=Path("examples/tiny_corpus"))
    documents = payload["documents"]
    points = payload["points"]
    clusters = payload["clusters"]
    explanations = payload["explanations"]

    document_ids = {str(document["document_id"]) for document in documents}
    cluster_signatures = {str(cluster["cluster_signature"]) for cluster in clusters}

    assert all(document_id.startswith("demo_doc_") for document_id in document_ids)
    assert all(
        signature.startswith("demo_cluster_") for signature in cluster_signatures
    )
    assert [document["relative_path"] for document in documents] == sorted(
        document["relative_path"] for document in documents
    )

    for point in points:
        assert point["document_id"] in document_ids
        assert point["cluster_signature"] in cluster_signatures
        assert point["nearest_neighbors"] == sorted(
            point["nearest_neighbors"],
            key=lambda neighbor: (-neighbor["score"], neighbor["relative_path"]),
        )
        assert all(
            neighbor["document_id"] in document_ids
            for neighbor in point["nearest_neighbors"]
        )

    for cluster in clusters:
        assert set(cluster["document_ids"]).issubset(document_ids)
        assert all(
            representative["document_id"] in document_ids
            for representative in cluster["representatives"]
        )
        assert cluster["top_terms"] == sorted(
            cluster["top_terms"],
            key=lambda term: (-term["score"], term["term"]),
        )

    for explanation in explanations:
        assert explanation["source"]["document_id"] in document_ids
        assert explanation["target"]["document_id"] in document_ids
        assert explanation["shared_terms"] == sorted(
            explanation["shared_terms"],
            key=lambda term: (-term["score"], term["term"]),
        )
        for match in explanation["chunk_matches"]:
            assert match["source_chunk_id"].startswith("demo_chunk_")
            assert match["target_chunk_id"].startswith("demo_chunk_")


def test_check_demo_site_accepts_generated_site(tmp_path: Path) -> None:
    site_copy = tmp_path / "site"
    dist = tmp_path / "site_dist"
    shutil.copytree(Path("site"), site_copy)
    build_demo_site(site_dir=site_copy, output_dir=dist)

    assert check_demo_site(dist_dir=dist) == []


def test_check_demo_site_rejects_external_runtime_asset(tmp_path: Path) -> None:
    site_copy = tmp_path / "site"
    dist = tmp_path / "site_dist"
    shutil.copytree(Path("site"), site_copy)
    build_demo_site(site_dir=site_copy, output_dir=dist)
    (dist / "demo" / "index.html").write_text(
        '<script src="https://example.invalid/app.js"></script>',
        encoding="utf-8",
    )

    issues = check_demo_site(dist_dir=dist)

    assert any(issue.code == "external_runtime_asset" for issue in issues)


def test_check_demo_site_rejects_real_zotero_markers(tmp_path: Path) -> None:
    site_copy = tmp_path / "site"
    dist = tmp_path / "site_dist"
    shutil.copytree(Path("site"), site_copy)
    build_demo_site(site_dir=site_copy, output_dir=dist)
    data_path = dist / "data" / "tiny-map.json"
    payload = json.loads(data_path.read_text(encoding="utf-8"))
    payload["metadata"]["contains_real_zotero_data"] = True
    payload["documents"][0]["path"] = "zotero://items/REALKEY"
    data_path.write_text(json.dumps(payload), encoding="utf-8")

    issues = check_demo_site(dist_dir=dist)

    assert any(issue.code == "demo_json_zotero_boundary_missing" for issue in issues)
    assert any(issue.code == "demo_json_local_path" for issue in issues)


def test_site_source_has_simplified_chinese_pages() -> None:
    assert (Path("site") / "zh-cn" / "index.html").exists()
    assert (Path("site") / "zh-cn" / "demo" / "index.html").exists()
    assert "简体中文" in (Path("site") / "index.html").read_text(encoding="utf-8")
    assert "English" in (Path("site") / "zh-cn" / "index.html").read_text(
        encoding="utf-8"
    )


def _serialize_payload(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
