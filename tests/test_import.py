def test_package_imports() -> None:
    import paper_galaxy

    assert isinstance(paper_galaxy.__version__, str)
    assert paper_galaxy.__version__
