"""Normalize Zotero API JSON into Paper Galaxy dataclasses."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

from paper_galaxy.zotero.models import (
    ZoteroAttachment,
    ZoteroCollection,
    ZoteroCreator,
    ZoteroItem,
    ZoteroLibrary,
    ZoteroNote,
    ZoteroTag,
)


def normalize_item(payload: dict[str, Any]) -> ZoteroItem:
    """Normalize one Zotero item API object."""

    data = _data(payload)
    library_payload = _dict(payload.get("library"))
    library = ZoteroLibrary(
        id=str(library_payload.get("id", "0")),
        type=str(library_payload.get("type", "user")),
        name=str(library_payload.get("name", "Zotero Local Library")),
    )
    creators = tuple(_creator(item) for item in _list(data.get("creators")))
    tags = tuple(_tag(item) for item in _list(data.get("tags")))
    date = _optional_text(data.get("date"))
    return ZoteroItem(
        key=str(payload.get("key") or data.get("key") or ""),
        version=_optional_int(payload.get("version", data.get("version"))),
        library=library,
        item_type=str(data.get("itemType", "")),
        title=_optional_text(data.get("title")) or "Untitled Zotero item",
        creators=creators,
        abstract_note=_optional_text(data.get("abstractNote")),
        date=date,
        year=_year(date),
        publication_title=_optional_text(
            data.get("publicationTitle") or data.get("proceedingsTitle")
        ),
        doi=_optional_text(data.get("DOI")),
        url=_optional_text(data.get("url")),
        archive=_optional_text(data.get("archive")),
        tags=tags,
        collections=tuple(str(value) for value in _list(data.get("collections"))),
        date_added=_optional_text(data.get("dateAdded")),
        date_modified=_optional_text(data.get("dateModified")),
        extra=_optional_text(data.get("extra")),
        relations=_dict(data.get("relations")),
        parent_key=_optional_text(data.get("parentItem")),
        raw=payload,
    )


def normalize_collection(payload: dict[str, Any]) -> ZoteroCollection:
    """Normalize one Zotero collection API object."""

    data = _data(payload)
    return ZoteroCollection(
        key=str(payload.get("key") or data.get("key") or ""),
        name=_optional_text(data.get("name")) or "Untitled collection",
        parent_key=_optional_text(data.get("parentCollection")),
        version=_optional_int(payload.get("version", data.get("version"))),
        raw=payload,
    )


def normalize_child(payload: dict[str, Any]) -> ZoteroAttachment | ZoteroNote | None:
    """Normalize a child note or attachment."""

    data = _data(payload)
    item_type = str(data.get("itemType", ""))
    key = str(payload.get("key") or data.get("key") or "")
    version = _optional_int(payload.get("version", data.get("version")))
    parent_key = _optional_text(data.get("parentItem"))
    if item_type == "note":
        return ZoteroNote(
            key=key,
            version=version,
            text=html_to_text(_optional_text(data.get("note")) or ""),
            parent_key=parent_key,
            raw=payload,
        )
    if item_type == "attachment":
        return ZoteroAttachment(
            key=key,
            version=version,
            title=_optional_text(data.get("title")) or "Attachment",
            filename=_optional_text(data.get("filename")),
            content_type=_optional_text(data.get("contentType")),
            path=_optional_text(data.get("path")),
            link_mode=_optional_text(data.get("linkMode")),
            parent_key=parent_key,
            raw=payload,
        )
    return None


def attach_children(
    item: ZoteroItem, children: list[ZoteroAttachment | ZoteroNote]
) -> ZoteroItem:
    """Return a copy of an item with normalized child records attached."""

    return ZoteroItem(
        key=item.key,
        version=item.version,
        library=item.library,
        item_type=item.item_type,
        title=item.title,
        creators=item.creators,
        abstract_note=item.abstract_note,
        date=item.date,
        year=item.year,
        publication_title=item.publication_title,
        doi=item.doi,
        url=item.url,
        archive=item.archive,
        tags=item.tags,
        collections=item.collections,
        date_added=item.date_added,
        date_modified=item.date_modified,
        extra=item.extra,
        relations=item.relations,
        parent_key=item.parent_key,
        attachments=tuple(
            child for child in children if isinstance(child, ZoteroAttachment)
        ),
        notes=tuple(child for child in children if isinstance(child, ZoteroNote)),
        raw=item.raw,
    )


def html_to_text(value: str) -> str:
    """Strip Zotero note HTML conservatively with stdlib HTMLParser."""

    parser = _TextHTMLParser()
    parser.feed(value)
    parser.close()
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


class _TextHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"br", "p", "div", "li"}:
            self.parts.append(" ")


def _creator(payload: object) -> ZoteroCreator:
    data = payload if isinstance(payload, dict) else {}
    return ZoteroCreator(
        creator_type=str(data.get("creatorType", "author")),
        first_name=_optional_text(data.get("firstName")),
        last_name=_optional_text(data.get("lastName")),
        name=_optional_text(data.get("name")),
    )


def _tag(payload: object) -> ZoteroTag:
    data = payload if isinstance(payload, dict) else {}
    return ZoteroTag(
        tag=str(data.get("tag", "")),
        type=_optional_int(data.get("type")),
    )


def _data(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _year(date: str | None) -> str | None:
    if not date:
        return None
    match = re.search(r"(18|19|20|21)\d{2}", date)
    return match.group(0) if match else None
