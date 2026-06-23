"""Build the static Paper Galaxy public demo site."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from paper_galaxy import __version__
from paper_galaxy.explain.pairs import explain_pair, pair_explanation_payload
from paper_galaxy.indexer import index_corpus
from paper_galaxy.storage.migrations import initialize_database
from paper_galaxy.storage.repository import Repository
from paper_galaxy.storage.sqlite import connect_database, resolve_database_path
from paper_galaxy.web.map_builder import build_map_payload

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SITE_DIR = REPO_ROOT / "site"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "site_dist"
DEFAULT_CORPUS_DIR = REPO_ROOT / "examples" / "tiny_corpus"

DEMO_METADATA = {
    "demo": "paper-galaxy-static-demo",
    "paper_galaxy_version": __version__,
    "synthetic_only": True,
    "source_corpus": "examples/tiny_corpus",
    "generated_at": "deterministic-static-demo",
    "languages": ["en", "zh-CN"],
    "notes": [
        "Generated from the synthetic tiny corpus only.",
        "No source document full text, absolute paths, or SQLite paths are included.",
    ],
}


def build_demo_site(
    *,
    site_dir: Path = DEFAULT_SITE_DIR,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    corpus_dir: Path = DEFAULT_CORPUS_DIR,
) -> Path:
    """Refresh demo data and copy the static site to ``output_dir``."""

    site_dir = site_dir.resolve()
    output_dir = output_dir.resolve()
    corpus_dir = corpus_dir.resolve()
    if not site_dir.exists():
        raise FileNotFoundError(f"Site source directory does not exist: {site_dir}")
    if not corpus_dir.exists():
        raise FileNotFoundError(f"Demo corpus does not exist: {corpus_dir}")

    data_dir = site_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    payload = build_demo_payload(corpus_dir=corpus_dir)
    (data_dir / "tiny-map.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    if output_dir.exists():
        shutil.rmtree(output_dir)
    shutil.copytree(site_dir, output_dir)
    (output_dir / ".nojekyll").write_text("", encoding="utf-8")
    return output_dir


def build_demo_payload(*, corpus_dir: Path = DEFAULT_CORPUS_DIR) -> dict[str, Any]:
    """Generate a safe static graph payload from the synthetic tiny corpus."""

    corpus_dir = corpus_dir.resolve()
    with tempfile.TemporaryDirectory(prefix="paper-galaxy-demo-") as temp_name:
        project_dir = Path(temp_name)
        index_corpus(corpus_dir, project_dir=project_dir, min_chars=40)
        payload = build_map_payload(
            project_dir=project_dir,
            seed=42,
            neighbors=3,
            limit=50,
        )
        explanations = _pair_explanations(project_dir=project_dir, payload=payload)

    id_map = _stable_id_map(payload)
    payload = _replace_ids(payload, id_map)
    explanations = _replace_ids(explanations, id_map)
    payload["metadata"] = dict(DEMO_METADATA)
    payload["explanations"] = _sanitize_explanations(explanations)
    payload["stats"] = _sanitize_stats(payload.get("stats"))
    payload["documents"] = _sanitize_documents(payload.get("documents"))
    return payload


def _pair_explanations(
    *, project_dir: Path, payload: dict[str, Any]
) -> list[dict[str, Any]]:
    pairs = _nearest_pairs(payload, limit=3)
    if not pairs:
        return []

    connection = connect_database(project_dir)
    try:
        initialize_database(connection)
        repository = Repository(connection, resolve_database_path(project_dir))
        explanations = []
        for source_id, target_id in pairs:
            explanation = explain_pair(
                repository,
                source_id,
                target_id,
                term_limit=5,
                chunk_limit=2,
            )
            explanations.append(pair_explanation_payload(explanation))
        return explanations
    finally:
        connection.close()


def _nearest_pairs(payload: dict[str, Any], *, limit: int) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for point in _dict_list(payload.get("points")):
        source_id = str(point.get("document_id", ""))
        for neighbor in _dict_list(point.get("nearest_neighbors")):
            target_id = str(neighbor.get("document_id", ""))
            if not source_id or not target_id:
                continue
            key = tuple(sorted((source_id, target_id)))
            if key in seen:
                continue
            seen.add(key)
            pairs.append((source_id, target_id))
            if len(pairs) >= limit:
                return pairs
    return pairs


def _stable_id_map(payload: dict[str, Any]) -> dict[str, str]:
    id_map: dict[str, str] = {}
    for document in _dict_list(payload.get("documents")):
        old_id = str(document.get("document_id") or document.get("id") or "")
        relative_path = str(document.get("relative_path", ""))
        if old_id and relative_path:
            digest = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()
            id_map[old_id] = f"demo_doc_{digest[:12]}"
    return id_map


def _replace_ids(value: Any, id_map: dict[str, str]) -> Any:
    if isinstance(value, str):
        return id_map.get(value, value)
    if isinstance(value, list):
        return [_replace_ids(item, id_map) for item in value]
    if isinstance(value, dict):
        return {key: _replace_ids(item, id_map) for key, item in value.items()}
    return value


def _sanitize_documents(value: object) -> list[dict[str, Any]]:
    documents = _dict_list(value)
    for document in documents:
        if "document_id" in document:
            document["id"] = document["document_id"]
        if "id" in document:
            document["document_id"] = document["id"]
        document["updated_at"] = "synthetic-demo"
    return documents


def _sanitize_stats(value: object) -> dict[str, Any]:
    stats = dict(value) if isinstance(value, dict) else {}
    if stats:
        stats["database_path"] = "omitted-for-static-demo"
        stats["last_scan_time"] = "synthetic-demo"
    return stats


def _sanitize_explanations(value: object) -> list[dict[str, Any]]:
    explanations = _dict_list(value)
    for explanation in explanations:
        for match in _dict_list(explanation.get("chunk_matches")):
            for key in ("source_excerpt", "target_excerpt"):
                match[key] = _clip_excerpt(str(match.get(key, "")))
    return explanations


def _clip_excerpt(value: str, *, limit: int = 160) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _dict_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, default=DEFAULT_SITE_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_DIR)
    args = parser.parse_args()

    output_dir = build_demo_site(
        site_dir=args.site,
        output_dir=args.out,
        corpus_dir=args.corpus,
    )
    print(f"Built demo site at {output_dir}")


if __name__ == "__main__":
    main()
