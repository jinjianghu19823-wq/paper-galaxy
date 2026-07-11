"""Build the static Paper Galaxy public demo site."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import stat
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from paper_galaxy import __version__
from paper_galaxy.explain.pairs import explain_pair, pair_explanation_payload
from paper_galaxy.indexer import index_corpus
from paper_galaxy.storage.repository import Repository
from paper_galaxy.storage.sqlite import connect_read_only, resolve_database_path
from paper_galaxy.web.map_builder import build_map_payload

if __package__:
    from scripts.check_demo_site import check_demo_site
else:
    from check_demo_site import check_demo_site

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SITE_DIR = REPO_ROOT / "site"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "site_dist"
DEFAULT_CORPUS_DIR = REPO_ROOT / "examples" / "tiny_corpus"
DEMO_NAMESPACE = "paper-galaxy-static-demo:v1"
PUBLIC_FLOAT_DIGITS = 8
BUILD_MARKER_NAME = ".paper-galaxy-demo-build.json"
BUILD_MARKER_FORMAT = "paper-galaxy-demo-build"
BUILD_MARKER_VERSION = 1
PUBLISH_JOURNAL_FORMAT = "paper-galaxy-demo-publish"
PUBLISH_JOURNAL_VERSION = 1

DEMO_METADATA = {
    "demo": "paper-galaxy-static-demo",
    "paper_galaxy_version": __version__,
    "synthetic_only": True,
    "zotero_demo": "synthetic-feature-description-only",
    "contains_real_zotero_data": False,
    "source_corpus": "examples/tiny_corpus",
    "generated_at": "deterministic-static-demo",
    "numeric_precision_digits": PUBLIC_FLOAT_DIGITS,
    "languages": ["en", "zh-CN"],
    "notes": [
        "Generated from the synthetic tiny corpus only.",
        "No source document full text, absolute paths, or SQLite paths are included.",
    ],
}


class DemoBuildSafetyError(ValueError):
    """Raised before an unsafe output path can be modified."""


def build_demo_site(
    *,
    site_dir: Path = DEFAULT_SITE_DIR,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    corpus_dir: Path = DEFAULT_CORPUS_DIR,
    refresh_source_data: bool = False,
) -> Path:
    """Build, validate, and safely publish a build-owned demo directory."""

    site_dir = _resolve_source_directory(site_dir, label="site source")
    corpus_dir = _resolve_source_directory(corpus_dir, label="demo corpus")
    if not site_dir.exists():
        raise FileNotFoundError(f"Site source directory does not exist: {site_dir}")
    if not corpus_dir.exists():
        raise FileNotFoundError(f"Demo corpus does not exist: {corpus_dir}")
    if not site_dir.is_dir():
        raise NotADirectoryError(f"Site source is not a directory: {site_dir}")
    if not corpus_dir.is_dir():
        raise NotADirectoryError(f"Demo corpus is not a directory: {corpus_dir}")
    _reject_source_symlinks(site_dir, label="site source")
    _reject_source_symlinks(corpus_dir, label="demo corpus")

    output_dir = _resolve_safe_output_target(
        output_dir,
        site_dir=site_dir,
        corpus_dir=corpus_dir,
    )
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink_components(output_dir)
    _recover_interrupted_publish(output_dir)
    _cleanup_orphan_staging_directories(output_dir)
    old_output = _classify_existing_output(output_dir)

    staging_dir = Path(
        tempfile.mkdtemp(
            prefix=f".{output_dir.name}.paper-galaxy-demo-staging-",
            dir=output_dir.parent,
        )
    )
    _write_build_marker(staging_dir, state="staging")
    try:
        shutil.copytree(site_dir, staging_dir, dirs_exist_ok=True)
        payload = build_demo_payload(corpus_dir=corpus_dir)
        serialized = serialize_demo_payload(payload)
        output_data = staging_dir / "data" / "tiny-map.json"
        output_data.parent.mkdir(parents=True, exist_ok=True)
        output_data.write_bytes(serialized)
        (staging_dir / ".nojekyll").write_bytes(b"")
        _write_build_marker(staging_dir, state="complete")
        issues = check_demo_site(dist_dir=staging_dir, corpus_dir=corpus_dir)
        if issues:
            details = "; ".join(
                f"{issue.code}: {issue.message}" for issue in issues[:5]
            )
            raise RuntimeError(f"Demo staging validation failed: {details}")
        _publish_staged_site(
            staging_dir=staging_dir,
            output_dir=output_dir,
            old_output=old_output,
        )
    finally:
        if _lexists(staging_dir):
            _remove_staging_directory(staging_dir)

    if refresh_source_data:
        source_data = site_dir / "data" / "tiny-map.json"
        source_data.parent.mkdir(parents=True, exist_ok=True)
        if _is_link_like(source_data):
            raise _safety_error("the committed demo fixture is a symlink")
        _atomic_write_bytes(source_data, serialized)

    return output_dir


def _resolve_safe_output_target(
    output_dir: Path, *, site_dir: Path, corpus_dir: Path
) -> Path:
    lexical = Path(os.path.abspath(os.fspath(output_dir.expanduser())))
    lexical = _normalize_macos_tmp_alias(lexical)
    _reject_symlink_components(lexical)
    resolved = lexical.resolve(strict=False)
    root = Path(resolved.anchor)
    home = Path.home().resolve(strict=False)
    repository = REPO_ROOT.resolve(strict=False)
    git_metadata = (repository / ".git").resolve(strict=False)

    if resolved == root or os.path.ismount(resolved):
        raise _safety_error("the output is a filesystem or mounted-volume root")
    if _same_existing_path(resolved, home):
        raise _safety_error("the output is the user home directory")
    if _same_existing_path(resolved, repository):
        raise _safety_error("the output is the repository or project root")
    if _paths_overlap_or_samefile(resolved, git_metadata):
        raise _safety_error("the output overlaps Git metadata")
    if _paths_overlap_or_samefile(resolved, site_dir):
        raise _safety_error("the output overlaps the tracked site source")
    if _paths_overlap_or_samefile(resolved, corpus_dir):
        raise _safety_error("the output overlaps the source corpus")
    return resolved


def _resolve_source_directory(path: Path, *, label: str) -> Path:
    lexical = Path(os.path.abspath(os.fspath(path.expanduser())))
    lexical = _normalize_macos_tmp_alias(lexical)
    _reject_symlink_components(lexical, label=label)
    return lexical.resolve(strict=False)


def _normalize_macos_tmp_alias(path: Path) -> Path:
    """Accept macOS's root-owned ``/tmp`` alias without following user links."""

    if path.anchor != "/" or len(path.parts) < 2 or path.parts[1] != "tmp":
        return path
    system_tmp = Path("/tmp")
    private_tmp = Path("/private/tmp")
    try:
        owner = system_tmp.lstat().st_uid
        resolved_tmp = system_tmp.resolve(strict=True)
    except OSError:
        return path
    if system_tmp.is_symlink() and owner == 0 and resolved_tmp == private_tmp:
        return private_tmp.joinpath(*path.parts[2:])
    return path


