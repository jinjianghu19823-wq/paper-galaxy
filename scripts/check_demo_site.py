"""Check the generated static Paper Galaxy demo site."""

from __future__ import annotations

import argparse
import json
import threading
from dataclasses import dataclass
from functools import partial
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.request import urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIST_DIR = REPO_ROOT / "site_dist"
DEFAULT_CORPUS_DIR = REPO_ROOT / "examples" / "tiny_corpus"

TEXT_SUFFIXES = {".html", ".css", ".js", ".json", ".svg"}
FORBIDDEN_TEXT_TOKENS = (
    "unpkg",
    "jsdelivr",
    "googleapis",
    "gstatic",
    "analytics.js",
    "gtag(",
    "eval(",
)
RUNTIME_ATTRS = {
    "script": ("src",),
    "link": ("href",),
    "img": ("src", "srcset"),
    "source": ("src", "srcset"),
    "video": ("src", "poster"),
    "audio": ("src",),
    "iframe": ("src",),
}
RUNTIME_LINK_RELS = {"stylesheet", "icon", "preload", "modulepreload", "manifest"}
PUBLIC_BASE_URL = "https://jinjianghu19823-wq.github.io/paper-galaxy/"


@dataclass(frozen=True)
class DemoSiteIssue:
    code: str
    message: str


def check_demo_site(
    *,
    dist_dir: Path = DEFAULT_DIST_DIR,
    corpus_dir: Path = DEFAULT_CORPUS_DIR,
    serve: bool = False,
) -> list[DemoSiteIssue]:
    """Return static demo site issues. Empty means pass."""

    dist_dir = dist_dir.resolve()
    corpus_dir = corpus_dir.resolve()
    issues: list[DemoSiteIssue] = []
    if not dist_dir.exists():
        return [DemoSiteIssue("dist_missing", f"Missing site output: {dist_dir}")]

    required = [
        "index.html",
        "demo/index.html",
        "privacy/index.html",
        "install/index.html",
        "cloud-library/index.html",
        "zh-cn/index.html",
        "zh-cn/demo/index.html",
        "data/tiny-map.json",
    ]
    for relative in required:
        if not (dist_dir / relative).exists():
            issues.append(DemoSiteIssue("required_file_missing", relative))

    data_path = dist_dir / "data" / "tiny-map.json"
    if data_path.exists():
        issues.extend(_check_demo_json(data_path, corpus_dir))

    issues.extend(_check_runtime_assets(dist_dir))
    issues.extend(_check_metadata(dist_dir))
    issues.extend(_check_text_tokens(dist_dir))
    if serve and not issues:
        issues.extend(_serve_smoke(dist_dir))
    return issues


def _check_demo_json(data_path: Path, corpus_dir: Path) -> list[DemoSiteIssue]:
    issues: list[DemoSiteIssue] = []
    try:
        payload = json.loads(data_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [DemoSiteIssue("demo_json_invalid", str(exc))]

    if not isinstance(payload.get("documents"), list) or not payload["documents"]:
        issues.append(DemoSiteIssue("demo_json_documents_missing", "documents"))
    if not isinstance(payload.get("points"), list) or not payload["points"]:
        issues.append(DemoSiteIssue("demo_json_points_missing", "points"))
    if not isinstance(payload.get("clusters"), list) or not payload["clusters"]:
        issues.append(DemoSiteIssue("demo_json_clusters_missing", "clusters"))
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict) or metadata.get("synthetic_only") is not True:
        issues.append(DemoSiteIssue("demo_json_not_synthetic", "metadata"))

    raw_json = data_path.read_text(encoding="utf-8")
    forbidden_fragments = [
        "/Users/",
        "/private/",
        "\\Users\\",
        ".paper-galaxy",
        ".sqlite3",
    ]
    for fragment in forbidden_fragments:
        if fragment in raw_json:
            issues.append(DemoSiteIssue("demo_json_local_path", fragment))

    normalized_json = _compact(raw_json)
    for source_file in corpus_dir.rglob("*"):
        if not source_file.is_file():
            continue
        source_text = _compact(source_file.read_text(encoding="utf-8"))
        if len(source_text) > 120 and source_text in normalized_json:
            issues.append(
                DemoSiteIssue(
                    "demo_json_full_source_text",
                    str(source_file.relative_to(corpus_dir)),
                )
            )
    for match in _all_chunk_matches(payload):
        for key in ("source_excerpt", "target_excerpt"):
            if len(str(match.get(key, ""))) > 180:
                issues.append(DemoSiteIssue("demo_json_excerpt_too_long", key))
    return issues


def _check_runtime_assets(dist_dir: Path) -> list[DemoSiteIssue]:
    issues: list[DemoSiteIssue] = []
    for html_path in dist_dir.rglob("*.html"):
        parser = _AssetParser()
        parser.feed(html_path.read_text(encoding="utf-8"))
        for tag, attr, value in parser.assets:
            value = value.strip()
            if value.startswith(("http://", "https://", "//")):
                issues.append(
                    DemoSiteIssue(
                        "external_runtime_asset",
                        f"{html_path.relative_to(dist_dir)} {tag}[{attr}]={value}",
                    )
                )
    return issues


