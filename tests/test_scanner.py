from pathlib import Path

from paper_galaxy.ingest.scanner import discover_files, relative_path


def test_scanner_finds_supported_files_deterministically(tmp_path: Path) -> None:
    (tmp_path / "b").mkdir()
    (tmp_path / "a").mkdir()
    (tmp_path / "b" / "note.txt").write_text("text", encoding="utf-8")
    (tmp_path / "a" / "paper.md").write_text("# Paper", encoding="utf-8")
    (tmp_path / "a" / "ignore.png").write_text("image", encoding="utf-8")

    files = discover_files(tmp_path)

    assert [relative_path(path, tmp_path) for path in files] == [
        "a/paper.md",
        "b/note.txt",
    ]


def test_scanner_ignores_generated_and_hidden_dirs(tmp_path: Path) -> None:
    for dirname in [
        ".git",
        ".paper-galaxy",
        ".venv",
        "node_modules",
        "__pycache__",
        ".hidden",
    ]:
        directory = tmp_path / dirname
        directory.mkdir()
        (directory / "ignored.md").write_text("# Ignored", encoding="utf-8")
    (tmp_path / "visible").mkdir()
    (tmp_path / "visible" / "kept.tex").write_text(r"\section{Kept}", encoding="utf-8")

    files = discover_files(tmp_path)

    assert [relative_path(path, tmp_path) for path in files] == ["visible/kept.tex"]