def _reject_symlink_components(path: Path, *, label: str = "output path") -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if not _lexists(current):
            continue
        if _is_link_like(current):
            raise _safety_error(f"the {label} contains a symlink: {current}")


def _is_link_like(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = int(getattr(path.lstat(), "st_file_attributes", 0))
    except OSError:
        return False
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    if reparse_flag and attributes & reparse_flag:
        return True
    return bool(getattr(os.path, "isjunction", lambda _path: False)(path))


def _reject_source_symlinks(root: Path, *, label: str) -> None:
    if _is_link_like(root):
        raise _safety_error(f"the {label} is a symlink")
    for path in root.rglob("*"):
        if _is_link_like(path):
            raise _safety_error(f"the {label} contains a symlink: {path}")


def _classify_existing_output(output_dir: Path) -> str:
    if not _lexists(output_dir):
        return "missing"
    if _is_link_like(output_dir) or not output_dir.is_dir():
        raise _safety_error("the output exists but is not a regular directory")
    if next(output_dir.iterdir(), None) is None:
        return "empty"
    _read_build_marker(output_dir, allowed_states={"complete"})
    return "owned"


def _write_build_marker(directory: Path, *, state: str) -> None:
    payload = {
        "format": BUILD_MARKER_FORMAT,
        "state": state,
        "version": BUILD_MARKER_VERSION,
    }
    (directory / BUILD_MARKER_NAME).write_bytes(_stable_json_bytes(payload))


def _read_build_marker(
    directory: Path, *, allowed_states: set[str]
) -> dict[str, object]:
    marker = directory / BUILD_MARKER_NAME
    if _is_link_like(marker):
        raise _safety_error("the build ownership marker is a symlink")
    if not marker.is_file():
        raise _safety_error("the non-empty output has no build ownership marker")
    try:
        payload = json.loads(marker.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _safety_error("the build ownership marker is malformed") from exc
    if not isinstance(payload, dict):
        raise _safety_error("the build ownership marker is not an object")
    if payload.get("format") != BUILD_MARKER_FORMAT:
        raise _safety_error("the build ownership marker format is unsupported")
    version = payload.get("version")
    if type(version) is not int or version != BUILD_MARKER_VERSION:
        raise _safety_error("the build ownership marker version is unsupported")
    if payload.get("state") not in allowed_states:
        raise _safety_error("the build ownership marker state is incomplete")
    return payload


def _publish_staged_site(
    *, staging_dir: Path, output_dir: Path, old_output: str
) -> None:
    backup = _backup_path(output_dir)
    journal = _journal_path(output_dir)
    if _lexists(backup) or _lexists(journal):
        raise _safety_error("an unrecovered demo publish state already exists")
    journal_payload = {
        "format": PUBLISH_JOURNAL_FORMAT,
        "old_output": old_output,
        "output_name": output_dir.name,
        "staging_name": staging_dir.name,
        "version": PUBLISH_JOURNAL_VERSION,
    }
    _atomic_write_bytes(journal, _stable_json_bytes(journal_payload))
    moved_old = False
    try:
        if old_output != "missing":
            os.replace(output_dir, backup)
            moved_old = True
            _validate_previous_output(backup, old_output)
        os.replace(staging_dir, output_dir)
    except BaseException:
        rollback_succeeded = True
        if moved_old:
            try:
                if _lexists(output_dir):
                    _remove_owned_output(output_dir, "owned")
                os.replace(backup, output_dir)
            except BaseException:
                rollback_succeeded = False
        if rollback_succeeded:
            journal.unlink(missing_ok=True)
        raise

    if _lexists(backup):
        try:
            _remove_owned_output(backup, old_output)
        except OSError:
            return
    try:
        journal.unlink(missing_ok=True)
    except OSError:
        return


def _recover_interrupted_publish(output_dir: Path) -> None:
    journal = _journal_path(output_dir)
    backup = _backup_path(output_dir)
    if not _lexists(journal):
        if _lexists(backup):
            raise _safety_error("an unjournaled demo backup exists")
        return
    payload = _read_publish_journal(journal, output_dir=output_dir)
    old_output = str(payload["old_output"])
    staging_dir = output_dir.parent / str(payload["staging_name"])
    output_exists = _lexists(output_dir)
    backup_exists = _lexists(backup)

    if backup_exists and output_exists:
        _read_build_marker(output_dir, allowed_states={"complete"})
        _remove_owned_output(backup, old_output)
    elif backup_exists:
        _validate_previous_output(backup, old_output)
        os.replace(backup, output_dir)
    elif output_exists:
        if old_output == "empty" and next(output_dir.iterdir(), None) is None:
            pass
        else:
            _read_build_marker(output_dir, allowed_states={"complete"})
    elif old_output != "missing":
        raise _safety_error("the interrupted publish lost its previous output")

    if _lexists(staging_dir):
        _remove_staging_directory(staging_dir)
    journal.unlink(missing_ok=True)


def _read_publish_journal(journal: Path, *, output_dir: Path) -> dict[str, object]:
    if _is_link_like(journal) or not journal.is_file():
        raise _safety_error("the publish journal is not a regular file")
    try:
        payload = json.loads(journal.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _safety_error("the publish journal is malformed") from exc
    if not isinstance(payload, dict):
        raise _safety_error("the publish journal is not an object")
    if payload.get("format") != PUBLISH_JOURNAL_FORMAT:
        raise _safety_error("the publish journal format is unsupported")
    version = payload.get("version")
    if type(version) is not int or version != PUBLISH_JOURNAL_VERSION:
        raise _safety_error("the publish journal version is unsupported")
    if payload.get("output_name") != output_dir.name:
        raise _safety_error("the publish journal names another output")
    if payload.get("old_output") not in {"missing", "empty", "owned"}:
        raise _safety_error("the publish journal has an invalid previous state")
    staging_name = payload.get("staging_name")
    prefix = f".{output_dir.name}.paper-galaxy-demo-staging-"
    if (
        not isinstance(staging_name, str)
        or Path(staging_name).name != staging_name
        or not staging_name.startswith(prefix)
    ):
        raise _safety_error("the publish journal has an invalid staging name")
    return payload


def _cleanup_orphan_staging_directories(output_dir: Path) -> None:
    prefix = f".{output_dir.name}.paper-galaxy-demo-staging-"
    for candidate in output_dir.parent.glob(f"{prefix}*"):
        if _is_link_like(candidate) or not candidate.is_dir():
            continue
        try:
            _read_build_marker(
                candidate,
                allowed_states={"staging", "complete"},
            )
        except DemoBuildSafetyError:
            continue
        shutil.rmtree(candidate)


def _remove_staging_directory(staging_dir: Path) -> None:
    if _is_link_like(staging_dir) or not staging_dir.is_dir():
        raise _safety_error("the staging directory changed type during build")
    _read_build_marker(staging_dir, allowed_states={"staging", "complete"})
    shutil.rmtree(staging_dir)


def _validate_previous_output(path: Path, kind: str) -> None:
    if _is_link_like(path) or not path.is_dir():
        raise _safety_error("the previous output backup changed type")
    if kind == "empty":
        if next(path.iterdir(), None) is not None:
            raise _safety_error("the previous empty output backup is not empty")
        return
    if kind == "owned":
        _read_build_marker(path, allowed_states={"complete"})
        return
    if kind != "missing":
        raise _safety_error("the previous output state is invalid")


def _remove_owned_output(path: Path, kind: str) -> None:
    _validate_previous_output(path, kind)
    if kind == "empty":
        path.rmdir()
    elif kind == "owned":
        shutil.rmtree(path)


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.tmp-",
        dir=path.parent,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _stable_json_bytes(payload: dict[str, object]) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _backup_path(output_dir: Path) -> Path:
    return output_dir.parent / f".{output_dir.name}.paper-galaxy-demo-backup"


def _journal_path(output_dir: Path) -> Path:
    return output_dir.parent / f".{output_dir.name}.paper-galaxy-demo-publish.json"


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _same_existing_path(first: Path, second: Path) -> bool:
    if first == second:
        return True
    try:
        return first.samefile(second)
    except OSError:
        return False


def _paths_overlap_or_samefile(first: Path, second: Path) -> bool:
    if _paths_overlap(first, second):
        return True
    if any(
        _same_existing_path(ancestor, second) for ancestor in (first, *first.parents)
    ):
        return True
    return any(
        _same_existing_path(ancestor, first) for ancestor in (second, *second.parents)
    )


def _safety_error(reason: str) -> DemoBuildSafetyError:
    example = REPO_ROOT.resolve(strict=False) / "site_dist"
    return DemoBuildSafetyError(
        f"Demo build safety check failed: {reason}. "
        f"Choose a dedicated build output such as {example}."
    )


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
    return _normalize_public_numbers(payload)


def serialize_demo_payload(payload: dict[str, Any]) -> bytes:
    """Serialize a public artifact with stable UTF-8 and JSON settings."""

    normalized = _normalize_public_numbers(payload)
    serialized = json.dumps(
        normalized,
        allow_nan=False,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return (serialized + "\n").encode("utf-8")


def _pair_explanations(
    *, project_dir: Path, payload: dict[str, Any]
) -> list[dict[str, Any]]:
    pairs = _nearest_pairs(payload, limit=3)
    if not pairs:
        return []

    connection = connect_read_only(project_dir)
    try:
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
    relative_path_by_id: dict[str, str] = {}
    for document in _dict_list(payload.get("documents")):
        document_id = str(document.get("document_id") or document.get("id") or "")
        relative_path = str(document.get("relative_path", ""))
        if document_id and relative_path:
            relative_path_by_id[document_id] = relative_path

    candidates: dict[tuple[str, str], tuple[float, str, str]] = {}
    for point in _dict_list(payload.get("points")):
        source_id = str(point.get("document_id", ""))
        source_path = relative_path_by_id.get(source_id)
        for neighbor in _dict_list(point.get("nearest_neighbors")):
            target_id = str(neighbor.get("document_id", ""))
            target_path = relative_path_by_id.get(target_id)
            if not source_id or not target_id or not source_path or not target_path:
                continue
            if source_path <= target_path:
                key = (source_path, target_path)
                oriented_ids = (source_id, target_id)
            else:
                key = (target_path, source_path)
                oriented_ids = (target_id, source_id)
            score = _numeric_score(neighbor.get("score"))
            existing = candidates.get(key)
            if existing is None or score > existing[0]:
                candidates[key] = (score, *oriented_ids)
    ranked = sorted(
        (
            (score, source_path, target_path, source_id, target_id)
            for (source_path, target_path), (
                score,
                source_id,
                target_id,
            ) in candidates.items()
        ),
        key=lambda item: (-item[0], item[1], item[2]),
    )
    return [(source_id, target_id) for _, _, _, source_id, target_id in ranked[:limit]]


def _canonicalize_demo_payload(
    payload: dict[str, Any],
    *,
    explanations: list[dict[str, Any]],
) -> dict[str, Any]:
    documents, id_map, relative_path_by_id = _canonicalize_demo_documents(
        payload.get("documents")
    )
    clusters, signature_map, cluster_id_by_signature = _canonicalize_demo_clusters(
        payload.get("clusters"),
        id_map=id_map,
        relative_path_by_id=relative_path_by_id,
    )
    points = _canonicalize_demo_points(
        payload.get("points"),
        id_map=id_map,
        relative_path_by_id=relative_path_by_id,
        signature_map=signature_map,
        cluster_id_by_signature=cluster_id_by_signature,
    )
    canonical = dict(payload)
    canonical["documents"] = documents
    canonical["clusters"] = clusters
    canonical["points"] = points
    canonical["cluster_labels"] = {
        str(cluster["cluster_id"]): str(cluster.get("display_label", ""))
        for cluster in clusters
    }
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
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, int]]:
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
    clusters.sort(key=lambda cluster: str(cluster["cluster_signature"]))
    cluster_id_by_signature: dict[str, int] = {}
    for cluster_id, cluster in enumerate(clusters):
        cluster["cluster_id"] = cluster_id
        cluster_id_by_signature[str(cluster["cluster_signature"])] = cluster_id
    return clusters, signature_map, cluster_id_by_signature


def _canonicalize_demo_points(
    value: object,
    *,
    id_map: dict[str, str],
    relative_path_by_id: dict[str, str],
    signature_map: dict[str, str],
    cluster_id_by_signature: dict[str, int],
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
        point["cluster_id"] = cluster_id_by_signature[cluster_signature]
        point["top_terms"] = _sorted_term_scores(point.get("top_terms"))
        point["nearest_neighbors"] = neighbors
        points.append(point)
    points.sort(key=lambda point: relative_path_by_id[str(point["document_id"])])
    _orient_demo_coordinate_axes(points, relative_path_by_id=relative_path_by_id)
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
        score = float(value)
    except (OverflowError, ValueError):
        return 0.0
    if not math.isfinite(score):
        raise ValueError("Demo payload scores must be finite.")
    return score


def _orient_demo_coordinate_axes(
    points: list[dict[str, Any]], *, relative_path_by_id: dict[str, str]
) -> None:
    """Make global SVD axis sign choices independent of backend conventions."""

    for axis in ("x", "y"):
        ranked = sorted(
            points,
            key=lambda point: (
                -abs(_numeric_score(point.get(axis))),
                relative_path_by_id[str(point["document_id"])],
            ),
        )
        if not ranked or _numeric_score(ranked[0].get(axis)) >= 0:
            continue
        for point in points:
            point[axis] = -_numeric_score(point.get(axis))


def _normalize_public_float(value: float) -> float:
    """Normalize one public float without reducing internal calculation precision."""

    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError("Demo public artifacts require finite numeric values.")
    rounded = round(numeric, PUBLIC_FLOAT_DIGITS)
    return 0.0 if rounded == 0.0 else rounded


def _normalize_public_numbers(value: Any) -> Any:
    if isinstance(value, float):
        return _normalize_public_float(value)
    if isinstance(value, dict):
        return {key: _normalize_public_numbers(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [_normalize_public_numbers(nested) for nested in value]
    if isinstance(value, tuple):
        return [_normalize_public_numbers(nested) for nested in value]
    return value


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
