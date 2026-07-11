"""Read-only Zotero Desktop local API client."""

from __future__ import annotations

import ipaddress
import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import (
    SplitResult,
    unquote,
    urlencode,
    urljoin,
    urlparse,
    urlsplit,
    urlunsplit,
)
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from paper_galaxy.zotero.models import ZoteroDeletedBatch, ZoteroSyncBatch

DEFAULT_LOCAL_API_URL = "http://localhost:23119/api"
API_VERSION = "3"
_MAX_SYNC_PAGES = 10_000
_MAX_ITEM_KEYS_PER_REQUEST = 50
MAX_SYNC_RESULT_LIMIT = 10_000
_ITEM_KEY_PATTERN = re.compile(r"[A-Za-z0-9]{1,64}\Z")
_TRANSIENT_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


def canonical_local_api_url(value: object) -> str:
    """Return a canonical HTTP loopback Zotero API URL or reject it."""

    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ValueError("Zotero local API URL is missing or invalid.")
    if any(
        character.isspace() or unicodedata.category(character).startswith("C")
        for character in value
    ):
        raise ValueError("Zotero local API URL is malformed.")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Zotero local API URL is malformed.") from exc
    if (
        parsed.scheme.lower() != "http"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.netloc.endswith(":")
    ):
        raise ValueError(
            "Zotero local API URL must be HTTP loopback without credentials, "
            "query parameters, or a fragment."
        )
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname != "localhost":
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError as exc:
            raise ValueError("Zotero local API host must be loopback.") from exc
        if not address.is_loopback:
            raise ValueError("Zotero local API host must be loopback.")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Zotero local API port is invalid.")
    if "\\" in parsed.path:
        raise ValueError("Zotero local API path is invalid.")
    host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = f"{host}:{port}" if port is not None else host
    path = parsed.path or ""
    return urlunsplit(SplitResult("http", netloc, path, "", ""))


class ZoteroAPIError(RuntimeError):
    """Raised when the read-only local Zotero API cannot be queried."""


class ZoteroAPICancelled(ZoteroAPIError):
    """Raised when a caller cancels at a read-only API page boundary."""


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Any,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Any:
        del newurl
        raise HTTPError(
            req.full_url,
            code,
            "Zotero local API redirects are disabled.",
            headers,
            fp,
        )


