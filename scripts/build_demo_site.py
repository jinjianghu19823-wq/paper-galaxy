"""Build the static Paper Galaxy public demo site."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path, PurePosixPath
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
DEMO_NAMESPACE = "paper-galaxy-static-demo:v1"

DEMO_METADATA = {
    "demo": "paper-galaxy-static-demo",
    "paper_galaxy_version": __version__,
    "synthetic_only": True,
    "zotero_demo": "synthetic-feature-description-only",
    "contains_real_zotero_data": False,
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
    refresh_source_data: bool = False,
) -> Path:
    """Build the demo in ``output_dir`` without changing source by default."""

    site_dir = site_dir.resolve()
    output_dir = output_dir.resolve()
    corpus_dir = corpus_dir.resolve()
    if not site_dir.exists():
        raise FileNotFoundError(f"Site source directory does not exist: {site_dir}")
    if not corpus_dir.exists():
        raise FileNotFoundError(f"Demo corpus does not exist: {corpus_dir}")
    if _paths_overlap(output_dir, site_dir) or _paths_overlap(output_dir, corpus_dir):
        raise ValueError("Demo output must not overlap the site source or corpus.")

    if output_dir.exists():
        shutil.rmtree(output_dir)
    shutil.copytree(site_dir, output_dir)

    payload = build_demo_payload(corpus_dir=corpus_dir)
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    output_data = output_dir / "data" / "tiny-map.json"
    output_data.parent.mkdir(parents=True, exist_ok=True)
    output_data.write_text(serialized, encoding="utf-8")
    (output_dir / ".nojekyll").write_text("", encoding="utf-8")

    if refresh_source_data:
        source_data = site_dir / "data" / "tiny-map.json"
        source_data.parent.mkdir(parents=True, exist_ok=True)
        source_data.write_text(serialized, encoding="utf-8")

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

    payload = _canonicalize_demo_payload(
        payload,
        explanations=_sanitize_explanations(explanations),
    )
    payload["metadata"] = dict(DEMO_METADATA)
    payload["stats"] = _sanitize_stats(payload.get("stats"))
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


def _canonicalize_demo_payload(
    payload: dict[str, Any],
    *,
    explanations: list[dict[str, Any]],
) -> dict[str, Any]:
    documents, id_map, relative_path_by_id = _canonicalize_demo_documents(
        payload.get("documents")
    )
    clusters, signature_map = _canonicalize_demo_clusters(
        payload.get("clusters"),
        id_map=id_map,
        relative_path_by_id=relative_path_by_id,
    )
    points = _canonicalize_demo_points(
        payload.get("points"),
        id_map=id_map,
        relative_path_by_id=relative_path_by_id,
        signature_map=signature_map,
    )
    canonical = dict(payload)
    canonical["documents"] = documents
    canonical["clusters"] = clusters
    canonical["points"] = points
    canonical["explanations"] = _canonicalize_demo_explanations(
        explanations,
        id_map=id_map,
        relative_path_by_id=relative_path_by_id,
    )
    return canonical


def _canonicalize_demo_documents(
    value: object,
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, str]]:
    documents: list[dict[str, Any]] = []
    id_map: dict[str, str] = {}
    relative_path_by_id: dict[str, str] = {}
    for raw_document in _dict_list(value):
        document = dict(raw_document)
        relative_path = _normalize_demo_relative_path(
            str(document.get("relative_path", ""))
        )
        old_ids = {
            str(candidate)
            for candidate in (document.get("document_id"), document.get("id"))
            if candidate
        }
        if not old_ids:
            raise ValueError(f"Demo document is missing an id: {relative_path}")
        demo_id = _stable_demo_document_id(relative_path)
        existing_path = relative_path_by_id.get(demo_id)
        if existing_path is not None and existing_path != relative_path:
            raise ValueError(f"Demo document id collision: {relative_path}")
        relative_path_by_id[demo_id] = relative_path
        for old_id in old_ids:
            existing_id = id_map.get(old_id)
            if existing_id is not None and existing_id != demo_id:
                raise ValueError(f"Indexed document id collision: {old_id}")
            id_map[old_id] = demo_id
        document["document_id"] = demo_id
        document["id"] = demo_id
        document["relative_path"] = relative_path
        document["updated_at"] = "synthetic-demo"
        documents.append(document)
    documents.sort(key=lambda document: str(document["relative_path"]))
    return documents, id_map, relative_path_by_id


def _canonicalize_demo_clusters(
    value: object,
    *,
    id_map: dict[str, str],
    relative_path_by_id: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    clusters: list[dict[str, Any]] = []
    signature_map: dict[str, str] = {}
    for raw_cluster in _dict_list(value):
        cluster = dict(raw_cluster)
        member_ids = [
            _mapped_document_id(str(document_id), id_map)
            for document_id in _list_value(cluster.get("document_ids"))
        ]
        member_ids.sort(key=relative_path_by_id.__getitem__)
        member_paths = [relative_path_by_id[document_id] for document_id in member_ids]
        demo_signature = _stable_demo_cluster_signature(member_paths)
        old_signature = str(cluster.get("cluster_signature", ""))
        if not old_signature:
            raise ValueError("Demo cluster is missing a cluster signature.")
        existing_signature = signature_map.get(old_signature)
        if existing_signature is not None and existing_signature != demo_signature:
            raise ValueError(f"Indexed cluster signature collision: {old_signature}")
        signature_map[old_signature] = demo_signature

        representatives = [
            _canonicalize_document_summary(
                summary,
                id_map=id_map,
                relative_path_by_id=relative_path_by_id,
            )
            for summary in _dict_list(cluster.get("representatives"))
        ]
        representatives.sort(
            key=lambda summary: (
                -_numeric_score(summary.get("score")),
                str(summary["relative_path"]),
            )
        )
        cluster["cluster_signature"] = demo_signature
        cluster["document_ids"] = member_ids
        cluster["representatives"] = representatives
        cluster["top_terms"] = _sorted_term_scores(cluster.get("top_terms"))
        clusters.append(cluster)
    clusters.sort(
        key=lambda cluster: (
            int(cluster.get("cluster_id", 0)),
            str(cluster["cluster_signature"]),
        )
    )
    return clusters, signature_map


def _canonicalize_demo_points(
    value: object,
    *,
    id_map: dict[str, str],
    relative_path_by_id: dict[str, str],
    signature_map: dict[str, str],
) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for raw_point in _dict_list(value):
        point = dict(raw_point)
        document_id = _mapped_document_id(str(point.get("document_id", "")), id_map)
        old_signature = str(point.get("cluster_signature", ""))
        try:
            cluster_signature = signature_map[old_signature]
        except KeyError as exc:
            raise ValueError(
                f"Unknown cluster signature in demo point: {old_signature}"
            ) from exc
        neighbors = [
            _canonicalize_document_summary(
                neighbor,
                id_map=id_map,
                relative_path_by_id=relative_path_by_id,
            )
            for neighbor in _dict_list(point.get("nearest_neighbors"))
        ]
        neighbors.sort(
            key=lambda neighbor: (
                -_numeric_score(neighbor.get("score")),
                str(neighbor["relative_path"]),
            )
        )
        point["document_id"] = document_id
        point["cluster_signature"] = cluster_signature
        point["nearest_neighbors"] = neighbors
        points.append(point)
    points.sort(key=lambda point: relative_path_by_id[str(point["document_id"])])
    return points


def _canonicalize_demo_explanations(
    value: object,
    *,
    id_map: dict[str, str],
    relative_path_by_id: dict[str, str],
) -> list[dict[str, Any]]:
    explanations: list[dict[str, Any]] = []
    for raw_explanation in _dict_list(value):
        explanation = dict(raw_explanation)
        source = _canonicalize_document_summary(
            _dict_value(explanation.get("source"), field="source"),
            id_map=id_map,
            relative_path_by_id=relative_path_by_id,
        )
        target = _canonicalize_document_summary(
            _dict_value(explanation.get("target"), field="target"),
            id_map=id_map,
            relative_path_by_id=relative_path_by_id,
        )
        matches: list[dict[str, Any]] = []
        for raw_match in _dict_list(explanation.get("chunk_matches")):
            match = dict(raw_match)
            source_index = _required_int(match, "source_chunk_index")
            target_index = _required_int(match, "target_chunk_index")
            match["source_chunk_id"] = _stable_demo_chunk_id(
                str(source["relative_path"]), source_index
            )
            match["target_chunk_id"] = _stable_demo_chunk_id(
                str(target["relative_path"]), target_index
            )
            matches.append(match)
        matches.sort(
            key=lambda match: (
                -_numeric_score(match.get("score")),
                int(match["source_chunk_index"]),
                int(match["target_chunk_index"]),
            )
        )
        explanation["source"] = source
        explanation["target"] = target
        explanation["shared_terms"] = _sorted_term_scores(
            explanation.get("shared_terms")
        )
        explanation["chunk_matches"] = matches
        explanations.append(explanation)
    explanations.sort(
        key=lambda explanation: (
            str(explanation["source"]["relative_path"]),
            str(explanation["target"]["relative_path"]),
        )
    )
    return explanations


def _canonicalize_document_summary(
    raw_summary: dict[str, Any],
    *,
    id_map: dict[str, str],
    relative_path_by_id: dict[str, str],
) -> dict[str, Any]:
    summary = dict(raw_summary)
    document_id = _mapped_document_id(str(summary.get("document_id", "")), id_map)
    expected_path = relative_path_by_id[document_id]
    supplied_path = _normalize_demo_relative_path(str(summary.get("relative_path", "")))
    if supplied_path != expected_path:
        raise ValueError(
            f"Demo document reference path mismatch: {supplied_path} != {expected_path}"
        )
    summary["document_id"] = document_id
    summary["relative_path"] = expected_path
    return summary


def _mapped_document_id(old_id: str, id_map: dict[str, str]) -> str:
    try:
        return id_map[old_id]
    except KeyError as exc:
        raise ValueError(f"Unknown document id in demo payload: {old_id}") from exc


def _stable_demo_document_id(relative_path: str) -> str:
    return _stable_demo_identifier("demo_doc", "document", relative_path)


def _stable_demo_chunk_id(relative_path: str, chunk_index: int) -> str:
    return _stable_demo_identifier(
        "demo_chunk",
        "chunk",
        relative_path,
        f"{chunk_index:06d}",
    )


def _stable_demo_cluster_signature(relative_paths: list[str]) -> str:
    return _stable_demo_identifier(
        "demo_cluster",
        "cluster",
        *sorted(relative_paths),
    )


def _stable_demo_identifier(prefix: str, kind: str, *parts: str) -> str:
    stable_input = json.dumps(
        [DEMO_NAMESPACE, kind, *parts],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(stable_input.encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:16]}"


def _normalize_demo_relative_path(value: str) -> str:
    normalized = PurePosixPath(value.replace("\\", "/"))
    if (
        not value
        or normalized.is_absolute()
        or ".." in normalized.parts
        or str(normalized) in {"", "."}
        or (normalized.parts and normalized.parts[0].endswith(":"))
    ):
        raise ValueError(f"Demo path must be corpus-relative: {value}")
    return str(normalized)


def _sorted_term_scores(value: object) -> list[dict[str, Any]]:
    terms = [dict(term) for term in _dict_list(value)]
    terms.sort(
        key=lambda term: (
            -_numeric_score(term.get("score")),
            str(term.get("term", "")),
        )
    )
    return terms


def _numeric_score(value: object) -> float:
    if not isinstance(value, (int, float, str)):
        return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def _required_int(value: dict[str, Any], field: str) -> int:
    if field not in value:
        raise ValueError(f"Demo payload is missing {field}.")
    try:
        return int(value[field])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Demo payload has an invalid {field}.") from exc


def _dict_value(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"Demo payload is missing {field}.")
    return value


def _list_value(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _paths_overlap(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


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
    parser.add_argument(
        "--refresh-source-data",
        action="store_true",
        help="Explicitly refresh site/data/tiny-map.json after a successful build.",
    )
    args = parser.parse_args()

    output_dir = build_demo_site(
        site_dir=args.site,
        output_dir=args.out,
        corpus_dir=args.corpus,
        refresh_source_data=args.refresh_source_data,
    )
    print(f"Built demo site at {output_dir}")


if __name__ == "__main__":
    main()
