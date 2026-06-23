"""Verify the deployed public Paper Galaxy GitHub Pages site."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "https://jinjianghu19823-wq.github.io/paper-galaxy/"
REQUIRED_PATHS = (
    "/",
    "/demo/",
    "/privacy/",
    "/install/",
    "/cloud-library/",
    "/zh-cn/",
    "/zh-cn/demo/",
    "/data/tiny-map.json",
)
HTML_PATHS = {
    "/",
    "/demo/",
    "/privacy/",
    "/install/",
    "/cloud-library/",
    "/zh-cn/",
    "/zh-cn/demo/",
}
RUNTIME_LINK_RELS = {"stylesheet", "icon", "preload", "modulepreload", "manifest"}
GITHUB_404_MARKERS = (
    "There isn't a GitHub Pages site here.",
    "404 File not found",
    "<title>Site not found",
    "<title>Page not found",
)


@dataclass(frozen=True)
class LiveSiteFetch:
    path: str
    url: str
    status: int | None
    content_type: str
    bytes_read: int
    error: str = ""


@dataclass(frozen=True)
class LiveSiteIssue:
    path: str
    code: str
    message: str


def check_live_site(
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Fetch and validate the deployed static demo site."""

    normalized_base = _normalize_base_url(base_url)
    fetches: list[LiveSiteFetch] = []
    bodies: dict[str, str] = {}
    issues: list[LiveSiteIssue] = []

    for path in REQUIRED_PATHS:
        url = _url_for_path(normalized_base, path)
        fetch, body = _fetch(url=url, path=path, timeout=timeout)
        fetches.append(fetch)
        if fetch.error:
            issues.append(LiveSiteIssue(path, "fetch_error", fetch.error))
            continue
        if fetch.status != 200:
            issues.append(
                LiveSiteIssue(path, "http_status", f"expected 200, got {fetch.status}")
            )
            continue
        if path in HTML_PATHS:
            bodies[path] = body
            issues.extend(_check_html(path=path, text=body))
        elif path == "/data/tiny-map.json":
            issues.extend(_check_demo_json(path=path, text=body))

    issues.extend(_check_page_specific_markers(bodies))
    status = "PASS" if not issues else "FAIL"
    return {
        "status": status,
        "base_url": normalized_base,
        "required_paths": list(REQUIRED_PATHS),
        "fetches": [asdict(fetch) for fetch in fetches],
        "issues": [asdict(issue) for issue in issues],
    }


def exit_code_for_report(report: dict[str, Any], *, allow_not_deployed: bool) -> int:
    """Return the CLI exit code for a report and deployment allowance."""

    if report.get("status") == "PASS":
        return 0
    return 0 if allow_not_deployed else 1


def _normalize_base_url(value: str) -> str:
    return value.rstrip("/") + "/"


def _url_for_path(base_url: str, path: str) -> str:
    relative = path.lstrip("/")
    if base_url.startswith("file:") and (not relative or relative.endswith("/")):
        relative += "index.html"
    return urljoin(base_url, relative)


