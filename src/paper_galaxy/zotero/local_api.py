"""Read-only Zotero Desktop local API client."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

DEFAULT_LOCAL_API_URL = "http://localhost:23119/api"
API_VERSION = "3"


class ZoteroAPIError(RuntimeError):
    """Raised when the read-only local Zotero API cannot be queried."""


@dataclass(frozen=True)
class _APIResponse:
    data: Any
    headers: dict[str, str]


class LocalZoteroAPIClient:
    """Small stdlib-only read-only client for Zotero Desktop's local API."""

    def __init__(
        self,
        base_url: str = DEFAULT_LOCAL_API_URL,
        *,
        timeout: float = 2.0,
        library_prefix: str = "/users/0",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.library_prefix = library_prefix.rstrip("/")

    def root(self) -> dict[str, Any]:
        """Return local API root metadata if available."""

        response = self.root_response()
        if isinstance(response.data, dict):
            return response.data
        return {"value": response.data, "headers": response.headers}

    def root_response(self) -> _APIResponse:
        """Return the local API root response, allowing Zotero's text/plain root."""

        return self._get("/", allow_non_json=True)

    def collections(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        """Return collections from the local library."""

        return self._list(f"{self.library_prefix}/collections", limit=limit)

    def tags(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        """Return tags from the local library."""

        return self._list(f"{self.library_prefix}/tags", limit=limit)

    def items(
        self,
        *,
        limit: int | None = None,
        start: int = 0,
        since: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return all item records from the local library."""

        params: dict[str, object] = {"start": max(0, start)}
        if since is not None:
            params["since"] = since
        return self._list(f"{self.library_prefix}/items", limit=limit, params=params)

    def top_items(
        self,
        *,
        limit: int | None = None,
        start: int = 0,
        since: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return top-level Zotero items."""

        params: dict[str, object] = {"start": max(0, start)}
        if since is not None:
            params["since"] = since
        return self._list(
            f"{self.library_prefix}/items/top",
            limit=limit,
            params=params,
        )

    def collection_items(
        self,
        collection_key: str,
        *,
        limit: int | None = None,
        since: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return items in one collection."""

        params: dict[str, object] = {}
        if since is not None:
            params["since"] = since
        return self._list(
            f"{self.library_prefix}/collections/{collection_key}/items",
            limit=limit,
            params=params,
        )

    def top_items_page(
        self,
        *,
        limit: int | None = None,
        start: int = 0,
        since: int | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        """Return one top-level item page plus response headers."""

        params: dict[str, object] = {"start": max(0, start)}
        if limit is not None:
            params["limit"] = max(0, limit)
        if since is not None:
            params["since"] = since
        return self._list_page(f"{self.library_prefix}/items/top", params=params)

    def collections_page(
        self, *, limit: int | None = None
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        """Return one collections page plus response headers."""

        params: dict[str, object] = {}
        if limit is not None:
            params["limit"] = max(0, limit)
        return self._list_page(f"{self.library_prefix}/collections", params=params)

    def tags_page(
        self, *, limit: int | None = None
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        """Return one tags page plus response headers."""

        params: dict[str, object] = {}
        if limit is not None:
            params["limit"] = max(0, limit)
        return self._list_page(f"{self.library_prefix}/tags", params=params)

    def item_children(self, item_key: str) -> list[dict[str, Any]]:
        """Return child notes and attachments for one Zotero item."""

        return self._list(f"{self.library_prefix}/items/{item_key}/children")

    def get_json(
        self, path_or_url: str, params: dict[str, object] | None = None
    ) -> Any:
        """GET JSON with Zotero API headers and clear local errors."""

        response = self._get(path_or_url, params=params)
        return response.data

    def _list(
        self,
        path: str,
        *,
        limit: int | None = None,
        params: dict[str, object] | None = None,
    ) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        request_params = dict(params or {})
        if limit is not None:
            request_params["limit"] = max(0, limit)
        next_url: str | None = path
        while next_url:
            response = self._get(
                next_url,
                params=request_params if next_url == path else None,
            )
            if not isinstance(response.data, list):
                raise ZoteroAPIError(f"Expected a JSON list from Zotero path {path}.")
            collected.extend(item for item in response.data if isinstance(item, dict))
            if limit is not None and len(collected) >= limit:
                return collected[:limit]
            next_url = _next_link(response.headers.get("link", ""))
            request_params = {}
        return collected

    def _list_page(
        self, path: str, *, params: dict[str, object] | None = None
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        response = self._get(path, params=params)
        if not isinstance(response.data, list):
            raise ZoteroAPIError(f"Expected a JSON list from Zotero path {path}.")
        return (
            [item for item in response.data if isinstance(item, dict)],
            response.headers,
        )

    def _get(
        self,
        path_or_url: str,
        params: dict[str, object] | None = None,
        *,
        allow_non_json: bool = False,
    ) -> _APIResponse:
        url = self._url(path_or_url, params=params)
        request = Request(url, headers={"Zotero-API-Version": API_VERSION})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                headers = {
                    key.lower(): value for key, value in response.headers.items()
                }
        except HTTPError as exc:
            raise ZoteroAPIError(
                f"Zotero local API returned HTTP {exc.code} for {url}."
            ) from exc
        except URLError as exc:
            raise ZoteroAPIError(
                "Zotero local API is not reachable at "
                f"{self.base_url}. Open Zotero Desktop and make sure the local "
                "API is enabled."
            ) from exc
        except TimeoutError as exc:
            raise ZoteroAPIError(
                f"Zotero local API timed out after {self.timeout:.1f}s at {url}."
            ) from exc
        try:
            data = json.loads(raw.decode("utf-8")) if raw else None
        except json.JSONDecodeError as exc:
            if allow_non_json:
                return _APIResponse(
                    data=raw.decode("utf-8", errors="replace"),
                    headers=headers,
                )
            raise ZoteroAPIError(
                f"Zotero local API returned invalid JSON at {url}."
            ) from exc
        return _APIResponse(data=data, headers=headers)

    def _url(self, path_or_url: str, params: dict[str, object] | None = None) -> str:
        if urlparse(path_or_url).scheme in {"http", "https"}:
            url = path_or_url
        else:
            path = path_or_url if path_or_url.startswith("/") else f"/{path_or_url}"
            url = urljoin(f"{self.base_url}/", path.lstrip("/"))
        if params:
            clean_params = {
                key: value
                for key, value in params.items()
                if value is not None and value != ""
            }
            if clean_params:
                separator = "&" if "?" in url else "?"
                url = f"{url}{separator}{urlencode(clean_params)}"
        return url


def _next_link(header: str) -> str | None:
    for part in header.split(","):
        pieces = part.split(";")
        if len(pieces) < 2:
            continue
        target = pieces[0].strip()
        rels = {piece.strip().lower() for piece in pieces[1:]}
        if 'rel="next"' in rels and target.startswith("<") and target.endswith(">"):
            return target[1:-1]
    return None
