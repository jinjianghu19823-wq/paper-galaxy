from paper_galaxy.plugins import get_plugin_registry


def test_builtin_plugins_are_local_and_include_extractors() -> None:
    plugins = get_plugin_registry().list_payloads()
    ids = {str(plugin["id"]) for plugin in plugins}

    assert "extractor.text" in ids
    assert "extractor.markdown" in ids
    assert "extractor.latex" in ids
    assert "extractor.pdf-pypdf" in ids
    assert "extractor.image-ocr-tesseract" in ids
    assert all(plugin["local_only"] is True for plugin in plugins)
    assert not any("http" in str(plugin) for plugin in plugins)
