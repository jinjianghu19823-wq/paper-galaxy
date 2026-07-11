from __future__ import annotations

import os
import shutil
import sqlite3
import threading
from pathlib import Path

import pytest

import paper_galaxy.projects as project_lifecycle
import paper_galaxy.services.jobs as job_service
import paper_galaxy.services.launch as launch_service
from paper_galaxy.errors import DatabaseLockedError
from paper_galaxy.projects import open_or_initialize_project
from paper_galaxy.services.jobs import (
    JobCancelled,
    JobContext,
    JobManager,
    enqueue_job,
    get_job,
    list_jobs,
    public_job_payload,
    recover_interrupted_jobs,
    request_job_cancel,
)
from paper_galaxy.services.launch import prepare_launch
from paper_galaxy.services.sources import (
    SOURCE_KIND_CORPUS,
    list_sources,
    public_source_payload,
    register_corpus_source,
    remove_source,
)
from paper_galaxy.storage.migrations import CURRENT_SCHEMA_VERSION
from paper_galaxy.storage.sqlite import (
    connect_read_write,
    ensure_database_ready,
    resolve_database_path,
)


def _corpus(root: Path, name: str = "corpus") -> tuple[Path, Path]:
    corpus = root / name
    corpus.mkdir(parents=True)
    source = corpus / "paper.md"
    source.write_text(
        "# Synthetic source\n\nLocal-only evidence for a durable indexing job.",
        encoding="utf-8",
    )
    return corpus, source


def test_v9_bootstrap_contains_source_and_job_state_machine(tmp_path: Path) -> None:
    ensure_database_ready(tmp_path)

    with sqlite3.connect(resolve_database_path(tmp_path)) as connection:
        version = int(
            connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()[0]
        )
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table'"
            )
        }
        job_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(jobs)")
        }

    assert CURRENT_SCHEMA_VERSION == 9
    assert version == 9
    assert {"registered_sources", "jobs"} <= tables
    assert {
        "kind",
        "status",
        "params_json",
        "progress_current",
        "progress_total",
        "message",
        "result_summary_json",
        "error_code",
        "error_message",
        "cancel_requested",
        "owner_pid",
        "owner_instance_id",
        "heartbeat_at",
    } <= job_columns


def test_corpus_source_registration_is_idempotent_and_public_payload_is_redacted(
    tmp_path: Path,
) -> None:
    corpus, _ = _corpus(tmp_path)
    ensure_database_ready(tmp_path)

    first, first_created = register_corpus_source(tmp_path, corpus)
    second, second_created = register_corpus_source(tmp_path, corpus)
    sources = list_sources(tmp_path)
    public = public_source_payload(first)

    assert first_created is True
    assert second_created is False
    assert first.id == second.id
    assert first.kind == SOURCE_KIND_CORPUS
    assert len(sources) == 1
    assert public["id"] == first.id
    assert public["display_name"] == corpus.name
    assert "path" not in public
    assert str(corpus) not in str(public)


def test_corpus_source_rejects_urls_files_and_symlink_roots(tmp_path: Path) -> None:
    ensure_database_ready(tmp_path)
    regular_file = tmp_path / "paper.md"
    regular_file.write_text("synthetic", encoding="utf-8")
    corpus, _ = _corpus(tmp_path, "real-corpus")
    symlink = tmp_path / "linked-corpus"
    symlink.symlink_to(corpus, target_is_directory=True)

    with pytest.raises(ValueError, match="local directory"):
        register_corpus_source(tmp_path, "https://example.invalid/papers")
    with pytest.raises(ValueError, match="directory"):
        register_corpus_source(tmp_path, regular_file)
    with pytest.raises(ValueError, match="symbolic link"):
        register_corpus_source(tmp_path, symlink)


