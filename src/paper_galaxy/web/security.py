"""Loopback Web security boundaries for the local Paper Galaxy workspace."""

from __future__ import annotations

import ipaddress
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import SplitResult, urlsplit

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
WRITE_TOKEN_HEADER = "X-Paper-Galaxy-Write-Token"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "connect-src 'self'",
        "font-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    )
)


@dataclass(frozen=True, slots=True)
class LocalWebSecurity:
    """Per-process credentials and allowlists for one loopback app."""

    write_token: str

    @classmethod
    def create(cls) -> LocalWebSecurity:
        return cls(write_token=secrets.token_urlsafe(32))


def install_local_security(app: Any, security: LocalWebSecurity) -> None:
    """Reject rebinding/cross-origin writes and add browser hardening headers."""

    from fastapi.responses import JSONResponse

    @app.middleware("http")
    async def local_security_middleware(request: Any, call_next: Any) -> Any:
        host = _validated_request_host(request)
        if host is None:
            response = JSONResponse(
                status_code=421,
                content={
                    "error": {
                        "code": "host_not_allowed",
                        "message": "Request Host is not allowed by the local app.",
                    }
                },
            )
            return _add_security_headers(response)

        if request.method.upper() not in SAFE_METHODS:
            if not _origin_matches_request(request, host):
                response = JSONResponse(
                    status_code=403,
                    content={
                        "error": {
                            "code": "origin_not_allowed",
                            "message": "A same-origin request is required for writes.",
                        }
                    },
                )
                return _add_security_headers(response)
            supplied = request.headers.get(WRITE_TOKEN_HEADER, "")
            if not secrets.compare_digest(supplied, security.write_token):
                response = JSONResponse(
                    status_code=403,
                    content={
                        "error": {
                            "code": "write_token_invalid",
                            "message": "The local write token is missing or invalid.",
                        }
                    },
                )
                return _add_security_headers(response)

        try:
            response = await call_next(request)
        except Exception:
            # Never let an application exception bypass the hardening headers or
            # expose a local traceback/path through Starlette's default response.
            response = JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "code": "internal_server_error",
                        "message": (
                            "The local request failed. Inspect the Paper Galaxy "
                            "CLI for details."
                        ),
                    }
                },
            )
        return _add_security_headers(response)


def _validated_request_host(request: Any) -> str | None:
    raw_host = request.headers.get("host", "")
    parsed = _parse_authority(raw_host)
    if parsed is None or parsed.hostname is None:
        return None
    hostname = parsed.hostname.lower()
    if hostname in _LOOPBACK_HOSTS:
        return raw_host
    try:
        if ipaddress.ip_address(hostname).is_loopback:
            return raw_host
    except ValueError:
        pass
    client = getattr(request, "client", None)
    if hostname == "testserver" and getattr(client, "host", None) == "testclient":
        return raw_host
    return None


def _origin_matches_request(request: Any, raw_host: str) -> bool:
    raw_origin = request.headers.get("origin")
    if raw_origin is None:
        return False
    try:
        origin = urlsplit(raw_origin)
    except ValueError:
        return False
    if not _plain_origin(origin):
        return False
    request_scheme = str(request.url.scheme).lower()
    if origin.scheme.lower() != request_scheme:
        return False
    origin_authority = origin.netloc.lower()
    return origin_authority == raw_host.lower()


def _parse_authority(value: str) -> SplitResult | None:
    if not value or any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in value
    ):
        return None
    try:
        parsed = urlsplit(f"//{value}")
        if parsed.netloc != value or parsed.path or parsed.query or parsed.fragment:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        # Accessing port validates malformed and out-of-range ports.
        _ = parsed.port
        return parsed
    except ValueError:
        return None


def _plain_origin(origin: SplitResult) -> bool:
    if origin.scheme.lower() not in {"http", "https"}:
        return False
    if not origin.netloc or origin.hostname is None:
        return False
    if origin.username is not None or origin.password is not None:
        return False
    if origin.path not in {"", "/"} or origin.query or origin.fragment:
        return False
    try:
        _ = origin.port
    except ValueError:
        return False
    return True


def _add_security_headers(response: Any) -> Any:
    response.headers["Content-Security-Policy"] = _CONTENT_SECURITY_POLICY
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    )
    response.headers["Cache-Control"] = "no-store"
    return response
