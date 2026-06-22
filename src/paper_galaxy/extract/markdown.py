"""Markdown extraction heuristics."""

from __future__ import annotations

import re
from pathlib import Path

from paper_galaxy.models import ExtractedContent

from .text import first_meaningful_line, normalize_whitespace, read_text_with_fallback

HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
CODE_FENCE_RE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)


def extract_markdown_file(path: Path) -> ExtractedContent:
    """Extract Markdown prose while keeping headings useful for topics."""

    raw, warnings = read_text_with_fallback(path)
    title = _first_heading(raw)
    body = _remove_code_fences(raw)
    frontmatter_values, body = _strip_frontmatter(body)
    body = HEADING_RE.sub(r"\1", body)
    cleaned = normalize_whitespace(" ".join([*frontmatter_values, body]))
    return ExtractedContent(
        title=title or first_meaningful_line(cleaned) or path.stem,
        text=cleaned,
        warnings=tuple(warnings),
    )


def _first_heading(text: str) -> str | None:
    match = HEADING_RE.search(text)
    return match.group(1).strip() if match else None


def _remove_code_fences(text: str) -> str:
    return CODE_FENCE_RE.sub(" ", text)


def _strip_frontmatter(text: str) -> tuple[list[str], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return [], text

    values: list[str] = []
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return values, "\n".join(lines[index + 1 :])
        if ":" in line:
            _, value = line.split(":", 1)
            value = value.strip().strip("\"'")
            if value:
                values.append(value)
    return values, text
