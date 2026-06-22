from pathlib import Path

STATIC_DIR = Path("src/paper_galaxy/web/static")


def test_static_assets_exist() -> None:
    assert (STATIC_DIR / "index.html").exists()
    assert (STATIC_DIR / "app.js").exists()
    assert (STATIC_DIR / "styles.css").exists()


def test_static_assets_have_no_external_network_references() -> None:
    combined = "\n".join(
        (STATIC_DIR / name).read_text(encoding="utf-8")
        for name in ("index.html", "app.js", "styles.css")
    ).lower()

    forbidden = [
        "https://",
        "http://",
        "googleapis",
        "google fonts",
        "unpkg",
        "jsdelivr",
        "cdn",
        "eval(",
    ]
    for token in forbidden:
        assert token not in combined


def test_static_html_references_only_local_assets() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'href="/static/styles.css"' in html
    assert 'src="/static/app.js"' in html
    assert "src=" not in html.replace('src="/static/app.js"', "")
