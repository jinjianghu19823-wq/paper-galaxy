from pathlib import Path

STATIC_DIR = Path("src/paper_galaxy/web/static")


def test_static_assets_exist() -> None:
    assert (STATIC_DIR / "index.html").exists()
    assert (STATIC_DIR / "app.js").exists()
    assert (STATIC_DIR / "graph.js").exists()
    assert (STATIC_DIR / "i18n.js").exists()
    assert (STATIC_DIR / "styles.css").exists()


def test_static_assets_have_no_external_network_references() -> None:
    combined = "\n".join(
        (STATIC_DIR / name).read_text(encoding="utf-8")
        for name in ("index.html", "app.js", "graph.js", "i18n.js", "styles.css")
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
    assert 'src="/static/i18n.js"' in html
    assert 'src="/static/app.js"' in html
    remaining_html = (
        html.replace('src="/static/graph.js"', "")
        .replace(
            'src="/static/i18n.js"',
            "",
        )
        .replace(
            'src="/static/app.js"',
            "",
        )
    )
    assert "src=" not in remaining_html


def test_static_graph_assets_include_dynamic_interaction_primitives() -> None:
    combined = "\n".join(
        (STATIC_DIR / name).read_text(encoding="utf-8")
        for name in ("app.js", "graph.js", "i18n.js")
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
        "cluster_signature",
        "renameCluster",
        "resetClusterLabel",
        "explainPair",
        "Evidence",
        "pair-explanation",
    ]
    for token in required_tokens:
        assert token in combined


def test_graph_labels_default_to_focus_only() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    graph_js = (STATIC_DIR / "graph.js").read_text(encoding="utf-8")

    assert 'value="focus"' in html
    assert "Focus labels only" in html
    assert 'data-i18n="layout.labelFocus"' in html
    assert 'labelMode: "focus"' in graph_js
    assert 'this.settings.labelMode === "focus"' in graph_js


def test_static_web_app_uses_quiet_research_tool_copy_and_tokens() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    i18n_js = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
    combined = "\n".join([html, app_js, i18n_js, styles])
    visible_copy = "\n".join([html, i18n_js])

    required = [
        "Details",
        "Layout",
        "Evidence",
        "Related papers",
        "Terms",
        "similarity links",
        "详情",
        "布局",
        "证据",
        "相关论文",
        "术语",
        "相似度连接",
        "paper-card",
        "status-strip",
        "Local research map",
    ]
    for token in required:
        assert token in combined

    forbidden = [
        "Inspector",
        "Forces",
        "Why nearby?",
        "Nearest neighbors",
        "Top terms",
        "semantic TF-IDF links",
        "backdrop-filter",
        "drop-shadow",
        "text-transform",
        "#a78bfa",
        "#6d5bd0",
        "#c4b5fd",
    ]
    for token in forbidden[:6]:
        assert token not in visible_copy
    for token in forbidden[6:]:
        assert token not in styles


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


def test_graph_nodes_do_not_render_nested_pin_dots() -> None:
    graph_js = (STATIC_DIR / "graph.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert "pinMarker" not in graph_js
    assert "graph-node-pin" not in graph_js
    assert "graph-node-pin" not in styles
    assert "is-pinned" in graph_js


def test_static_web_app_supports_simplified_chinese_locale() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    i18n_js = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    assert 'id="language-toggle"' in html
    assert 'data-i18n="search.title"' in html
    assert 'data-i18n-placeholder="search.placeholder"' in html
    assert "paper-galaxy:language" in i18n_js
    assert '"zh-CN"' in i18n_js
    assert "简体中文" in i18n_js
    assert "切换到英文" in i18n_js
    assert "toggleLanguage" in app_js
    assert "static/i18n.js" in pyproject


def test_static_web_app_includes_zotero_reading_graph_controls() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    i18n_js = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")

    required_html = [
        'value="zotero"',
        'id="zotero-status-filter"',
        'id="zotero-tag-filter"',
        'id="zotero-collection-filter"',
        'data-i18n="zotero.filters"',
    ]
    for token in required_html:
        assert token in html

    required_js = [
        "/api/zotero/status",
        "/api/zotero/reading-map",
        "renderZoteroInspector",
        "graphSource",
        "zoteroStatusFilter",
    ]
    for token in required_js:
        assert token in app_js

    assert "Zotero Reading Graph" in i18n_js
    assert "Zotero 阅读图谱" in i18n_js
