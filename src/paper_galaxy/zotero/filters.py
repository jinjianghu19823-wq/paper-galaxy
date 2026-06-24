"""Shared Zotero filter and option validation helpers."""

from __future__ import annotations

from dataclasses import dataclass

from paper_galaxy.zotero.models import ZoteroCollection, ZoteroItem

VALID_READING_STATUSES = {"all", "read", "reading", "to_read", "unknown"}
READING_STATUS_ALIASES = {"unclassified": "unknown"}
SUPPORTED_LOCAL_LIBRARY_ALIASES = {"local", "user", "users/0", "/users/0"}


class ZoteroFilterError(ValueError):
    """Raised when a user supplied Zotero filter cannot be resolved."""


@dataclass(frozen=True)
class ReadingStatusSelection:
    """Validated reading status selection."""

    value: str
    warning: str | None = None


@dataclass(frozen=True)
class CollectionSelection:
    """Resolved collection filter details."""

    key: str
    name: str
    path: str
    matched_by: str


def normalize_reading_status(
    value: str, *, option_name: str = "--status", allow_all: bool = True
) -> ReadingStatusSelection:
    """Validate a reading status value and normalize deprecated aliases."""

    normalized = value.strip().lower()
    if normalized in READING_STATUS_ALIASES:
        target = READING_STATUS_ALIASES[normalized]
        return ReadingStatusSelection(
            value=target,
            warning=(f"{option_name}={value!r} is deprecated; use {target!r} instead."),
        )
    allowed = set(VALID_READING_STATUSES)
    if not allow_all:
        allowed.discard("all")
    if normalized not in allowed:
        labels = ", ".join(sorted(allowed | set(READING_STATUS_ALIASES)))
        raise ZoteroFilterError(
            f"Invalid {option_name} value {value!r}. Expected one of: {labels}."
        )
    return ReadingStatusSelection(value=normalized)


def normalize_local_library(value: str) -> str:
    """Validate the beta-supported Zotero library argument."""

    normalized = value.strip().lower().lstrip("/")
    if value.strip().lower() in SUPPORTED_LOCAL_LIBRARY_ALIASES:
        return "/users/0"
    if normalized in SUPPORTED_LOCAL_LIBRARY_ALIASES:
        return "/users/0"
    raise ZoteroFilterError(
        "Only Zotero Desktop local user library (/users/0) is supported in "
        "this beta. Zotero group/cloud libraries are not implemented."
    )


def validate_non_empty_values(values: tuple[str, ...], *, option_name: str) -> None:
    """Reject empty repeatable CLI filter values."""

    if any(not value.strip() for value in values):
        raise ZoteroFilterError(f"{option_name} values must be non-empty.")


def collection_paths(collections: list[ZoteroCollection]) -> dict[str, str]:
    """Return stable human-readable collection paths by collection key."""

    by_key = {collection.key: collection for collection in collections}

    def path_for(key: str, seen: set[str] | None = None) -> str:
        seen = seen or set()
        if key in seen or key not in by_key:
            return key
        seen.add(key)
        collection = by_key[key]
        if collection.parent_key and collection.parent_key in by_key:
            return f"{path_for(collection.parent_key, seen)} / {collection.name}"
        return collection.name

    return {collection.key: path_for(collection.key) for collection in collections}


def resolve_collection(
    collections: list[ZoteroCollection], query: str
) -> CollectionSelection:
    """Resolve a collection key, exact name, or exact path."""

    cleaned = query.strip()
    if not cleaned:
        raise ZoteroFilterError("Collection filter must be non-empty.")
    paths = collection_paths(collections)
    by_key = {collection.key: collection for collection in collections}
    if cleaned in by_key:
        collection = by_key[cleaned]
        return CollectionSelection(
            key=collection.key,
            name=collection.name,
            path=paths.get(collection.key, collection.name),
            matched_by="key",
        )

    exact_matches = _collection_name_path_matches(collections, paths, cleaned)
    if len(exact_matches) == 1:
        collection = exact_matches[0]
        return CollectionSelection(
            key=collection.key,
            name=collection.name,
            path=paths.get(collection.key, collection.name),
            matched_by="name_or_path",
        )
    if len(exact_matches) > 1:
        raise ZoteroFilterError(_ambiguous_collection_message(exact_matches, paths))

    lowered = cleaned.casefold()
    insensitive = [
        collection
        for collection in collections
        if collection.name.casefold() == lowered
        or paths.get(collection.key, collection.name).casefold() == lowered
    ]
    if len(insensitive) == 1:
        collection = insensitive[0]
        return CollectionSelection(
            key=collection.key,
            name=collection.name,
            path=paths.get(collection.key, collection.name),
            matched_by="name_or_path_case_insensitive",
        )
    if len(insensitive) > 1:
        raise ZoteroFilterError(_ambiguous_collection_message(insensitive, paths))

    raise ZoteroFilterError(
        f"No Zotero collection matched {query!r}. Run "
        "paper-galaxy zotero collections to list available collections."
    )


def filter_items(
    items: list[ZoteroItem],
    *,
    collection_key: str | None,
    tags: tuple[str, ...],
    item_types: tuple[str, ...],
) -> list[ZoteroItem]:
    """Apply item-type, tag, and collection filters to normalized items."""

    selected: list[ZoteroItem] = []
    allowed_types = {
        item_type.strip()
        for part in item_types
        for item_type in part.split(",")
        if item_type.strip()
    }
    for item in items:
        if allowed_types and item.item_type not in allowed_types:
            continue
        item_tags = {tag_row.tag for tag_row in item.tags}
        if tags and any(tag not in item_tags for tag in tags):
            continue
        if collection_key and collection_key not in item.collections:
            continue
        selected.append(item)
    return selected


def _collection_name_path_matches(
    collections: list[ZoteroCollection], paths: dict[str, str], query: str
) -> list[ZoteroCollection]:
    return [
        collection
        for collection in collections
        if collection.name == query
        or paths.get(collection.key, collection.name) == query
    ]


def _ambiguous_collection_message(
    collections: list[ZoteroCollection], paths: dict[str, str]
) -> str:
    rows = [
        f"{collection.key}: {paths.get(collection.key, collection.name)}"
        for collection in collections
    ]
    return (
        "Collection name is ambiguous. Use one of these collection keys: "
        + "; ".join(rows)
    )
