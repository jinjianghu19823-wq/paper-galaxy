from pathlib import Path


def test_github_pages_workflow_exists_and_builds_demo_site() -> None:
    workflow = Path(".github/workflows/pages.yml").read_text(encoding="utf-8")

    assert "actions/configure-pages" in workflow
    assert "actions/upload-pages-artifact" in workflow
    assert "actions/deploy-pages" in workflow
    assert "contents: read" in workflow
    assert "pages: write" in workflow
    assert "id-token: write" in workflow
    assert "github.event.repository.private == true" in workflow
    assert "github.event.repository.private == false" in workflow
    assert "enablement: true" in workflow
    assert "python scripts/build_demo_site.py --out site_dist" in workflow
    assert "python scripts/check_demo_site.py --dist site_dist" in workflow


def test_cloud_design_docs_exist_and_are_design_only() -> None:
    required = [
        "docs/CLOUD_LIBRARY_DESIGN.md",
        "docs/cloud-library/README.md",
        "docs/cloud-library/PRODUCT_SPEC.md",
        "docs/cloud-library/ARCHITECTURE.md",
        "docs/cloud-library/PRIVACY_AND_SECURITY.md",
        "docs/cloud-library/API_SKETCH.md",
        "docs/cloud-library/DATA_MODEL.md",
        "docs/cloud-library/ROADMAP.md",
        "docs/cloud-library/THREAT_MODEL.md",
    ]
    for relative in required:
        text = Path(relative).read_text(encoding="utf-8").lower()
        assert (
            "not implemented" in text or "design-only" in text or "design only" in text
        )
        assert "opt-in" in text or "local-first" in text

    source_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("src/paper_galaxy").rglob("*.py")
    )
    forbidden_imports = ["boto3", "google.cloud", "azure.storage", "stripe"]
    for token in forbidden_imports:
        assert token not in source_text


def test_community_files_exist() -> None:
    required = [
        "CODE_OF_CONDUCT.md",
        "CITATION.cff",
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/ISSUE_TEMPLATE/feature_request.yml",
        ".github/ISSUE_TEMPLATE/config.yml",
        ".github/pull_request_template.md",
    ]
    for relative in required:
        assert Path(relative).exists()


def test_readme_links_public_demo_and_cloud_design() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "https://jinjianghu19823-wq.github.io/paper-galaxy/" in readme
    assert "Simplified Chinese" in readme
    assert "docs/CLOUD_LIBRARY_DESIGN.md" in readme