def _open_local_request(request: Request, *, timeout: float) -> Any:
    return build_opener(ProxyHandler({}), _RejectRedirects()).open(
        request, timeout=timeout
    )


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
        max_attempts: int = 3,
    ) -> None:
        self.base_url = canonical_local_api_url(base_url).rstrip("/")
        self.timeout = timeout
        self.library_prefix = library_prefix.rstrip("/")
        if isinstance(max_attempts, bool) or not 1 <= max_attempts <= 5:
            raise ValueError("Zotero local API max_attempts must be between 1 and 5.")
        self.max_attempts = max_attempts

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

    def sync_collections(
        self,
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> ZoteroSyncBatch:
        """Return every collection with one version-fenced library snapshot."""

        return self._sync_list(
            f"{self.library_prefix}/collections",
            params={"start": 0},
            cancel_requested=cancel_requested,
        )

    def sync_items(
        self,
        *,
        since: int,
        limit: int | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> ZoteroSyncBatch:
        """Return changed parents and children without per-parent requests."""

        _validate_since(since)
        params: dict[str, object] = {"since": since, "start": 0}
        if limit is not None:
            _validate_sync_limit(limit)
            params["limit"] = limit
        return self._sync_list(
            f"{self.library_prefix}/items",
            params=params,
            result_limit=limit,
            cancel_requested=cancel_requested,
        )

    def items_by_keys(
        self,
        keys: tuple[str, ...],
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> ZoteroSyncBatch:
        """Fetch explicit item keys in API-sized batches with version fencing."""

        unique_keys = tuple(dict.fromkeys(keys))
        if any(not _ITEM_KEY_PATTERN.fullmatch(key) for key in unique_keys):
            raise ValueError("Zotero item keys must be bounded alphanumeric values.")
        if not unique_keys:
            raise ValueError("At least one Zotero item key is required.")
        records: list[dict[str, Any]] = []
        version: int | None = None
        for offset in range(0, len(unique_keys), _MAX_ITEM_KEYS_PER_REQUEST):
            _raise_if_cancelled(cancel_requested)
            batch = unique_keys[offset : offset + _MAX_ITEM_KEYS_PER_REQUEST]
            page = self._sync_list(
                f"{self.library_prefix}/items",
                params={"itemKey": ",".join(batch), "start": 0},
                cancel_requested=cancel_requested,
            )
            version = _same_library_version(version, page.library_version)
            records.extend(page.records)
        return ZoteroSyncBatch(tuple(records), _require_version(version))

    def deleted_since(
        self,
        *,
        since: int,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> ZoteroDeletedBatch:
        """Return Zotero's read-only deletion log since a library version."""

        _validate_since(since)
        _raise_if_cancelled(cancel_requested)
        response = self._get(
            f"{self.library_prefix}/deleted",
            params={"since": since},
        )
        _raise_if_cancelled(cancel_requested)
        version = _library_version(response.headers)
        if not isinstance(response.data, dict):
            raise ZoteroAPIError("Expected a JSON object from Zotero deleted feed.")
        object_keys: dict[str, tuple[str, ...]] = {}
        for object_type in ("collections", "searches", "items", "tags", "settings"):
            raw = response.data.get(object_type, [])
            if not isinstance(raw, list) or any(
                not isinstance(value, str) or not value for value in raw
            ):
                raise ZoteroAPIError(
                    f"Zotero deleted feed contains invalid {object_type} keys."
                )
            object_keys[object_type] = tuple(raw)
        return ZoteroDeletedBatch(
            object_keys=object_keys,
            library_version=version,
        )

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

    def _sync_list(
        self,
        path: str,
        *,
        params: dict[str, object] | None = None,
        result_limit: int | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> ZoteroSyncBatch:
        """Collect a strict list while requiring one version across every page."""

        collected: list[dict[str, Any]] = []
        seen_records: dict[str, bytes] = {}
        request_params = dict(params or {})
        next_url: str | None = path
        visited: set[str] = set()
        library_version: int | None = None
        complete = True
        page_count = 0
        while next_url:
            _raise_if_cancelled(cancel_requested)
            page_count += 1
            if page_count > _MAX_SYNC_PAGES:
                raise ZoteroAPIError("Zotero pagination exceeded the safe page limit.")
            url = self._url(
                next_url,
                params=request_params if next_url == path else None,
            )
            if url in visited:
                raise ZoteroAPIError("Zotero pagination returned a repeated page URL.")
            visited.add(url)
            response = self._get(
                next_url,
                params=request_params if next_url == path else None,
            )
            page_version = _library_version(response.headers)
            try:
                library_version = _same_library_version(library_version, page_version)
            except ZoteroAPIError as exc:
                raise ZoteroAPIError(
                    "Zotero library changed during pagination; retry the sync."
                ) from exc
            if not isinstance(response.data, list) or any(
                not isinstance(item, dict) for item in response.data
            ):
                raise ZoteroAPIError(f"Expected a JSON object list from {path}.")
            for item in response.data:
                key = _record_key(item)
                encoded = json.dumps(
                    item,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
                previous = seen_records.get(key)
                if previous is not None:
                    if previous != encoded:
                        raise ZoteroAPIError(
                            f"Zotero returned divergent duplicate item {key}."
                        )
                    continue
                seen_records[key] = encoded
                collected.append(item)
            _raise_if_cancelled(cancel_requested)
            next_link = _next_link(response.headers.get("link", ""))
            if result_limit is not None and len(collected) >= result_limit:
                complete = next_link is None and len(collected) <= result_limit
                collected = collected[:result_limit]
                break
            next_url = next_link
            request_params = {}
        return ZoteroSyncBatch(
            records=tuple(collected),
            library_version=_require_version(library_version),
            complete=complete,
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
        raw: bytes | None = None
        headers: dict[str, str] = {}
        last_error: HTTPError | URLError | TimeoutError | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                with _open_local_request(request, timeout=self.timeout) as response:
                    raw = response.read()
                    headers = {
                        key.lower(): value for key, value in response.headers.items()
                    }
                break
            except HTTPError as exc:
                last_error = exc
                if (
                    exc.code not in _TRANSIENT_HTTP_STATUSES
                    or attempt == self.max_attempts
                ):
                    raise ZoteroAPIError(
                        f"Zotero local API returned HTTP {exc.code} for {url}."
                    ) from exc
            except URLError as exc:
                last_error = exc
                if attempt == self.max_attempts:
                    raise ZoteroAPIError(
                        "Zotero local API is not reachable at "
                        f"{self.base_url}. Open Zotero Desktop and make sure the local "
                        "API is enabled."
                    ) from exc
            except TimeoutError as exc:
                last_error = exc
                if attempt == self.max_attempts:
                    message = (
                        f"Zotero local API timed out after {self.timeout:.1f}s "
                        f"at {url}."
                    )
                    raise ZoteroAPIError(message) from exc
        if raw is None:
            raise ZoteroAPIError(
                "Zotero local API request failed safely."
            ) from last_error
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
        return _validate_same_origin_url(self.base_url, url)


def _validate_same_origin_url(base_url: str, candidate: str) -> str:
    try:
        base = urlsplit(base_url)
        parsed = urlsplit(candidate)
        base_port = base.port or 80
        candidate_port = parsed.port or 80
    except ValueError as exc:
        raise ZoteroAPIError("Zotero local API returned a malformed URL.") from exc
    if (
        parsed.scheme.lower() != "http"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.hostname is None
        or base.hostname is None
        or parsed.hostname.lower().rstrip(".") != base.hostname.lower().rstrip(".")
        or candidate_port != base_port
    ):
        raise ZoteroAPIError(
            "Zotero local API pagination must stay on the configured loopback origin."
        )
    decoded_path = unquote(parsed.path)
    base_path = unquote(base.path).rstrip("/")
    if (
        "\\" in decoded_path
        or any(segment in {".", ".."} for segment in decoded_path.split("/"))
        or not (decoded_path == base_path or decoded_path.startswith(f"{base_path}/"))
        or len(parsed.query) > 8192
        or any(character.isspace() for character in parsed.query)
    ):
        raise ZoteroAPIError("Zotero local API returned an unsafe local URL.")
    return candidate


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


def _validate_since(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("Zotero since version must be a non-negative integer.")


def _validate_sync_limit(value: int) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= MAX_SYNC_RESULT_LIMIT
    ):
        raise ValueError(
            f"Zotero sync limit must be between 1 and {MAX_SYNC_RESULT_LIMIT}."
        )


def _raise_if_cancelled(
    cancel_requested: Callable[[], bool] | None,
) -> None:
    if cancel_requested is not None and cancel_requested():
        raise ZoteroAPICancelled(
            "Zotero local API sync was cancelled at a pagination boundary."
        )


def _library_version(headers: dict[str, str]) -> int:
    raw = headers.get("last-modified-version")
    if raw is None or not raw.isdigit():
        raise ZoteroAPIError(
            "Zotero sync response is missing a valid Last-Modified-Version."
        )
    version = int(raw)
    if version < 0:
        raise ZoteroAPIError("Zotero library version must not be negative.")
    return version


def _same_library_version(current: int | None, candidate: int) -> int:
    if current is not None and current != candidate:
        raise ZoteroAPIError("Zotero library version changed during the sync.")
    return candidate


def _require_version(value: int | None) -> int:
    if value is None:
        raise ZoteroAPIError("Zotero sync did not return a library version.")
    return value


def _record_key(record: dict[str, Any]) -> str:
    data = record.get("data")
    nested = data if isinstance(data, dict) else {}
    key = record.get("key") or nested.get("key")
    if not isinstance(key, str) or not key or len(key) > 200:
        raise ZoteroAPIError("Zotero sync returned an item without a valid key.")
    return key
