from pathlib import Path

STATIC_DIR = Path("src/paper_galaxy/web/static")


def test_static_assets_exist() -> None:
    assert (STATIC_DIR / "index.html").exists()
    assert (STATIC_DIR / "app.js").exists()
    assert (STATIC_DIR / "graph.js").exists()
    assert (STATIC_DIR / "styles.css").exists()


def test_static_assets_have_no_external_network_references() -> None:
    combined = "\n".join(
        (STATIC_DIR / name).read_text(encoding="utf-8")
        for name in ("index.html", "app.js", "graph.js", "styles.css")
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
    assert 'src="/static/graph.js"' in html
    assert 'src="/static/app.js"' in html
    remaining_html = html.replace('src="/static/graph.js"', "").replace(
        'src="/static/app.js"',
        "",
    )
    assert "src=" not in remaining_html


def test_static_graph_assets_include_dynamic_interaction_primitives() -> None:
    combined = "\n".join(
        (STATIC_DIR / name).read_text(encoding="utf-8")
        for name in ("app.js", "graph.js")
    )

    required_tokens = [
        "requestAnimationFrame",
        "pointerdown",
        "pointermove",
        "pointerup",
        "hoveredId",
        "adjacency",
        "dragging",
        "panning",
        "wheel",
        "localStorage",
        "resetLayout",
        "nearest_neighbors",
    ]
    for token in required_tokens:
        assert token in combined


def test_graph_labels_default_to_focus_only() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    graph_js = (STATIC_DIR / "graph.js").read_text(encoding="utf-8")

    assert '<option value="focus" selected>Focus labels only</option>' in html
    assert 'labelMode: "focus"' in graph_js
    assert 'this.settings.labelMode === "focus"' in graph_js


def test_graph_labels_do_not_use_small_corpus_global_default() -> None:
    graph_js = (STATIC_DIR / "graph.js").read_text(encoding="utf-8")

    assert "this.nodes.length <= 120" not in graph_js
    assert "shouldShowLabel" in graph_js
    assert "ambientLabelBudget" in graph_js
    assert "boxesOverlap" in graph_js


def test_graph_labels_keep_focus_context_visible() -> None:
    graph_js = (STATIC_DIR / "graph.js").read_text(encoding="utf-8")

    required_tokens = [
        "isHovered || isSelected || isNeighbor",
        "visibleLabelBoxes",
        "is-selected",
        "is-focused",
    ]
    for token in required_tokens:
        assert token in graph_js