def _check_metadata(dist_dir: Path) -> list[DemoSiteIssue]:
    issues: list[DemoSiteIssue] = []
    required_meta = {
        ("name", "description"),
        ("property", "og:title"),
        ("property", "og:description"),
        ("property", "og:type"),
        ("property", "og:url"),
        ("property", "og:image"),
        ("name", "twitter:card"),
    }
    for html_path in dist_dir.rglob("*.html"):
        relative = str(html_path.relative_to(dist_dir))
        parser = _MetadataParser()
        parser.feed(html_path.read_text(encoding="utf-8"))
        present = {(kind, key) for kind, key, content in parser.meta if content.strip()}
        for _kind, key in sorted(required_meta - present):
            issues.append(DemoSiteIssue("metadata_missing", f"{relative} {key}"))
        if not any(rel == "canonical" and href for rel, _, href in parser.links):
            issues.append(DemoSiteIssue("metadata_missing", f"{relative} canonical"))
        alternate_langs = {
            hreflang
            for rel, hreflang, href in parser.links
            if rel == "alternate" and href
        }
        if not {"en", "zh-CN"}.issubset(alternate_langs):
            issues.append(
                DemoSiteIssue("metadata_missing", f"{relative} language alternates")
            )
        og_images = [
            content
            for kind, key, content in parser.meta
            if kind == "property" and key == "og:image"
        ]
        for og_image in og_images:
            local_path = _local_asset_from_url(og_image)
            if local_path is None:
                issues.append(DemoSiteIssue("metadata_external_image", og_image))
            elif not (dist_dir / local_path).exists():
                issues.append(DemoSiteIssue("metadata_image_missing", og_image))
    return issues


def _check_text_tokens(dist_dir: Path) -> list[DemoSiteIssue]:
    issues: list[DemoSiteIssue] = []
    for path in dist_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8").lower()
        normalized = text.replace("http://www.w3.org/2000/svg", "")
        if path.suffix.lower() in {".js", ".css", ".svg"}:
            if "https://" in normalized or "http://" in normalized:
                issues.append(
                    DemoSiteIssue(
                        "external_runtime_url",
                        str(path.relative_to(dist_dir)),
                    )
                )
        for token in FORBIDDEN_TEXT_TOKENS:
            if token in text:
                issues.append(
                    DemoSiteIssue(
                        "forbidden_token",
                        f"{path.relative_to(dist_dir)} contains {token}",
                    )
                )
    return issues


def _serve_smoke(dist_dir: Path) -> list[DemoSiteIssue]:
    issues: list[DemoSiteIssue] = []
    handler = partial(SimpleHTTPRequestHandler, directory=str(dist_dir))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        for path in (
            "/",
            "/demo/",
            "/privacy/",
            "/install/",
            "/cloud-library/",
            "/zh-cn/",
            "/zh-cn/demo/",
            "/data/tiny-map.json",
        ):
            with urlopen(base_url + path, timeout=5) as response:
                if response.status != 200:
                    issues.append(DemoSiteIssue("serve_status", path))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    return issues


def _all_chunk_matches(payload: dict[str, Any]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    explanations = payload.get("explanations", [])
    if not isinstance(explanations, list):
        return matches
    for explanation in explanations:
        if isinstance(explanation, dict) and isinstance(
            explanation.get("chunk_matches"), list
        ):
            matches.extend(
                match
                for match in explanation["chunk_matches"]
                if isinstance(match, dict)
            )
    return matches


def _compact(value: str) -> str:
    return " ".join(value.split())


class _AssetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.assets: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        wanted = RUNTIME_ATTRS.get(tag)
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


class _MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.meta: list[tuple[str, str, str]] = []
        self.links: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {name: value or "" for name, value in attrs}
        if tag == "meta":
            if attr_map.get("name"):
                self.meta.append(
                    ("name", attr_map["name"], attr_map.get("content", ""))
                )
            if attr_map.get("property"):
                self.meta.append(
                    ("property", attr_map["property"], attr_map.get("content", ""))
                )
        elif tag == "link":
            href = attr_map.get("href", "")
            hreflang = attr_map.get("hreflang", "")
            for rel_token in attr_map.get("rel", "").replace(",", " ").split():
                self.links.append((rel_token, hreflang, href))


def _local_asset_from_url(value: str) -> Path | None:
    value = value.strip()
    if value.startswith(PUBLIC_BASE_URL):
        return Path(value.removeprefix(PUBLIC_BASE_URL))
    if value.startswith(("http://", "https://", "//")):
        return None
    return Path(value.lstrip("/"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=DEFAULT_DIST_DIR)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_DIR)
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()

    issues = check_demo_site(
        dist_dir=args.dist,
        corpus_dir=args.corpus,
        serve=args.serve,
    )
    if issues:
        print("Static demo site check failed:")
        for issue in issues:
            print(f"- {issue.code}: {issue.message}")
        raise SystemExit(1)
    print(f"Static demo site check passed: {args.dist}")


if __name__ == "__main__":
    main()