def test_active_job_deduplication_and_queued_cancel_are_persistent(
    tmp_path: Path,
) -> None:
    corpus, _ = _corpus(tmp_path)
    ensure_database_ready(tmp_path)
    source, _ = register_corpus_source(tmp_path, corpus)

    first, first_created = enqueue_job(
        tmp_path,
        kind="index_corpus",
        source_id=source.id,
        params={"min_chars": 1},
    )
    duplicate, duplicate_created = enqueue_job(
        tmp_path,
        kind="index_corpus",
        source_id=source.id,
        params={"min_chars": 1},
    )
    cancelled = request_job_cancel(tmp_path, first.id)

    assert first_created is True
    assert duplicate_created is False
    assert duplicate.id == first.id
    assert cancelled.status == "cancelled"
    assert cancelled.cancel_requested is True
    assert get_job(tmp_path, first.id) == cancelled


def test_job_manager_runs_index_jobs_in_queue_order_without_touching_sources(
    tmp_path: Path,
) -> None:
    first_corpus, first_file = _corpus(tmp_path, "first")
    second_corpus, second_file = _corpus(tmp_path, "second")
    ensure_database_ready(tmp_path)
    first_source, _ = register_corpus_source(tmp_path, first_corpus)
    second_source, _ = register_corpus_source(tmp_path, second_corpus)
    first_bytes = first_file.read_bytes()
    second_bytes = second_file.read_bytes()
    first_job, _ = enqueue_job(
        tmp_path,
        kind="index_corpus",
        source_id=first_source.id,
        params={"min_chars": 1},
    )
    second_job, _ = enqueue_job(
        tmp_path,
        kind="index_corpus",
        source_id=second_source.id,
        params={"min_chars": 1},
    )
    manager = JobManager(tmp_path)

    assert manager.run_next_job() == first_job.id
    assert manager.run_next_job() == second_job.id
    assert manager.run_next_job() is None
    assert get_job(tmp_path, first_job.id).status == "completed"
    assert get_job(tmp_path, second_job.id).status == "completed"
    assert first_file.read_bytes() == first_bytes
    assert second_file.read_bytes() == second_bytes
    with sqlite3.connect(resolve_database_path(tmp_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 2


def test_running_job_cancellation_is_cooperative_and_does_not_complete(
    tmp_path: Path,
) -> None:
    corpus, _ = _corpus(tmp_path)
    ensure_database_ready(tmp_path)
    source, _ = register_corpus_source(tmp_path, corpus)
    job, _ = enqueue_job(
        tmp_path,
        kind="index_corpus",
        source_id=source.id,
        params={"min_chars": 1},
    )

    def cancel_during_handler(context: JobContext) -> dict[str, object]:
        request_job_cancel(tmp_path, job.id)
        assert context.cancel_requested() is True
        raise JobCancelled("Cancelled at a synthetic batch boundary.")

    manager = JobManager(tmp_path, handlers={"index_corpus": cancel_during_handler})

    assert manager.run_next_job() == job.id
    finished = get_job(tmp_path, job.id)
    assert finished.status == "cancelled"
    assert finished.cancel_requested is True
    assert finished.finished_at is not None
    assert finished.error_message is None
    source_after = list_sources(tmp_path)[0]
    assert source_after.last_success_at is None


def test_stale_worker_cannot_overwrite_terminal_job_or_source_state(
    tmp_path: Path,
) -> None:
    corpus, _ = _corpus(tmp_path)
    ensure_database_ready(tmp_path)
    source, _ = register_corpus_source(tmp_path, corpus)
    job, _ = enqueue_job(
        tmp_path,
        kind="index_corpus",
        source_id=source.id,
        params={"min_chars": 1},
    )

    def lose_ownership(context: JobContext) -> dict[str, object]:
        connection = connect_read_write(tmp_path)
        try:
            with connection:
                connection.execute(
                    """
                    UPDATE jobs
                    SET status = 'interrupted', owner_pid = NULL,
                        owner_instance_id = NULL, heartbeat_at = NULL,
                        error_code = 'synthetic_recovery',
                        message = 'Recovered by another worker.',
                        finished_at = updated_at
                    WHERE id = ?
                    """,
                    (context.job.id,),
                )
        finally:
            connection.close()
        return {"should_not_publish": True}

    manager = JobManager(tmp_path, handlers={"index_corpus": lose_ownership})

    assert manager.run_next_job() == job.id
    finished = get_job(tmp_path, job.id)
    assert finished.status == "interrupted"
    assert finished.error_code == "synthetic_recovery"
    assert finished.result_summary == {}
    assert list_sources(tmp_path)[0].last_success_at is None


def test_worker_lease_recovers_stale_same_pid_previous_instance(tmp_path: Path) -> None:
    corpus, _ = _corpus(tmp_path)
    ensure_database_ready(tmp_path)
    source, _ = register_corpus_source(tmp_path, corpus)
    job, _ = enqueue_job(
        tmp_path,
        kind="index_corpus",
        source_id=source.id,
        params={},
    )
    connection = connect_read_write(tmp_path)
    try:
        with connection:
            connection.execute(
                """
                UPDATE jobs
                SET status = 'running', owner_pid = ?,
                    owner_instance_id = 'previous-instance',
                    started_at = '2000-01-01T00:00:00+00:00',
                    heartbeat_at = '2000-01-01T00:00:00+00:00'
                WHERE id = ?
                """,
                (os.getpid(), job.id),
            )
    finally:
        connection.close()

    manager = JobManager(tmp_path)
    manager.start()
    manager.stop()

    assert get_job(tmp_path, job.id).status == "interrupted"


def test_only_one_background_job_manager_holds_project_lease(tmp_path: Path) -> None:
    ensure_database_ready(tmp_path)
    first = JobManager(tmp_path)
    second = JobManager(tmp_path)
    first.start()
    try:
        with pytest.raises(RuntimeError, match="worker is already active"):
            second.start()
    finally:
        first.stop()


def test_restart_recovery_only_interrupts_dead_job_owners(tmp_path: Path) -> None:
    corpus, _ = _corpus(tmp_path)
    ensure_database_ready(tmp_path)
    source, _ = register_corpus_source(tmp_path, corpus)
    dead_job, _ = enqueue_job(
        tmp_path,
        kind="index_corpus",
        source_id=source.id,
        params={},
    )
    live_job, _ = enqueue_job(
        tmp_path,
        kind="backup_project",
        source_id=None,
        params={},
    )
    connection = connect_read_write(tmp_path)
    try:
        with connection:
            connection.execute(
                """
                UPDATE jobs
                SET status = 'running', owner_pid = 99999999,
                    owner_instance_id = 'dead', started_at = created_at,
                    heartbeat_at = created_at
                WHERE id = ?
                """,
                (dead_job.id,),
            )
    finally:
        connection.close()

    dead_recovered = recover_interrupted_jobs(
        tmp_path,
        process_alive=lambda pid: pid == 1,
    )

    connection = connect_read_write(tmp_path)
    try:
        with connection:
            connection.execute(
                """
                UPDATE jobs
                SET status = 'running', owner_pid = ?, owner_instance_id = 'live',
                    started_at = created_at, heartbeat_at = created_at
                WHERE id = ?
                """,
                (1, live_job.id),
            )
    finally:
        connection.close()
    live_recovered = recover_interrupted_jobs(
        tmp_path,
        process_alive=lambda pid: pid == 1,
    )

    assert dead_recovered == 1
    assert live_recovered == 0
    assert get_job(tmp_path, dead_job.id).status == "interrupted"
    assert get_job(tmp_path, live_job.id).status == "running"


def test_enqueue_rechecks_source_after_prevalidation_in_same_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus, _ = _corpus(tmp_path)
    ensure_database_ready(tmp_path)
    source, _ = register_corpus_source(tmp_path, corpus)
    original_ensure = job_service.ensure_database_ready
    removed = False

    def remove_before_insert(project_dir: Path | str) -> Path:
        nonlocal removed
        if not removed:
            removed = True
            remove_source(tmp_path, source.id)
        return original_ensure(project_dir)

    monkeypatch.setattr(job_service, "ensure_database_ready", remove_before_insert)

    with pytest.raises(ValueError, match=r"removed before the job was queued"):
        enqueue_job(
            tmp_path,
            kind="index_corpus",
            source_id=source.id,
            params={},
        )

    assert list_jobs(tmp_path) == []


def test_public_job_payload_never_trusts_stored_message_or_error_text(
    tmp_path: Path,
) -> None:
    ensure_database_ready(tmp_path)
    job, _ = enqueue_job(
        tmp_path,
        kind="backup_project",
        source_id=None,
        params={},
    )
    private_path = str(tmp_path / "private.sqlite3")
    connection = connect_read_write(tmp_path)
    try:
        with connection:
            connection.execute(
                """
                UPDATE jobs
                SET status = 'failed', message = ?, error_code = ?,
                    error_message = ?, finished_at = updated_at
                WHERE id = ?
                """,
                (private_path, private_path, private_path, job.id),
            )
    finally:
        connection.close()

    payload = public_job_payload(get_job(tmp_path, job.id))

    assert private_path not in str(payload)
    assert payload["message"] == (
        "Local job failed; inspect the local CLI for details."
    )
    assert payload["error"] == {
        "code": "job_failed",
        "message": (
            "The local job did not complete. Run the equivalent CLI command for "
            "detailed local diagnostics, then retry."
        ),
    }


def test_normal_worker_stop_marks_inflight_job_interrupted_not_cancelled(
    tmp_path: Path,
) -> None:
    ensure_database_ready(tmp_path)
    job, _ = enqueue_job(
        tmp_path,
        kind="backup_project",
        source_id=None,
        params={},
    )
    entered = threading.Event()

    def wait_for_stop(context: JobContext) -> dict[str, object]:
        entered.set()
        poll = threading.Event()
        while not context.stop_requested():
            poll.wait(0.01)
        context.raise_if_interrupted()
        raise AssertionError("stop boundary must raise")

    manager = JobManager(tmp_path, handlers={"backup_project": wait_for_stop})
    manager.start()
    assert entered.wait(2.0) is True
    manager.stop(timeout=2.0)

    assert get_job(tmp_path, job.id).status == "interrupted"


def test_cancel_arriving_after_atomic_publication_records_completed(
    tmp_path: Path,
) -> None:
    ensure_database_ready(tmp_path)
    job, _ = enqueue_job(
        tmp_path,
        kind="backup_project",
        source_id=None,
        params={},
    )
    artifact = tmp_path / ".paper-galaxy" / "published.txt"

    def publish_then_cancel(context: JobContext) -> dict[str, object]:
        artifact.write_bytes(b"complete artifact")
        request_job_cancel(tmp_path, context.job.id)
        return {"backup_name": artifact.name, "file_count": 1}

    manager = JobManager(
        tmp_path,
        handlers={"backup_project": publish_then_cancel},
    )
    assert manager.run_next_job() == job.id

    finished = get_job(tmp_path, job.id)
    assert finished.status == "completed"
    assert finished.cancel_requested is True
    assert artifact.read_bytes() == b"complete artifact"


def test_analysis_followup_waits_for_all_input_jobs_and_is_deduplicated(
    tmp_path: Path,
) -> None:
    first_corpus, _ = _corpus(tmp_path, "first")
    second_corpus, _ = _corpus(tmp_path, "second")
    ensure_database_ready(tmp_path)
    first_source, _ = register_corpus_source(tmp_path, first_corpus)
    second_source, _ = register_corpus_source(tmp_path, second_corpus)
    for source in (first_source, second_source):
        enqueue_job(
            tmp_path,
            kind="index_corpus",
            source_id=source.id,
            params={"rebuild_analysis": True, "analysis_seed": 17},
        )
    order: list[str] = []

    def complete_input(context: JobContext) -> dict[str, object]:
        order.append(context.job.source_id or "missing")
        return {"run_id": context.job.id}

    def complete_analysis(context: JobContext) -> dict[str, object]:
        order.append("analysis")
        assert context.job.params["seed"] == 17
        return {"run_id": context.job.id}

    manager = JobManager(
        tmp_path,
        handlers={
            "index_corpus": complete_input,
            "rebuild_analysis": complete_analysis,
        },
    )

    assert manager.run_next_job() is not None
    assert manager.run_next_job() is not None
    jobs_before_analysis = list_jobs(tmp_path, limit=100)
    assert sum(job.kind == "rebuild_analysis" for job in jobs_before_analysis) == 1
    assert manager.run_next_job() is not None
    assert manager.run_next_job() is None
    assert order == [first_source.id, second_source.id, "analysis"]


def test_running_duplicate_can_upgrade_one_analysis_followup(
    tmp_path: Path,
) -> None:
    corpus, _ = _corpus(tmp_path)
    ensure_database_ready(tmp_path)
    source, _ = register_corpus_source(tmp_path, corpus)
    original, _ = enqueue_job(
        tmp_path,
        kind="index_corpus",
        source_id=source.id,
        params={"rebuild_analysis": False},
    )
    entered = threading.Event()
    release = threading.Event()

    def blocked_input(context: JobContext) -> dict[str, object]:
        entered.set()
        assert release.wait(5.0) is True
        return {"run_id": context.job.id}

    manager = JobManager(tmp_path, handlers={"index_corpus": blocked_input})
    worker = threading.Thread(target=manager.run_next_job)
    worker.start()
    assert entered.wait(2.0) is True
    duplicate, created = enqueue_job(
        tmp_path,
        kind="index_corpus",
        source_id=source.id,
        params={
            "rebuild_analysis": True,
            "analysis_seed": 19,
            "analysis_neighbors": 7,
        },
    )
    release.set()
    worker.join(5.0)

    assert worker.is_alive() is False
    assert created is False
    assert duplicate.id == original.id
    followups = [job for job in list_jobs(tmp_path) if job.kind == "rebuild_analysis"]
    assert len(followups) == 1
    assert followups[0].params["seed"] == 19
    assert followups[0].params["neighbors"] == 7


def test_worker_survives_one_transient_claim_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ensure_database_ready(tmp_path)
    job, _ = enqueue_job(
        tmp_path,
        kind="backup_project",
        source_id=None,
        params={},
    )
    original_claim = job_service._claim_next_job
    calls = 0
    completed = threading.Event()

    def flaky_claim(project_dir: Path, owner_instance_id: str):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise DatabaseLockedError(resolve_database_path(project_dir))
        return original_claim(project_dir, owner_instance_id)

    def handler(context: JobContext) -> dict[str, object]:
        completed.set()
        return {"backup_name": context.job.id}

    monkeypatch.setattr(job_service, "_claim_next_job", flaky_claim)
    manager = JobManager(tmp_path, handlers={"backup_project": handler})
    manager.start()
    try:
        assert completed.wait(3.0) is True
    finally:
        manager.stop()

    assert calls >= 2
    assert get_job(tmp_path, job.id).status == "completed"


def test_idle_worker_exits_if_metadata_lock_path_is_replaced(
    tmp_path: Path,
) -> None:
    ensure_database_ready(tmp_path)
    manager = JobManager(tmp_path)
    manager.start()
    metadata = tmp_path / ".paper-galaxy"
    displaced = tmp_path / "displaced-metadata"
    metadata.rename(displaced)
    shutil.copytree(displaced, metadata)
    thread = manager._thread
    assert thread is not None
    thread.join(3.0)
    assert thread.is_alive() is False

    job, _ = enqueue_job(
        tmp_path,
        kind="backup_project",
        source_id=None,
        params={},
    )
    manager.stop()

    assert get_job(tmp_path, job.id).status == "queued"


def test_replacement_worker_recovery_fences_old_handler_before_publish(
    tmp_path: Path,
) -> None:
    ensure_database_ready(tmp_path)
    job, _ = enqueue_job(
        tmp_path,
        kind="backup_project",
        source_id=None,
        params={},
    )
    entered = threading.Event()
    release = threading.Event()
    artifact = tmp_path / "must-not-publish.txt"

    def guarded_publish(context: JobContext) -> dict[str, object]:
        entered.set()
        assert release.wait(5.0) is True
        context.raise_if_worker_stopped_or_ownership_lost()
        artifact.write_bytes(b"stale worker output")
        return {"backup_name": artifact.name}

    first = JobManager(tmp_path, handlers={"backup_project": guarded_publish})
    first.start()
    assert entered.wait(2.0) is True
    metadata = tmp_path / ".paper-galaxy"
    displaced = tmp_path / "displaced-active-metadata"
    metadata.rename(displaced)
    shutil.copytree(displaced, metadata)

    second = JobManager(tmp_path)
    second.start()
    try:
        assert get_job(tmp_path, job.id).status == "interrupted"
        release.set()
        first.stop(timeout=3.0)
    finally:
        second.stop()

    assert not artifact.exists()


def test_prepare_launch_initializes_once_registers_sources_and_queues_once(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    corpus, _ = _corpus(tmp_path)

    first = prepare_launch(
        project_dir=project,
        corpus_dirs=[corpus],
        zotero_sync=False,
    )
    config_before = (project / ".paper-galaxy" / "project.toml").read_bytes()
    second = prepare_launch(
        project_dir=project,
        corpus_dirs=[corpus],
        zotero_sync=False,
    )

    assert first.project_created is True
    assert len(first.source_ids) == 1
    assert len(first.job_ids) == 1
    assert second.project_created is False
    assert second.source_ids == first.source_ids
    assert second.job_ids == ()
    assert (project / ".paper-galaxy" / "project.toml").read_bytes() == config_before
    assert resolve_database_path(project).is_file()


def test_repeated_launch_repairs_source_registered_before_enqueue_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    corpus, _ = _corpus(tmp_path)
    original_enqueue = launch_service.enqueue_job

    def fail_enqueue(*args: object, **kwargs: object):
        del args, kwargs
        raise DatabaseLockedError(resolve_database_path(project))

    monkeypatch.setattr(launch_service, "enqueue_job", fail_enqueue)
    with pytest.raises(DatabaseLockedError):
        prepare_launch(
            project_dir=project,
            corpus_dirs=[corpus],
            queue_analysis=True,
        )
    assert len(list_sources(project)) == 1
    assert list_jobs(project) == []

    monkeypatch.setattr(launch_service, "enqueue_job", original_enqueue)
    repaired = prepare_launch(
        project_dir=project,
        corpus_dirs=[corpus],
        queue_analysis=True,
    )

    assert repaired.project_created is False
    assert len(repaired.job_ids) == 1
    queued = list_jobs(project)
    assert len(queued) == 1
    assert queued[0].kind == "index_corpus"
    assert queued[0].params["rebuild_analysis"] is True


def test_prepare_launch_rejects_project_inside_corpus_before_any_write(
    tmp_path: Path,
) -> None:
    corpus, source = _corpus(tmp_path)
    project = corpus / "PaperGalaxy"
    before = source.read_bytes()

    with pytest.raises(ValueError, match=r"project metadata or database"):
        prepare_launch(project_dir=project, corpus_dirs=[corpus])

    assert source.read_bytes() == before
    assert not project.exists()


def test_prepare_launch_rejects_custom_database_inside_corpus_before_creation(
    tmp_path: Path,
) -> None:
    corpus, source = _corpus(tmp_path)
    project = tmp_path / "project"
    metadata = project / ".paper-galaxy"
    metadata.mkdir(parents=True)
    database = corpus / "private.sqlite3"
    (metadata / "project.toml").write_text(
        "\n".join(
            [
                'project_name = "Synthetic"',
                'created_by = "test"',
                "map_seed = 42",
                "corpus_dirs = []",
                f'database_path = "{database.as_posix()}"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    before = source.read_bytes()

    with pytest.raises(ValueError, match=r"database cannot be stored inside"):
        prepare_launch(project_dir=project, corpus_dirs=[corpus])

    assert source.read_bytes() == before
    assert not database.exists()


def test_prepare_launch_rejects_project_symlink_without_touching_target(
    tmp_path: Path,
) -> None:
    corpus, _ = _corpus(tmp_path)
    target = tmp_path / "user-directory"
    target.mkdir()
    sentinel = target / "keep.txt"
    sentinel.write_bytes(b"private")
    linked_project = tmp_path / "linked-project"
    linked_project.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match=r"Project directory.*symbolic link"):
        prepare_launch(project_dir=linked_project, corpus_dirs=[corpus])

    assert sentinel.read_bytes() == b"private"
    assert not (target / ".paper-galaxy").exists()


def test_prepare_launch_preflights_all_corpora_before_registration(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    open_or_initialize_project(project)
    safe_corpus, _ = _corpus(tmp_path, "safe-corpus")
    unsafe_corpus = project / ".paper-galaxy" / "backups"
    unsafe_corpus.mkdir()

    with pytest.raises(ValueError, match=r"project metadata or database"):
        prepare_launch(
            project_dir=project,
            corpus_dirs=[safe_corpus, unsafe_corpus],
        )

    assert list_sources(project) == []


def test_project_initializer_rejects_metadata_and_config_symlinks(
    tmp_path: Path,
) -> None:
    metadata_target = tmp_path / "metadata-target"
    metadata_target.mkdir()
    metadata_sentinel = metadata_target / "keep.txt"
    metadata_sentinel.write_bytes(b"keep metadata")
    metadata_project = tmp_path / "metadata-project"
    metadata_project.mkdir()
    (metadata_project / ".paper-galaxy").symlink_to(
        metadata_target,
        target_is_directory=True,
    )

    with pytest.raises(ValueError, match=r"metadata directory.*symbolic link"):
        open_or_initialize_project(metadata_project)
    assert metadata_sentinel.read_bytes() == b"keep metadata"

    config_target = tmp_path / "user-config.toml"
    config_target.write_bytes(b"private user config")
    config_project = tmp_path / "config-project"
    (config_project / ".paper-galaxy").mkdir(parents=True)
    (config_project / ".paper-galaxy" / "project.toml").symlink_to(config_target)

    with pytest.raises(ValueError, match=r"configuration.*symbolic link"):
        open_or_initialize_project(config_project)
    assert config_target.read_bytes() == b"private user config"


def test_project_initializer_preserves_invalid_existing_config_bytes(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    metadata = project / ".paper-galaxy"
    metadata.mkdir(parents=True)
    config = metadata / "project.toml"
    original = b'project_name = "unterminated\n'
    config.write_bytes(original)

    with pytest.raises(ValueError, match=r"Existing project.toml is invalid"):
        open_or_initialize_project(project)

    assert config.read_bytes() == original
    assert not (metadata / "paper_galaxy.sqlite3").exists()


def test_project_initializer_cleans_staging_if_atomic_publish_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"

    def fail_publish(source: Path, destination: Path) -> None:
        del source, destination
        raise OSError("synthetic atomic publish failure")

    monkeypatch.setattr(project_lifecycle.os, "link", fail_publish)

    with pytest.raises(OSError, match="synthetic atomic publish failure"):
        open_or_initialize_project(project)

    metadata = project / ".paper-galaxy"
    assert not (metadata / "project.toml").exists()
    assert not list(metadata.glob(".project.toml.*.staging"))
