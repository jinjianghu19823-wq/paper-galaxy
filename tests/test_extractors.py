import sys
import types
from pathlib import Path

import paper_galaxy.extract as extract_module
from paper_galaxy.extract import extract_file
from paper_galaxy.extract import ocr as ocr_module
from paper_galaxy.extract import pdf as pdf_module
from paper_galaxy.extract.latex import extract_latex_file
from paper_galaxy.extract.markdown import extract_markdown_file
from paper_galaxy.extract.pdf import extract_pdf_file
from paper_galaxy.extract.text import extract_text_file
from paper_galaxy.models import ExtractedContent


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
    assert extracted.method == "markdown"
    assert extracted.metadata["frontmatter_keys"] == ["topic"]
    assert extracted.sections == ("Main Heading",)


def test_markdown_extraction_captures_wikilinks_and_links(tmp_path: Path) -> None:
    path = tmp_path / "note.md"
    path.write_text(
        "\n".join(
            [
                "---",
                "tags: [neural, pde]",
                "---",
                "# Link Note",
                "See [[Fourier Neural Operator|FNO]] and [paper](papers/fno.pdf).",
                "```",
                "[code link](ignore.md)",
                "```",
            ]
        ),
        encoding="utf-8",
    )

    extracted = extract_markdown_file(path)

    assert "FNO" in extracted.text
    assert "paper" in extracted.text
    assert "code link" not in extracted.text
    assert extracted.links == ("Fourier Neural Operator", "papers/fno.pdf")
    assert extracted.metadata["frontmatter"]["tags"] == ["neural", "pde"]


def test_latex_extraction_handles_title_and_sections(tmp_path: Path) -> None:
    path = tmp_path / "paper.tex"
    path.write_text(
        r"""
\title{Spectral Notes}
\author{Ada Lovelace}
\begin{abstract}
We study operator learning.
\end{abstract}
\section{Chebyshev Grids}
Spectral methods use global basis functions.
\begin{theorem}[Approximation]
\label{thm:approx}
The approximation holds.
\end{theorem}
\caption{A spectral diagram}
See \cite{li2021fno,kovachki2023}.
\bibliography{operators}
""",
        encoding="utf-8",
    )

    extracted = extract_latex_file(path)

    assert extracted.title == "Spectral Notes"
    assert "section Chebyshev Grids" in extracted.text
    assert "Spectral methods" in extracted.text
    assert extracted.method == "latex"
    assert extracted.metadata["author"] == "Ada Lovelace"
    assert extracted.metadata["abstract_present"] is True
    assert extracted.sections == ("Chebyshev Grids",)
    assert "thm:approx" in extracted.metadata["latex_labels"]
    assert "theorem:Approximation" in extracted.metadata["latex_labels"]
    assert extracted.metadata["citation_keys"] == ["li2021fno", "kovachki2023"]
    assert extracted.metadata["bibliography_keys"] == ["operators"]
    assert "A spectral diagram" in extracted.text


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


def test_pdf_extractor_marks_low_text_as_scanned_candidate(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF-1.4")

    class FakePage:
        def extract_text(self) -> str:
            return ""

    class FakeReader:
        is_encrypted = False
        metadata = types.SimpleNamespace(title="Metadata Title")

        def __init__(self, source: Path) -> None:
            assert source == path
            self.pages = [FakePage(), FakePage()]

    fake_pypdf = types.SimpleNamespace(PdfReader=FakeReader)
    monkeypatch.setattr(pdf_module, "_pypdf_available", lambda: True)
    monkeypatch.setitem(sys.modules, "pypdf", fake_pypdf)

    extracted, reason = extract_pdf_file(path)

    assert reason is None
    assert extracted is not None
    assert extracted.title == "Metadata Title"
    assert extracted.metadata["page_count"] == 2
    assert extracted.metadata["scanned_pdf_candidate"] is True
    assert "likely scanned PDF" in " ".join(extracted.warnings)


def test_image_ocr_missing_dependency_is_a_skip_reason(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    path = tmp_path / "screen.png"
    path.write_bytes(b"not really an image")
    monkeypatch.setattr(ocr_module, "_pillow_available", lambda: False)

    extracted, reason = extract_file(path, include_images=True, ocr=True)

    assert extracted is None
    assert reason is not None
    assert "Pillow" in reason


def test_image_ocr_dispatch_can_return_extracted_text(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    path = tmp_path / "screen.png"
    path.write_bytes(b"image")

    def fake_ocr(
        source: Path, *, language: str = "eng"
    ) -> tuple[ExtractedContent | None, str | None]:
        assert source == path
        assert language == "deu"
        return (
            ExtractedContent(
                title="screen",
                text="OCR text from a screenshot",
                method="image-ocr-tesseract",
                metadata={"ocr_language": language, "image_size": [200, 100]},
            ),
            None,
        )

    monkeypatch.setattr(extract_module, "extract_image_ocr", fake_ocr)

    extracted, reason = extract_file(
        path,
        include_images=True,
        ocr=True,
        ocr_language="deu",
    )

    assert reason is None
    assert extracted is not None
    assert extracted.method == "image-ocr-tesseract"
    assert extracted.metadata["ocr_language"] == "deu"
