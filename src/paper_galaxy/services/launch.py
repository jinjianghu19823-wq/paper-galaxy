"""Preparation for the one-command local workstation launch flow."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from paper_galaxy.paths import project_config_path
from paper_galaxy.projects import open_or_initialize_project
from paper_galaxy.services.jobs import enqueue_job
from paper_galaxy.services.sources import (
    SOURCE_KIND_ZOTERO,
    SourceRecord,
    list_sources,
    preflight_corpus_source,
    register_corpus_source,
    validate_source_locator_for_use,
)
from paper_galaxy.storage.sqlite import ensure_database_ready, resolve_database_path


@dataclass(frozen=True)
class LaunchPreparation:
    """Durable state prepared before starting the loopback Web server."""

    project_dir: Path
    project_created: bool
    source_ids: tuple[str, ...]
    job_ids: tuple[str, ...]


def prepare_launch(
    *,
    project_dir: Path | str,
    corpus_dirs: Sequence[Path | str] = (),
    zotero_sync: bool = False,
    queue_analysis: bool = False,
    analysis_seed: int = 42,
    analysis_neighbors: int = 5,
    analysis_limit: int = 1000,
) -> LaunchPreparation:
    """Initialize a project and enqueue work only for newly registered inputs.

    Corpus roots are registered idempotently and are never copied or modified.
    A repeated launch returns the same source ids without adding another index
    job.  ``zotero_sync`` queues the already registered read-only profiles; the
    profile discovery/registration step remains explicit so launch cannot read
    an arbitrary browser-provided path.
    """

    lexical_project = Path(project_dir).expanduser().absolute()
    # Validate every source before creating project metadata. This all-or-none
    # preflight prevents a later unsafe corpus from leaving earlier writes.
    preflighted_corpora = tuple(
        preflight_corpus_source(lexical_project, corpus_dir)
        for corpus_dir in corpus_dirs
    )
    preflighted_zotero = (
        _preflight_existing_zotero_profiles(lexical_project) if zotero_sync else ()
    )

    opened = open_or_initialize_project(
        lexical_project,
        initialize_database=False,
    )
    # Re-run relationship checks against the exact persisted configuration in
    # case an existing project uses a custom database path.
    preflighted_corpora = tuple(
        preflight_corpus_source(opened.project_dir, corpus_dir)
        for corpus_dir in preflighted_corpora
    )
    ensure_database_ready(opened.project_dir)
    source_ids: list[str] = []
    seen_source_ids: set[str] = set()
    job_ids: list[str] = []

    for corpus_dir in preflighted_corpora:
        source, created = register_corpus_source(opened.project_dir, corpus_dir)
        if source.id not in seen_source_ids:
            source_ids.append(source.id)
            seen_source_ids.add(source.id)
        if not created and source.last_success_at is not None:
            continue
        job, queued = enqueue_job(
            opened.project_dir,
            kind="index_corpus",
            source_id=source.id,
            params={
                "rebuild_analysis": queue_analysis,
                "analysis_seed": analysis_seed,
                "analysis_neighbors": analysis_neighbors,
                "analysis_limit": analysis_limit,
            },
        )
        if queued:
            job_ids.append(job.id)

    if zotero_sync:
        # Use the profiles that passed read-only preflight before any mutation,
        # then revalidate their locators immediately before queuing work.
        for profile in preflighted_zotero:
            profile = validate_source_locator_for_use(opened.project_dir, profile)
            if profile.id not in seen_source_ids:
                source_ids.append(profile.id)
                seen_source_ids.add(profile.id)
            job, queued = enqueue_job(
                opened.project_dir,
                kind="zotero_sync",
                source_id=profile.id,
                params={
                    "rebuild_analysis": queue_analysis,
                    "analysis_seed": analysis_seed,
                    "analysis_neighbors": analysis_neighbors,
                    "analysis_limit": analysis_limit,
                },
            )
            if queued:
                job_ids.append(job.id)

    return LaunchPreparation(
        project_dir=opened.project_dir,
        project_created=opened.created,
        source_ids=tuple(source_ids),
        job_ids=tuple(job_ids),
    )


def _preflight_existing_zotero_profiles(
    project_dir: Path,
) -> tuple[SourceRecord, ...]:
    """Load and validate registered Zotero profiles without creating state."""

    if project_dir.is_symlink():
        raise ValueError("Project directory must not be a symbolic link.")
    resolved_project = project_dir.resolve(strict=False)
    if not project_config_path(resolved_project).is_file():
        raise ValueError(
            "No existing project is available for Zotero sync; initialize and "
            "register a read-only Zotero profile first."
        )
    database = resolve_database_path(resolved_project)
    if not database.is_file():
        raise ValueError(
            "The existing project database is missing; open the project before "
            "requesting Zotero sync."
        )
    profiles = list_sources(
        resolved_project,
        kind=SOURCE_KIND_ZOTERO,
        limit=100,
    )
    if not profiles:
        raise ValueError(
            "No read-only Zotero profile is registered; register one before "
            "requesting launch-time sync."
        )
    return tuple(
        validate_source_locator_for_use(resolved_project, profile)
        for profile in profiles
    )
