"""Conservative LaTeX extraction heuristics for Phase 1."""

from __future__ import annotations

import re
from pathlib import Path

from paper_galaxy.models import ExtractedContent

from .text import first_meaningful_line, normalize_whitespace, read_text_with_fallback

TITLE_RE = re.compile(r"\\title\{([^{}]+)\}")
COMMENT_RE = re.compile(r"(?<!\\)%.*")
SECTION_RE = re.compile(
    r"\\(part|chapter|section|subsection|subsubsection)\*?\{([^{}]+)\}"
)
CAPTION_RE = re.compile(r"\\caption\{([^{}]+)\}")
ENV_BEGIN_RE = re.compile(
    r"\\begin\{(theorem|definition|lemma|proposition|remark)\}(?:\[([^\]]+)\])?"
)
COMMAND_WITH_ARG_RE = re.compile(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?\{([^{}]*)\}")
MATH_DELIMITER_RE = re.compile(r"\$\$?|\[|\]")
COMMAND_RE = re.compile(r"\\[a-zA-Z]+")


def extract_latex_file(path: Path) -> ExtractedContent:
    """Extract readable prose from a LaTeX file without parsing TeX fully."""

    raw, warnings = read_text_with_fallback(path)
    title = _latex_title(raw)
    cleaned = COMMENT_RE.sub("", raw)
    cleaned = SECTION_RE.sub(r"\1 \2", cleaned)
    cleaned = CAPTION_RE.sub(r"caption \1", cleaned)
    cleaned = ENV_BEGIN_RE.sub(_environment_text, cleaned)
    cleaned = COMMAND_WITH_ARG_RE.sub(r"\1", cleaned)
    cleaned = MATH_DELIMITER_RE.sub(" ", cleaned)
    cleaned = COMMAND_RE.sub(" ", cleaned)
    cleaned = cleaned.replace("{", " ").replace("}", " ")
    text = normalize_whitespace(cleaned)
    return ExtractedContent(
        title=title or first_meaningful_line(text) or path.stem,
        text=text,
        warnings=tuple(warnings),
    )


def _latex_title(text: str) -> str | None:
    match = TITLE_RE.search(text)
    return normalize_whitespace(match.group(1)) if match else None


def _environment_text(match: re.Match[str]) -> str:
    name = match.group(1)
    label = match.group(2)
    return f"{name} {label or ''} "
