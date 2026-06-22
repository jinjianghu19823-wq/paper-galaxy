"""Markdown extraction heuristics."""

from __future__ import annotations

import re
from pathlib import Path

from paper_galaxy.models import ExtractedContent

from .text import first_meaningful_line, normalize_whitespace, read_text_with_fallback

HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
CODE_FENCE_RE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def extract_markdown_file(path: Path) -> ExtractedContent:
    """Extract Markdown prose while keeping headings useful for topics."""

    raw, warnings = read_text_with_fallback(path)
    title = _first_heading(raw)
    body = _remove_code_fences(raw)
    frontmatter, body = _parse_frontmatter(body)
    frontmatter_values = _frontmatter_text_values(frontmatter)
    links: list[str] = []
    body = _replace_links(body, links)
    sections = tuple(_headings(body))
    body = HEADING_RE.sub(r"\1", body)
    cleaned = normalize_whitespace(" ".join([*frontmatter_values, body]))
    return ExtractedContent(
        title=title or first_meaningful_line(cleaned) or path.stem,
        text=cleaned,
        warnings=tuple(warnings),
        method="markdown",
        metadata={
            "frontmatter": frontmatter,
            "frontmatter_keys": sorted(frontmatter),
            "link_count": len(links),
        },
        sections=sections,
        links=tuple(dict.fromkeys(links)),
    )


def _first_heading(text: str) -> str | None:
    match = HEADING_RE.search(text)
    return match.group(1).strip() if match else None


def _remove_code_fences(text: str) -> str:
    return CODE_FENCE_RE.sub(" ", text)


def _parse_frontmatter(text: str) -> tuple[dict[str, object], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text

    metadata: dict[str, object] = {}
    current_list_key: str | None = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return metadata, "\n".join(lines[index + 1 :])
        stripped = line.strip()
        if not stripped:
            continue
        if current_list_key and stripped.startswith("- "):
            values = metadata.setdefault(current_list_key, [])
            if isinstance(values, list):
                values.append(_clean_frontmatter_value(stripped[2:]))
            continue
        current_list_key = None
        if ":" in line:
            key, value = line.split(":", 1)
            key = key.strip()
            if not key:
                continue
            parsed = _parse_frontmatter_value(value)
            metadata[key] = parsed
            if parsed == "":
                current_list_key = key
    return metadata, text


def _parse_frontmatter_value(value: str) -> object:
    stripped = _clean_frontmatter_value(value)
    if stripped.startswith("[") and stripped.endswith("]"):
        items = [
            _clean_frontmatter_value(item)
            for item in stripped[1:-1].split(",")
            if _clean_frontmatter_value(item)
        ]
        return items
    return stripped


def _clean_frontmatter_value(value: str) -> str:
    return value.strip().strip("\"'")


def _frontmatter_text_values(metadata: dict[str, object]) -> list[str]:
    values: list[str] = []
    for value in metadata.values():
        if isinstance(value, str) and value:
            values.append(value)
        elif isinstance(value, list):
            values.extend(str(item) for item in value if str(item).strip())
    return values


def _replace_links(text: str, links: list[str]) -> str:
    def replace_wikilink(match: re.Match[str]) -> str:
        target = normalize_whitespace(match.group(1))
        label = normalize_whitespace(match.group(2) or target)
        if target:
            links.append(target)
        return label

    def replace_markdown_link(match: re.Match[str]) -> str:
        label = normalize_whitespace(match.group(1))
        target = normalize_whitespace(match.group(2))
        if target:
            links.append(target)
        return label

    text = WIKILINK_RE.sub(replace_wikilink, text)
    return MARKDOWN_LINK_RE.sub(replace_markdown_link, text)


def _headings(text: str) -> list[str]:
    return [normalize_whitespace(match.group(1)) for match in HEADING_RE.finditer(text)]
