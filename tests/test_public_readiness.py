from __future__ import annotations

import json
from pathlib import Path

from scripts.public_readiness_check import run_public_readiness


def test_public_readiness_passes_on_clean_fixture(tmp_path: Path) -> None:
    fixture = _public_fixture(tmp_path)

    report = run_public_readiness(root=fixture)

    assert report["status"] == "PASS"


def test_public_readiness_catches_fake_secret(tmp_path: Path) -> None:
    fixture = _public_fixture(tmp_path)
    key_name = "OPENAI" + "_API_KEY"
    (fixture / "secret.py").write_text(
        f'{key_name} = "abcdefghijklmnopqrstuvwxyz"\n',
        encoding="utf-8",
    )

    report = run_public_readiness(root=fixture)

    assert report["status"] == "FAIL"
    assert _check_detail(report, "likely-secrets")


def test_public_readiness_catches_forbidden_artifact(tmp_path: Path) -> None:
    fixture = _public_fixture(tmp_path)
    (fixture / ".paper-galaxy").mkdir()

    report = run_public_readiness(root=fixture)

    assert report["status"] == "FAIL"
    assert ".paper-galaxy" in _check_detail(report, "generated-local-artifacts")


def test_public_readiness_catches_missing_community_file(tmp_path: Path) -> None:
    fixture = _public_fixture(tmp_path)
    (fixture / ".github" / "pull_request_template.md").unlink()

    report = run_public_readiness(root=fixture)

    assert report["status"] == "FAIL"
    assert "pull_request_template" in _check_detail(report, "github-community-files")


def test_public_readiness_checks_site_asset_policy(tmp_path: Path) -> None:
    fixture = _public_fixture(tmp_path)
    (fixture / "site_dist" / "index.html").write_text(
        '<link rel="stylesheet" href="https://example.invalid/style.css">',
        encoding="utf-8",
    )

    report = run_public_readiness(root=fixture)

    assert report["status"] == "FAIL"
    assert "external_runtime_asset" in _check_detail(report, "static-demo-site")


def _check_detail(report: dict[str, object], name: str) -> str:
    checks = report["checks"]
    assert isinstance(checks, list)
    for check in checks:
        if isinstance(check, dict) and check["name"] == name:
            return str(check["detail"])
    raise AssertionError(f"Missing check {name}")


def _public_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for name in (
        "LICENSE",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "CODE_OF_CONDUCT.md",
        "CITATION.cff",
    ):
        (root / name).write_text(f"# {name}\n", encoding="utf-8")
    (root / "README.md").write_text(
        """
# Paper Galaxy

Quickstart
Live demo
Privacy
Local-first
python -m pip install -e ".[dev,ml,pdf,app]"
Screenshot
Contributing
""",
        encoding="utf-8",
    )
    (root / ".gitignore").write_text("site_dist/\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        """
[project]
version = "0.1.0"

[tool.setuptools.package-data]
"paper_galaxy.storage" = ["schema.sql"]
"paper_galaxy.web" = ["static/index.html", "static/app.js"]
""",
        encoding="utf-8",
    )
    version_dir = root / "src" / "paper_galaxy"
    version_dir.mkdir(parents=True)
    (version_dir / "__init__.py").write_text('__version__ = "0.1.0"\n')

    issue_dir = root / ".github" / "ISSUE_TEMPLATE"
    issue_dir.mkdir(parents=True)
    for relative in (
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/ISSUE_TEMPLATE/feature_request.yml",
        ".github/ISSUE_TEMPLATE/config.yml",
        ".github/pull_request_template.md",
        ".github/workflows/pages.yml",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.name == "pages.yml":
            path.write_text(
                """
name: Pages
jobs:
  deploy:
    steps:
      - if: ${{ github.repository_visibility != 'public' }}
        run: echo skipped
      - if: ${{ github.repository_visibility == 'public' }}
        uses: actions/configure-pages@v5
        with:
          enablement: true
""",
                encoding="utf-8",
            )
        else:
            path.write_text("name: placeholder\n", encoding="utf-8")

    for base in (root / "site", root / "site_dist"):
        _write_minimal_site(base)
    return root


def _write_minimal_site(base: Path) -> None:
    for relative in (
        "index.html",
        "demo/index.html",
        "privacy/index.html",
        "install/index.html",
        "cloud-library/index.html",
        "zh-cn/index.html",
        "zh-cn/demo/index.html",
        "assets/styles.css",
        "assets/demo.js",
        "assets/graph-demo.js",
    ):
        path = base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        content = "<html></html>" if path.suffix == ".html" else ""
        path.write_text(content, encoding="utf-8")
    data_dir = base / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "tiny-map.json").write_text(
        json.dumps(
            {
                "metadata": {"synthetic_only": True},
                "documents": [{"document_id": "demo", "relative_path": "demo.md"}],
                "points": [{"document_id": "demo", "x": 0, "y": 0}],
                "clusters": [{"cluster_id": 0, "display_label": "Demo"}],
                "explanations": [],
            }
        ),
        encoding="utf-8",
    )
