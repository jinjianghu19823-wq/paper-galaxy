"""Conservative LaTeX extraction heuristics for Phase 1."""

from __future__ import annotations

import re
from pathlib import Path

from paper_galaxy.models import ExtractedContent

from .text import first_meaningful_line, normalize_whitespace, read_text_with_fallback

TITLE_RE = re.compile(r"\\title\{([^{}]+)\}")
AUTHOR_RE = re.compile(r"\\author\{([^{}]+)\}")
COMMENT_RE = re.compile(r"(?<!\\)%.*")
SECTION_RE = re.compile(
    r"\\(part|chapter|section|subsection|subsubsection)\*?\{([^{}]+)\}"
)
ABSTRACT_RE = re.compile(
    r"\\begin\{abstract\}(.*?)\\end\{abstract\}", re.DOTALL | re.IGNORECASE
)
CAPTION_RE = re.compile(r"\\caption\{([^{}]+)\}")
STRUCTURED_ENV_RE = re.compile(
    r"\\begin\{(theorem|definition|lemma|proposition|remark)\}"
    r"(?:\[([^\]]+)\])?(.*?)\\end\{\1\}",
    re.DOTALL,
)
CITATION_RE = re.compile(r"\\cite[a-zA-Z*]*?(?:\[[^\]]*\])*\{([^{}]+)\}")
BIBLIOGRAPHY_RE = re.compile(r"\\bibliography\{([^{}]+)\}")
ADDBIBRESOURCE_RE = re.compile(r"\\addbibresource(?:\[[^\]]*\])?\{([^{}]+)\}")
LABEL_RE = re.compile(r"\\label\{([^{}]+)\}")
COMMAND_WITH_ARG_RE = re.compile(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?\{([^{}]*)\}")
MATH_DELIMITER_RE = re.compile(r"\$\$?|\[|\]")
COMMAND_RE = re.compile(r"\\[a-zA-Z]+")


def extract_latex_file(path: Path) -> ExtractedContent:
    """Extract readable prose from a LaTeX file without parsing TeX fully."""

    raw, warnings = read_text_with_fallback(path)
    title = _latex_title(raw)
    author = _latex_author(raw)
    cleaned = COMMENT_RE.sub("", raw)
    abstract = _abstract(cleaned)
    sections = tuple(_sections(cleaned))
    captions = tuple(_captions(cleaned))
    citation_keys = tuple(_citation_keys(cleaned))
    bibliography_keys = tuple(_bibliography_keys(cleaned))
    latex_labels = tuple(_latex_labels(cleaned))
    cleaned = ABSTRACT_RE.sub(r"abstract \1", cleaned)
    cleaned = SECTION_RE.sub(_section_text, cleaned)
    cleaned = CAPTION_RE.sub(r"caption \1", cleaned)
    cleaned = STRUCTURED_ENV_RE.sub(_environment_text, cleaned)
    cleaned = COMMAND_WITH_ARG_RE.sub(r"\1", cleaned)
    cleaned = MATH_DELIMITER_RE.sub(" ", cleaned)
    cleaned = COMMAND_RE.sub(" ", cleaned)
    cleaned = cleaned.replace("{", " ").replace("}", " ")
    text = normalize_whitespace(cleaned)
    if len(text) < 50:
        warnings.append("LaTeX extraction produced very little readable text")
    return ExtractedContent(
        title=title or first_meaningful_line(text) or path.stem,
        text=text,
        warnings=tuple(warnings),
        method="latex",
        metadata={
            "author": author or "",
            "abstract": abstract or "",
            "abstract_present": abstract is not None,
            "citation_keys": list(citation_keys),
            "bibliography_keys": list(bibliography_keys),
            "latex_labels": list(latex_labels),
            "captions": list(captions),
        },
        sections=sections,
        links=tuple(
            [f"cite:{key}" for key in citation_keys]
            + [f"bib:{key}" for key in bibliography_keys]
        ),
    )


def _latex_title(text: str) -> str | None:
    match = TITLE_RE.search(text)
    return normalize_whitespace(match.group(1)) if match else None


def _latex_author(text: str) -> str | None:
    match = AUTHOR_RE.search(text)
    return normalize_whitespace(match.group(1)) if match else None


def _abstract(text: str) -> str | None:
    match = ABSTRACT_RE.search(text)
    return normalize_whitespace(match.group(1)) if match else None


def _sections(text: str) -> list[str]:
    return [normalize_whitespace(match.group(2)) for match in SECTION_RE.finditer(text)]


def _captions(text: str) -> list[str]:
    return [normalize_whitespace(match.group(1)) for match in CAPTION_RE.finditer(text)]


def _citation_keys(text: str) -> list[str]:
    keys: list[str] = []
    for match in CITATION_RE.finditer(text):
        keys.extend(key.strip() for key in match.group(1).split(",") if key.strip())
    return list(dict.fromkeys(keys))


def _bibliography_keys(text: str) -> list[str]:
    keys: list[str] = []
    for regex in (BIBLIOGRAPHY_RE, ADDBIBRESOURCE_RE):
        for match in regex.finditer(text):
            keys.extend(key.strip() for key in match.group(1).split(",") if key.strip())
    return list(dict.fromkeys(keys))


def _latex_labels(text: str) -> list[str]:
    labels = list(LABEL_RE.findall(text))
    for match in STRUCTURED_ENV_RE.finditer(text):
        env_name = match.group(1)
        optional_label = match.group(2)
        if optional_label:
            labels.append(f"{env_name}:{normalize_whitespace(optional_label)}")
    return list(dict.fromkeys(normalize_whitespace(label) for label in labels if label))


def _section_text(match: re.Match[str]) -> str:
    return f"{match.group(1)} {match.group(2)}"


def _environment_text(match: re.Match[str]) -> str:
    name = match.group(1)
    label = match.group(2)
    body = match.group(3)
    return f"{name} {label or ''} {body} "