def _fetch(*, url: str, path: str, timeout: float) -> tuple[LiveSiteFetch, str]:
    request = Request(url, headers={"User-Agent": "paper-galaxy-live-site-check/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
            text = raw.decode(charset, errors="replace")
            return (
                LiveSiteFetch(
                    path=path,
                    url=url,
                    status=getattr(response, "status", None) or 200,
                    content_type=response.headers.get("content-type", ""),
                    bytes_read=len(raw),
                ),
                text,
            )
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return (
            LiveSiteFetch(
                path=path,
                url=url,
                status=exc.code,
                content_type=exc.headers.get("content-type", ""),
                bytes_read=len(body.encode("utf-8")),
                error=f"HTTP {exc.code}",
            ),
            body,
        )
    except (TimeoutError, URLError, OSError) as exc:
        return (
            LiveSiteFetch(
                path=path,
                url=url,
                status=None,
                content_type="",
                bytes_read=0,
                error=str(exc),
            ),
            "",
        )


def _check_html(*, path: str, text: str) -> list[LiveSiteIssue]:
    issues: list[LiveSiteIssue] = []
    if any(marker in text for marker in GITHUB_404_MARKERS):
        issues.append(
            LiveSiteIssue(path, "github_pages_404", "GitHub Pages 404 marker")
        )

    parser = _AssetParser()
    parser.feed(text)
    for tag, attr, value in parser.assets:
        stripped = value.strip()
        if stripped.startswith(("http://", "https://", "//")):
            issues.append(
                LiveSiteIssue(
                    path,
                    "external_runtime_asset",
                    f"{tag}[{attr}]={stripped}",
                )
            )
    return issues


def _check_page_specific_markers(bodies: dict[str, str]) -> list[LiveSiteIssue]:
    issues: list[LiveSiteIssue] = []
    home = bodies.get("/", "")
    if home and "Paper Galaxy" not in home:
        issues.append(LiveSiteIssue("/", "missing_home_marker", "Paper Galaxy"))
    demo = bodies.get("/demo/", "")
    demo_markers = ("Static graph demo", "data-graph-canvas")
    if demo and not all(marker in demo for marker in demo_markers):
        issues.append(
            LiveSiteIssue(
                "/demo/",
                "missing_demo_marker",
                "Static graph demo or graph canvas marker",
            )
        )
    zh_home = bodies.get("/zh-cn/", "")
    if zh_home and not any(marker in zh_home for marker in ("本地优先", "研究图谱")):
        issues.append(
            LiveSiteIssue("/zh-cn/", "missing_chinese_marker", "Chinese title/copy")
        )
    return issues


def _check_demo_json(*, path: str, text: str) -> list[LiveSiteIssue]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return [LiveSiteIssue(path, "invalid_json", str(exc))]

    issues: list[LiveSiteIssue] = []
    for key in ("documents", "points", "clusters"):
        value = payload.get(key)
        if not isinstance(value, list) or not value:
            issues.append(LiveSiteIssue(path, "json_shape", f"{key} missing or empty"))
    return issues


class _AssetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.assets: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        wanted = {
            "script": ("src",),
            "link": ("href",),
            "img": ("src", "srcset"),
            "source": ("src", "srcset"),
            "video": ("src", "poster"),
            "audio": ("src",),
            "iframe": ("src",),
        }.get(tag)
        if not wanted:
            return
        attr_map = {name: value or "" for name, value in attrs}
        if tag == "link":
            rel_tokens = {
                token.lower()
                for token in attr_map.get("rel", "").replace(",", " ").split()
            }
            if not rel_tokens.intersection(RUNTIME_LINK_RELS):
                return
        for attr in wanted:
            value = attr_map.get(attr)
            if value:
                self.assets.append((tag, attr, value))


def _print_summary(report: dict[str, Any], *, allow_not_deployed: bool) -> None:
    status = report["status"]
    prefix = "Live site check"
    if status == "PASS":
        print(f"{prefix}: PASS ({report['base_url']})")
        print(f"Fetched {len(report['fetches'])} required paths successfully.")
        return

    print(f"{prefix}: FAIL ({report['base_url']})")
    if allow_not_deployed:
        print("--allow-not-deployed is set, so this will not fail the command.")
    for issue in report["issues"]:
        print(f"- {issue['path']} {issue['code']}: {issue['message']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--allow-not-deployed", action="store_true")
    args = parser.parse_args()

    report = check_live_site(base_url=args.base_url, timeout=args.timeout)
    if args.json_out:
        args.json_out.write_text(
            json.dumps(report, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    _print_summary(report, allow_not_deployed=args.allow_not_deployed)
    raise SystemExit(
        exit_code_for_report(report, allow_not_deployed=args.allow_not_deployed)
    )


if __name__ == "__main__":
    main()
