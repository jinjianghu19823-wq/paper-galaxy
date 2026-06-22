from pathlib import Path

from paper_galaxy.extract import pdf as pdf_module
from paper_galaxy.extract.latex import extract_latex_file
from paper_galaxy.extract.markdown import extract_markdown_file
from paper_galaxy.extract.pdf import extract_pdf_file
from paper_galaxy.extract.text import extract_text_file


def test_text_extraction_normalizes_plain_text(tmp_path: Path) -> None:
    path = tmp_path / "note.txt"
    path.write_text("Title line\n\nLots   of\tspace.", encoding="utf-8")

    extracted = extract_text_file(path)

    assert extracted.title == "Title line"
    assert extracted.text == "Title line Lots of space."


def test_markdown_extraction_prefers_first_heading(tmp_path: Path) -> None:
    path = tmp_path / "note.md"
    path.write_text(
        "\n".join(
            [
                "---",
                "topic: Numerical PDEs",
                "---",
                "# Main Heading",
                "",
                "```python",
                "print('skip')",
                "```",
                "Body text.",
            ]
        ),
        encoding="utf-8",
    )

    extracted = extract_markdown_file(path)

    assert extracted.title == "Main Heading"
    assert "Numerical PDEs" in extracted.text
    assert "Main Heading" in extracted.text
    assert "print" not in extracted.text


def test_latex_extraction_handles_title_and_sections(tmp_path: Path) -> None:
    path = tmp_path / "paper.tex"
    path.write_text(
        r"""
\title{Spectral Notes}
\section{Chebyshev Grids}
Spectral methods use global basis functions.
""",
        encoding="utf-8",
    )

    extracted = extract_latex_file(path)

    assert extracted.title == "Spectral Notes"
    assert "section Chebyshev Grids" in extracted.text
    assert "Spectral methods" in extracted.text


def test_pdf_extractor_skips_when_pypdf_missing(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(pdf_module, "_pypdf_available", lambda: False)

    extracted, reason = extract_pdf_file(path)

    assert extracted is None
    assert reason is not None
    assert "pypdf" in reason
