from __future__ import annotations

import json
import shutil
from pathlib import Path

from scripts.build_demo_site import build_demo_payload, build_demo_site
from scripts.check_demo_site import check_demo_site


def test_build_demo_site_creates_static_output(tmp_path: Path) -> None:
    site_copy = tmp_path / "site"
    dist = tmp_path / "site_dist"
    shutil.copytree(Path("site"), site_copy)

    output = build_demo_site(site_dir=site_copy, output_dir=dist)

    assert output == dist.resolve()
    assert (dist / "index.html").exists()
    assert (dist / "demo" / "index.html").exists()
    assert (dist / "zh-cn" / "index.html").exists()
    assert (dist / "zh-cn" / "demo" / "index.html").exists()
    assert (dist / "data" / "tiny-map.json").exists()


def test_demo_payload_is_safe_and_graph_shaped() -> None:
    payload = build_demo_payload(corpus_dir=Path("examples/tiny_corpus"))
    raw = json.dumps(payload, sort_keys=True)

    assert payload["metadata"]["synthetic_only"] is True
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


def test_site_source_has_simplified_chinese_pages() -> None:
    assert (Path("site") / "zh-cn" / "index.html").exists()
    assert (Path("site") / "zh-cn" / "demo" / "index.html").exists()
    assert "简体中文" in (Path("site") / "index.html").read_text(encoding="utf-8")
    assert "English" in (Path("site") / "zh-cn" / "index.html").read_text(
        encoding="utf-8"
    )
