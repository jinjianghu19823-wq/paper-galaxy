from __future__ import annotations

import json
from pathlib import Path

from scripts.check_live_site import check_live_site, exit_code_for_report


def test_live_site_checker_passes_against_local_fixture(tmp_path: Path) -> None:
    _write_live_site_fixture(tmp_path)

    report = check_live_site(base_url=_file_base_url(tmp_path))

    assert report["status"] == "PASS"


def test_live_site_checker_fails_on_missing_demo_page(tmp_path: Path) -> None:
    _write_live_site_fixture(tmp_path)
    (tmp_path / "demo" / "index.html").unlink()

    report = check_live_site(base_url=_file_base_url(tmp_path))

    assert report["status"] == "FAIL"
    assert any(issue["path"] == "/demo/" for issue in report["issues"])


def test_live_site_checker_fails_on_invalid_json(tmp_path: Path) -> None:
    _write_live_site_fixture(tmp_path)
    (tmp_path / "data" / "tiny-map.json").write_text("not json", encoding="utf-8")

    report = check_live_site(base_url=_file_base_url(tmp_path))

    assert report["status"] == "FAIL"
    assert any(issue["code"] == "invalid_json" for issue in report["issues"])


def test_allow_not_deployed_exit_code(tmp_path: Path) -> None:
    _write_live_site_fixture(tmp_path)
    (tmp_path / "demo" / "index.html").unlink()

    report = check_live_site(base_url=_file_base_url(tmp_path))

    assert report["status"] == "FAIL"
    assert exit_code_for_report(report, allow_not_deployed=True) == 0
    assert exit_code_for_report(report, allow_not_deployed=False) == 1


def _file_base_url(path: Path) -> str:
    return path.as_uri() + "/"


def _write_live_site_fixture(root: Path) -> None:
    pages = {
        "index.html": "<html><head></head><body>Paper Galaxy home</body></html>",
        "demo/index.html": (
            "<html><body><h1>Static graph demo</h1>"
            '<svg data-graph-canvas=""></svg></body></html>'
        ),
        "privacy/index.html": "<html><body>Privacy</body></html>",
        "install/index.html": "<html><body>Install</body></html>",
        "cloud-library/index.html": "<html><body>Cloud design</body></html>",
        "zh-cn/index.html": "<html><body>本地优先的研究图谱</body></html>",
        "zh-cn/demo/index.html": "<html><body>静态图谱演示</body></html>",
    }
    for relative, text in pages.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "tiny-map.json").write_text(
        json.dumps(
            {
                "documents": [{"id": "demo"}],
                "points": [{"document_id": "demo"}],
                "clusters": [{"cluster_id": 0}],
            }
        ),
        encoding="utf-8",
    )
