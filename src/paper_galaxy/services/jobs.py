"""Durable, single-writer local jobs without an external queue service."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, cast
from uuid import uuid4

from paper_galaxy.errors import DatabaseError
from paper_galaxy.processes import process_is_alive
from paper_galaxy.services.sources import (
    SOURCE_KIND_CORPUS,
    SOURCE_KIND_ZOTERO,
    SourceRecord,
    get_source,
    validate_source_locator_for_use,
)
from paper_galaxy.services.worker_lease import (
    JobWorkerLease,
    JobWorkerLeaseError,
    acquire_job_worker_lease,
)
from paper_galaxy.storage.json import load_json_object
from paper_galaxy.storage.sqlite import (
    connect_read_only,
    connect_read_write,
    ensure_database_ready,
)

JOB_KINDS = frozenset(
    {"index_corpus", "zotero_sync", "rebuild_analysis", "backup_project"}
)
ACTIVE_JOB_STATUSES = frozenset({"queued", "running", "cancelling"})
TERMINAL_JOB_STATUSES = frozenset({"completed", "failed", "interrupted", "cancelled"})
MAX_JOB_LIST_LIMIT = 100
MAX_QUEUED_JOBS = 1000
_MAX_MESSAGE_LENGTH = 300
_PUBLIC_ERROR_CODE_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")
_ANALYSIS_FOLLOWUP_FIELDS = (
    "rebuild_analysis",
    "analysis_seed",
    "analysis_neighbors",
    "analysis_limit",
)
_RESULT_FIELDS: dict[str, frozenset[str]] = {
    "index_corpus": frozenset(
        {
            "run_id",
            "files_found",
            "documents_inserted",
            "documents_updated",
            "documents_unchanged",
            "documents_missing",
            "skipped_files",
        }
    ),
    "zotero_sync": frozenset(
        {"run_id", "items_seen", "items_imported", "items_updated", "items_unchanged"}
    ),
    "rebuild_analysis": frozenset({"run_id", "document_count", "cluster_count"}),
    "backup_project": frozenset({"backup_name", "file_count", "contains_database"}),
}


class JobCancelled(RuntimeError):
    """Raised by a handler at a cooperative cancellation boundary."""


class WorkerStopping(RuntimeError):
    """Raised when a worker must stop without claiming a user cancellation."""


class JobOwnershipLost(WorkerStopping):
    """Raised when a stale handler no longer owns its durable job row."""


@dataclass(frozen=True)
class JobRecord:
    """One durable local job row."""

    id: str
    queue_sequence: int
    kind: str
    source_id: str | None
    request_key: str
    status: str
    params: dict[str, Any]
    current: int
    total: int | None
    message: str
    result_summary: dict[str, Any]
    error_code: str | None
    error_message: str | None
    cancel_requested: bool
    owner_pid: int | None
    owner_instance_id: str | None
    heartbeat_at: str | None
    revision: int
    created_at: str
    started_at: str | None
    finished_at: str | None
    updated_at: str


@dataclass(frozen=True)
class JobContext:
    """Narrow control surface passed to one job handler."""

    project_dir: Path
    job: JobRecord
    owner_instance_id: str
    stop_requested: Callable[[], bool]

    def cancel_requested(self) -> bool:
        try:
            self.raise_if_interrupted()
        except (JobCancelled, WorkerStopping):
            return True
        return False

    def raise_if_cancelled(self) -> None:
        """Backward-compatible alias for an ownership-aware batch boundary."""

        self.raise_if_interrupted()

    def raise_if_interrupted(self) -> None:
        current = self._current_owned_job()
        if current.cancel_requested or current.status == "cancelling":
            raise JobCancelled("Cancelled at a safe batch boundary.")

    def raise_if_worker_stopped_or_ownership_lost(self) -> None:
        """Fence commits while allowing explicit cancel after the current batch."""

        self._current_owned_job()

    def _current_owned_job(self) -> JobRecord:
        if self.stop_requested():
            raise WorkerStopping("Worker stopped at a safe batch boundary.")
        current = get_job(self.project_dir, self.job.id)
        if (
            current.owner_instance_id != self.owner_instance_id
            or current.status not in {"running", "cancelling"}
        ):
            raise JobOwnershipLost("Job ownership changed at a safe boundary.")
        return current

    def report_progress(
        self,
        current: int,
        total: int | None,
        message: str,
    ) -> None:
        _update_job_progress(
            self.project_dir,
            job_id=self.job.id,
            owner_instance_id=self.owner_instance_id,
            current=current,
            total=total,
            message=message,
        )


JobHandler = Callable[[JobContext], dict[str, object]]


def enqueue_job(
    project_dir: Path | str,
    *,
    kind: str,
    source_id: str | None,
    params: Mapping[str, object],
) -> tuple[JobRecord, bool]:
    """Enqueue one canonical request or return its existing active job."""

    if kind not in JOB_KINDS:
        raise ValueError("Unknown job kind.")
    normalized_params = _normalize_params(kind, params)
    _validate_source_for_job(project_dir, kind=kind, source_id=source_id)
    request_key = _request_key(
        kind,
        source_id,
        _work_params_for_request_key(kind, normalized_params),
    )
    ensure_database_ready(project_dir)
    now = _utc_now()
    connection = connect_read_write(project_dir)
    try:
        connection.execute("BEGIN IMMEDIATE")
        _validate_source_row_for_job(
            connection,
            kind=kind,
            source_id=source_id,
        )
        existing = connection.execute(
            """
            SELECT * FROM jobs
            WHERE request_key = ?
              AND status IN ('queued', 'running', 'cancelling')
            ORDER BY queue_sequence
            LIMIT 1
            """,
            (request_key,),
        ).fetchone()
        if existing is not None:
            existing = _upgrade_analysis_followup_in_connection(
                connection,
                existing=existing,
                incoming_params=normalized_params,
                now=now,
            )
            connection.commit()
            return _job_from_row(existing), False
        queued = int(
            connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE status = 'queued'"
            ).fetchone()[0]
        )
        if queued >= MAX_QUEUED_JOBS:
            raise ValueError(
                "The local job queue is full; finish or cancel jobs first."
            )
        sequence = int(
            connection.execute(
                "SELECT COALESCE(MAX(queue_sequence), 0) + 1 FROM jobs"
            ).fetchone()[0]
        )
        job_id = f"job_{uuid4().hex}"
        connection.execute(
            """
            INSERT INTO jobs(
              id, queue_sequence, kind, source_id, request_key, status,
              params_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?)
            """,
            (
                job_id,
                sequence,
                kind,
                source_id,
                request_key,
                _canonical_json(normalized_params),
                now,
                now,
            ),
        )
        row = connection.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        connection.commit()
        if row is None:
            raise RuntimeError("Queued job disappeared during creation.")
        return _job_from_row(row), True
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_job(project_dir: Path | str, job_id: str) -> JobRecord:
    """Return one job or raise a stable lookup error."""

    connection = connect_read_only(project_dir)
    try:
        row = connection.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise ValueError("No local job exists with that id.")
        return _job_from_row(row)
    finally:
        connection.close()


def list_jobs(
    project_dir: Path | str,
    *,
    status: str | None = None,
    kind: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[JobRecord]:
    """Return bounded job history in newest-first order."""

    if status is not None and status not in ACTIVE_JOB_STATUSES | TERMINAL_JOB_STATUSES:
        raise ValueError("Unknown job status.")
    if kind is not None and kind not in JOB_KINDS:
        raise ValueError("Unknown job kind.")
    clauses: list[str] = []
    parameters: list[object] = []
    if status is not None:
        clauses.append("status = ?")
        parameters.append(status)
    if kind is not None:
        clauses.append("kind = ?")
        parameters.append(kind)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    connection = connect_read_only(project_dir)
    try:
        rows = connection.execute(
            f"""
            SELECT * FROM jobs
            {where}
            ORDER BY queue_sequence DESC
            LIMIT ? OFFSET ?
            """,
            (
                *parameters,
                min(max(0, limit), MAX_JOB_LIST_LIMIT),
                max(0, offset),
            ),
        ).fetchall()
        return [_job_from_row(row) for row in rows]
    finally:
        connection.close()


def request_job_cancel(project_dir: Path | str, job_id: str) -> JobRecord:
    """Cancel queued work or request cooperative cancellation of running work."""

    now = _utc_now()
    connection = connect_read_write(project_dir)
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise ValueError("No local job exists with that id.")
        status = str(row["status"])
        if status == "queued":
            connection.execute(
                """
                UPDATE jobs
                SET status = 'cancelled', cancel_requested = 1,
                    message = 'Cancelled before starting.', finished_at = ?,
                    updated_at = ?, revision = revision + 1
                WHERE id = ? AND status = 'queued'
                """,
                (now, now, job_id),
            )
        elif status == "running":
            connection.execute(
                """
                UPDATE jobs
                SET status = 'cancelling', cancel_requested = 1,
                    message = 'Cancellation requested.', updated_at = ?,
                    revision = revision + 1
                WHERE id = ? AND status = 'running'
                """,
                (now, job_id),
            )
        updated = connection.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        connection.commit()
        if updated is None:
            raise RuntimeError("Job disappeared during cancellation.")
        return _job_from_row(updated)
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def recover_interrupted_jobs(
    project_dir: Path | str,
    *,
    process_alive: Callable[[int], bool] = process_is_alive,
    exclusive_worker_lease: bool = False,
) -> int:
    """Mark only dead-owner active jobs interrupted during explicit startup."""

    ensure_database_ready(project_dir)
    now = _utc_now()
    connection = connect_read_write(project_dir)
    recovered = 0
    try:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            """
            SELECT id, owner_pid
            FROM jobs
            WHERE status IN ('running', 'cancelling')
            ORDER BY queue_sequence
            """
        ).fetchall()
        for row in rows:
            pid = row["owner_pid"]
            alive = False
            if (
                not exclusive_worker_lease
                and isinstance(pid, int)
                and not isinstance(pid, bool)
                and pid > 0
            ):
                alive = process_alive(pid)
            if alive:
                continue
            connection.execute(
                """
                UPDATE jobs
                SET status = 'interrupted', owner_pid = NULL,
                    owner_instance_id = NULL, heartbeat_at = NULL,
                    message = 'Interrupted because the previous worker stopped.',
                    error_code = 'worker_interrupted', error_message = NULL,
                    finished_at = ?, updated_at = ?, revision = revision + 1
                WHERE id = ? AND status IN ('running', 'cancelling')
                """,
                (now, now, str(row["id"])),
            )
            recovered += 1
        connection.commit()
        return recovered
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def public_job_payload(job: JobRecord) -> dict[str, object]:
    """Return a path-free, raw-payload-free job response."""

    public_messages = {
        "queued": "Queued for local processing.",
        "running": "Running local job.",
        "cancelling": "Cancellation requested; finishing a safe batch.",
        "completed": "Completed.",
        "failed": "Local job failed; inspect the local CLI for details.",
        "interrupted": "Local job was interrupted and can be retried.",
        "cancelled": "Cancelled at a safe batch boundary.",
    }
    public_error_code = (
        job.error_code
        if job.error_code is not None
        and _PUBLIC_ERROR_CODE_PATTERN.fullmatch(job.error_code)
        else "job_failed"
    )
    return {
        "id": job.id,
        "kind": job.kind,
        "source_id": job.source_id,
        "status": job.status,
        "current": job.current,
        "total": job.total,
        "message": public_messages.get(job.status, "Local job status unavailable."),
        "result_summary": _safe_result_summary(job.kind, job.result_summary),
        "error": (
            {
                "code": public_error_code,
                "message": (
                    "The local job did not complete. Run the equivalent CLI "
                    "command for detailed local diagnostics, then retry."
                ),
            }
            if job.error_code
            else None
        ),
        "cancel_requested": job.cancel_requested,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "updated_at": job.updated_at,
    }


class JobManager:
    """One lightweight worker; every database operation uses a short connection."""

    def __init__(
        self,
        project_dir: Path | str,
        *,
        handlers: Mapping[str, JobHandler] | None = None,
    ) -> None:
        self.project_dir = Path(project_dir).expanduser().resolve()
        self.owner_instance_id = f"worker_{uuid4().hex}"
        self._handlers = dict(handlers or _default_handlers())
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._worker_lease: JobWorkerLease | None = None
        self._lease_lock = threading.Lock()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._release_worker_lease()
        ensure_database_ready(self.project_dir)
        lease = acquire_job_worker_lease(self.project_dir)
        try:
            recover_interrupted_jobs(
                self.project_dir,
                exclusive_worker_lease=True,
            )
            if _has_active_jobs(self.project_dir):
                raise JobWorkerLeaseError(
                    "A live process still owns an active local job; stop that "
                    "process or wait for it to finish before launching again."
                )
        except BaseException:
            lease.close()
            raise
        with self._lease_lock:
            self._worker_lease = lease
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"paper-galaxy-jobs-{self.owner_instance_id[-8:]}",
            daemon=True,
        )
        self._thread.start()

    def notify(self) -> None:
        self._wake.set()

    def stop(self, *, timeout: float = 10.0) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(0.0, timeout))
        if thread is None or not thread.is_alive():
            self._release_worker_lease()

    def run_next_job(self) -> str | None:
        job = _claim_next_job(self.project_dir, self.owner_instance_id)
        if job is None:
            return None
        context = JobContext(
            project_dir=self.project_dir,
            job=job,
            owner_instance_id=self.owner_instance_id,
            stop_requested=self._worker_stopping,
        )
        handler = self._handlers.get(job.kind)
        if handler is None:
            _fail_job(
                self.project_dir,
                job=job,
                owner_instance_id=self.owner_instance_id,
                error=RuntimeError("No local handler is registered."),
            )
            return job.id
        try:
            context.raise_if_interrupted()
            result = handler(context)
            _complete_job(
                self.project_dir,
                job=job,
                owner_instance_id=self.owner_instance_id,
                result=result,
            )
        except JobCancelled:
            _finish_cancelled_job(
                self.project_dir,
                job=job,
                owner_instance_id=self.owner_instance_id,
            )
        except (KeyboardInterrupt, WorkerStopping):
            _interrupt_job(
                self.project_dir,
                job=job,
                owner_instance_id=self.owner_instance_id,
            )
        except BaseException as exc:
            _fail_job(
                self.project_dir,
                job=job,
                owner_instance_id=self.owner_instance_id,
                error=exc,
            )
        return job.id

    def _run(self) -> None:
        claimed_job_may_need_recovery = False
        try:
            while not self._stop.is_set():
                try:
                    if self._worker_stopping():
                        _interrupt_jobs_owned_by(
                            self.project_dir,
                            self.owner_instance_id,
                        )
                        break
                    if claimed_job_may_need_recovery:
                        _interrupt_jobs_owned_by(
                            self.project_dir,
                            self.owner_instance_id,
                        )
                        claimed_job_may_need_recovery = False
                    if self.run_next_job() is not None:
                        continue
                    self._wake.wait(0.25)
                    self._wake.clear()
                except (DatabaseError, sqlite3.Error):
                    claimed_job_may_need_recovery = True
                    self._wake.wait(0.1)
                    self._wake.clear()
        finally:
            self._release_worker_lease()

    def _worker_stopping(self) -> bool:
        if self._stop.is_set():
            return True
        with self._lease_lock:
            lease = self._worker_lease
        return lease is not None and not lease.is_current()

    def _release_worker_lease(self) -> None:
        with self._lease_lock:
            lease, self._worker_lease = self._worker_lease, None
        if lease is not None:
            lease.close()


def _claim_next_job(project_dir: Path, owner_instance_id: str) -> JobRecord | None:
    now = _utc_now()
    connection = connect_read_write(project_dir)
    try:
        connection.execute("BEGIN IMMEDIATE")
        active = connection.execute(
            "SELECT 1 FROM jobs WHERE status IN ('running', 'cancelling') LIMIT 1"
        ).fetchone()
        if active is not None:
            connection.commit()
            return None
        row = connection.execute(
            """
            SELECT * FROM jobs
            WHERE status = 'queued'
            ORDER BY queue_sequence
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            connection.commit()
            return None
        job_id = str(row["id"])
        cursor = connection.execute(
            """
            UPDATE jobs
            SET status = 'running', owner_pid = ?, owner_instance_id = ?,
                started_at = ?, heartbeat_at = ?, updated_at = ?,
                message = 'Starting local job.', revision = revision + 1
            WHERE id = ? AND status = 'queued'
              AND NOT EXISTS (
                SELECT 1 FROM jobs active
                WHERE active.status IN ('running', 'cancelling')
              )
            """,
            (os.getpid(), owner_instance_id, now, now, now, job_id),
        )
        if cursor.rowcount != 1:
            connection.commit()
            return None
        claimed = connection.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        connection.commit()
        return _job_from_row(claimed) if claimed is not None else None
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _has_active_jobs(project_dir: Path) -> bool:
    connection = connect_read_only(project_dir)
    try:
        return (
            connection.execute(
                "SELECT 1 FROM jobs WHERE status IN ('running', 'cancelling') LIMIT 1"
            ).fetchone()
            is not None
        )
    finally:
        connection.close()


def _interrupt_jobs_owned_by(project_dir: Path, owner_instance_id: str) -> int:
    """Fence jobs whose handler was lost after a transient supervisor error."""

    now = _utc_now()
    connection = connect_read_write(project_dir)
    try:
        with connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status = 'interrupted', owner_pid = NULL,
                    owner_instance_id = NULL, heartbeat_at = NULL,
                    message = 'Interrupted after a transient worker failure.',
                    error_code = 'worker_interrupted', error_message = NULL,
                    finished_at = ?, updated_at = ?, revision = revision + 1
                WHERE owner_instance_id = ?
                  AND status IN ('running', 'cancelling')
                """,
                (now, now, owner_instance_id),
            )
            return cursor.rowcount
    finally:
        connection.close()


def _update_job_progress(
    project_dir: Path,
    *,
    job_id: str,
    owner_instance_id: str,
    current: int,
    total: int | None,
    message: str,
) -> None:
    if current < 0 or (total is not None and (total < 0 or current > total)):
        raise ValueError("Job progress is outside its valid bounds.")
    now = _utc_now()
    connection = connect_read_write(project_dir)
    try:
        with connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET progress_current = ?, progress_total = ?, message = ?,
                    heartbeat_at = ?, updated_at = ?, revision = revision + 1
                WHERE id = ? AND owner_instance_id = ?
                  AND status IN ('running', 'cancelling')
                """,
                (
                    current,
                    total,
                    _safe_message(message),
                    now,
                    now,
                    job_id,
                    owner_instance_id,
                ),
            )
            if cursor.rowcount != 1:
                raise JobCancelled("Job ownership changed during progress update.")
    finally:
        connection.close()


def _complete_job(
    project_dir: Path,
    *,
    job: JobRecord,
    owner_instance_id: str,
    result: Mapping[str, object],
) -> bool:
    now = _utc_now()
    connection = connect_read_write(project_dir)
    try:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            """
            UPDATE jobs
            SET status = 'completed', result_summary_json = ?,
                message = 'Completed.', owner_pid = NULL,
                owner_instance_id = NULL, heartbeat_at = NULL,
                finished_at = ?, updated_at = ?, revision = revision + 1
            WHERE id = ? AND owner_instance_id = ?
              AND status IN ('running', 'cancelling')
            """,
            (
                _canonical_json(_safe_result_summary(job.kind, result)),
                now,
                now,
                job.id,
                owner_instance_id,
            ),
        )
        completed = cursor.rowcount == 1
        if completed and job.source_id is not None:
            connection.execute(
                """
                UPDATE registered_sources
                SET last_success_at = ?, last_error_code = NULL,
                    last_error_message = NULL, updated_at = ?
                WHERE id = ?
                """,
                (now, now, job.source_id),
            )
        if completed:
            current_params_row = connection.execute(
                "SELECT params_json FROM jobs WHERE id = ?",
                (job.id,),
            ).fetchone()
            current_params = (
                load_json_object(current_params_row["params_json"])
                if current_params_row is not None
                else job.params
            )
            _enqueue_requested_analysis_in_connection(
                connection,
                parent_kind=job.kind,
                parent_params=current_params,
                now=now,
            )
        connection.commit()
        return completed
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _finish_cancelled_job(
    project_dir: Path,
    *,
    job: JobRecord,
    owner_instance_id: str,
) -> None:
    now = _utc_now()
    connection = connect_read_write(project_dir)
    try:
        with connection:
            _finish_cancelled_in_connection(
                connection,
                job_id=job.id,
                owner_instance_id=owner_instance_id,
                now=now,
            )
    finally:
        connection.close()


def _finish_cancelled_in_connection(
    connection: sqlite3.Connection,
    *,
    job_id: str,
    owner_instance_id: str,
    now: str,
) -> bool:
    cursor = connection.execute(
        """
        UPDATE jobs
        SET status = 'cancelled', cancel_requested = 1,
            message = 'Cancelled at a safe batch boundary.',
            error_code = NULL, error_message = NULL, owner_pid = NULL,
            owner_instance_id = NULL, heartbeat_at = NULL,
            finished_at = ?, updated_at = ?, revision = revision + 1
        WHERE id = ? AND owner_instance_id = ?
          AND status IN ('running', 'cancelling')
        """,
        (now, now, job_id, owner_instance_id),
    )
    return cursor.rowcount == 1


def _interrupt_job(
    project_dir: Path,
    *,
    job: JobRecord,
    owner_instance_id: str,
) -> None:
    _finalize_error(
        project_dir,
        job=job,
        owner_instance_id=owner_instance_id,
        status="interrupted",
        error_code="worker_interrupted",
        error_message=None,
        message="Interrupted before completion.",
    )


def _fail_job(
    project_dir: Path,
    *,
    job: JobRecord,
    owner_instance_id: str,
    error: BaseException,
) -> None:
    code, message = _safe_error(error)
    _finalize_error(
        project_dir,
        job=job,
        owner_instance_id=owner_instance_id,
        status="failed",
        error_code=code,
        error_message=message,
        message="Failed. Review the actionable error and retry.",
    )


def _finalize_error(
    project_dir: Path,
    *,
    job: JobRecord,
    owner_instance_id: str,
    status: str,
    error_code: str,
    error_message: str | None,
    message: str,
) -> None:
    now = _utc_now()
    connection = connect_read_write(project_dir)
    try:
        with connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status = ?, message = ?, error_code = ?, error_message = ?,
                    owner_pid = NULL, owner_instance_id = NULL,
                    heartbeat_at = NULL, finished_at = ?, updated_at = ?,
                    revision = revision + 1
                WHERE id = ? AND owner_instance_id = ?
                  AND status IN ('running', 'cancelling')
                """,
                (
                    status,
                    message,
                    error_code,
                    error_message,
                    now,
                    now,
                    job.id,
                    owner_instance_id,
                ),
            )
            if cursor.rowcount == 1 and job.source_id is not None:
                connection.execute(
                    """
                    UPDATE registered_sources
                    SET last_error_code = ?, last_error_message = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (error_code, error_message, now, job.source_id),
                )
    finally:
        connection.close()


def _default_handlers() -> dict[str, JobHandler]:
    return {
        "index_corpus": _run_index_job,
        "zotero_sync": _run_zotero_job,
        "rebuild_analysis": _run_analysis_job,
        "backup_project": _run_backup_job,
    }


def _run_index_job(context: JobContext) -> dict[str, object]:
    from paper_galaxy.indexer import IndexingCancelled, index_corpus

    source = _required_source(context, expected_kind=SOURCE_KIND_CORPUS)
    if source.root_path is None:
        raise RuntimeError("Registered corpus source has no local root.")
    context.report_progress(0, None, "Scanning registered corpus.")
    context.raise_if_interrupted()
    try:
        summary = index_corpus(
            Path(source.root_path),
            project_dir=context.project_dir,
            min_chars=int(context.job.params.get("min_chars", 80)),
            chunk_size=int(context.job.params.get("chunk_size", 2000)),
            chunk_overlap=int(context.job.params.get("chunk_overlap", 200)),
            cancel_requested=context.cancel_requested,
            commit_guard=context.raise_if_worker_stopped_or_ownership_lost,
            progress_callback=context.report_progress,
        )
    except IndexingCancelled as exc:
        context.raise_if_interrupted()
        raise JobCancelled(str(exc)) from exc
    return {
        "run_id": summary.scan_run_id,
        "files_found": summary.files_found,
        "documents_inserted": summary.documents_inserted,
        "documents_updated": summary.documents_updated,
        "documents_unchanged": summary.documents_unchanged,
        "documents_missing": summary.documents_missing,
        "skipped_files": summary.skipped_files,
    }


def _run_zotero_job(context: JobContext) -> dict[str, object]:
    from paper_galaxy.zotero.importers import (
        ZoteroImportCancelled,
        import_from_zotero,
    )

    source = _required_source(context, expected_kind=SOURCE_KIND_ZOTERO)
    filters = source.config.get("filters", {})
    profile_filters = filters if isinstance(filters, dict) else {}
    collections = _string_tuple(profile_filters.get("collections"))
    context.raise_if_interrupted()
    try:
        summary = import_from_zotero(
            project_dir=context.project_dir,
            api_url=str(
                source.config.get("local_api_url", "http://127.0.0.1:23119/api")
            ),
            data_dir=(
                Path(str(source.config["data_dir"]))
                if source.config.get("data_dir")
                else None
            ),
            include_pdfs=bool(context.job.params.get("include_pdfs", True)),
            include_notes=bool(context.job.params.get("include_notes", True)),
            include_attachments=bool(
                context.job.params.get("include_attachments", True)
            ),
            collection=collections[0] if collections else None,
            tags=_string_tuple(profile_filters.get("tags")),
            item_types=_string_tuple(profile_filters.get("item_types")),
            include_status=str(profile_filters.get("include_status", "all")),
            pdf_policy=str(profile_filters.get("pdf_policy", "extract")),
            build_reading_map=False,
            cancel_requested=context.cancel_requested,
            commit_guard=context.raise_if_worker_stopped_or_ownership_lost,
            progress_callback=context.report_progress,
        )
    except ZoteroImportCancelled as exc:
        context.raise_if_interrupted()
        raise JobCancelled(str(exc)) from exc
    return {
        "run_id": summary.run_id,
        "items_seen": summary.items_seen,
        "items_imported": summary.items_imported,
        "items_updated": summary.items_updated,
        "items_unchanged": summary.items_unchanged,
    }


def _run_analysis_job(context: JobContext) -> dict[str, object]:
    from paper_galaxy.maps import build_and_store_map_run
    from paper_galaxy.maps.runs import MapBuildCancelled

    context.raise_if_interrupted()
    try:
        result = build_and_store_map_run(
            project_dir=context.project_dir,
            name=str(context.job.params.get("name", "Automatic local analysis")),
            seed=int(context.job.params.get("seed", 42)),
            neighbors=int(context.job.params.get("neighbors", 5)),
            limit=int(context.job.params.get("limit", 1000)),
            cancel_requested=context.cancel_requested,
        )
    except MapBuildCancelled as exc:
        context.raise_if_interrupted()
        raise JobCancelled(str(exc)) from exc
    run = result.get("map_run")
    run_payload = run if isinstance(run, dict) else {}
    return {
        "run_id": run_payload.get("id"),
        "document_count": run_payload.get("document_count", 0),
        "cluster_count": run_payload.get("cluster_count", 0),
    }


def _run_backup_job(context: JobContext) -> dict[str, object]:
    from paper_galaxy.backup import export_project
    from paper_galaxy.backup.bundle import BackupCancelled

    context.raise_if_interrupted()
    backup_dir = context.project_dir / ".paper-galaxy" / "backups"
    backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup_name = f"paper-galaxy-backup-{context.job.id}.zip"
    try:
        result = export_project(
            project_dir=context.project_dir,
            output_path=backup_dir / backup_name,
            include_vector_indexes=bool(
                context.job.params.get("include_vector_indexes", False)
            ),
            yes=True,
            cancel_requested=context.cancel_requested,
        )
    except BackupCancelled as exc:
        context.raise_if_interrupted()
        raise JobCancelled(str(exc)) from exc
    return {
        "backup_name": backup_name,
        "file_count": len(result.get("files", [])),
        "contains_database": bool(
            dict(result.get("manifest", {})).get("contains_database", False)
        ),
    }


def _required_source(context: JobContext, *, expected_kind: str) -> SourceRecord:
    if context.job.source_id is None:
        raise RuntimeError("Job requires a registered source id.")
    source = get_source(context.project_dir, context.job.source_id)
    if source is None or source.kind != expected_kind:
        raise RuntimeError("Registered source is unavailable or has the wrong kind.")
    return validate_source_locator_for_use(context.project_dir, source)


def _validate_source_for_job(
    project_dir: Path | str,
    *,
    kind: str,
    source_id: str | None,
) -> None:
    expected = {
        "index_corpus": SOURCE_KIND_CORPUS,
        "zotero_sync": SOURCE_KIND_ZOTERO,
    }.get(kind)
    if expected is None:
        if source_id is not None:
            raise ValueError(f"{kind} does not accept a source id.")
        return
    if source_id is None:
        raise ValueError(f"{kind} requires a registered source id.")
    source = get_source(project_dir, source_id)
    if source is None:
        raise ValueError("Registered source was not found.")
    if source.kind != expected:
        raise ValueError("Registered source kind does not match the requested job.")
    validate_source_locator_for_use(project_dir, source)


def _validate_source_row_for_job(
    connection: sqlite3.Connection,
    *,
    kind: str,
    source_id: str | None,
) -> None:
    """Fence source removal inside the same transaction as job insertion."""

    expected = {
        "index_corpus": SOURCE_KIND_CORPUS,
        "zotero_sync": SOURCE_KIND_ZOTERO,
    }.get(kind)
    if expected is None:
        return
    row = connection.execute(
        "SELECT kind, removed_at FROM registered_sources WHERE id = ?",
        (source_id,),
    ).fetchone()
    if row is None or row["removed_at"] is not None:
        raise ValueError("Registered source was removed before the job was queued.")
    if str(row["kind"]) != expected:
        raise ValueError("Registered source kind does not match the requested job.")


def _normalize_params(kind: str, params: Mapping[str, object]) -> dict[str, object]:
    allowed: dict[str, tuple[str, ...]] = {
        "index_corpus": (
            "min_chars",
            "chunk_size",
            "chunk_overlap",
            *_ANALYSIS_FOLLOWUP_FIELDS,
        ),
        "zotero_sync": (
            "include_pdfs",
            "include_notes",
            "include_attachments",
            *_ANALYSIS_FOLLOWUP_FIELDS,
        ),
        "rebuild_analysis": ("name", "seed", "neighbors", "limit"),
        "backup_project": ("include_vector_indexes",),
    }
    extras = set(params) - set(allowed[kind])
    if extras:
        unsupported = ", ".join(sorted(extras))
        raise ValueError(f"Unsupported parameters for {kind}: {unsupported}.")
    normalized = dict(params)
    if kind == "index_corpus":
        _bounded_int(normalized, "min_chars", default=80, minimum=1, maximum=1_000_000)
        _bounded_int(
            normalized,
            "chunk_size",
            default=2000,
            minimum=1,
            maximum=1_000_000,
        )
        _bounded_int(
            normalized,
            "chunk_overlap",
            default=200,
            minimum=0,
            maximum=999_999,
        )
        if cast(int, normalized["chunk_overlap"]) >= cast(
            int, normalized["chunk_size"]
        ):
            raise ValueError("chunk_overlap must be smaller than chunk_size.")
        _normalize_analysis_followup(normalized)
    elif kind == "zotero_sync":
        for name in ("include_pdfs", "include_notes", "include_attachments"):
            value = normalized.get(name, True)
            if not isinstance(value, bool):
                raise ValueError(f"{name} must be a boolean.")
            normalized[name] = value
        _normalize_analysis_followup(normalized)
    elif kind == "rebuild_analysis":
        name = " ".join(str(normalized.get("name", "Automatic local analysis")).split())
        if not name or len(name) > 120:
            raise ValueError("Analysis name must contain 1 to 120 characters.")
        normalized["name"] = name
        _bounded_int(normalized, "seed", default=42, minimum=0, maximum=2**31 - 1)
        _bounded_int(normalized, "neighbors", default=5, minimum=1, maximum=50)
        _bounded_int(normalized, "limit", default=1000, minimum=1, maximum=2_000)
    else:
        value = normalized.get("include_vector_indexes", False)
        if not isinstance(value, bool):
            raise ValueError("include_vector_indexes must be a boolean.")
        normalized["include_vector_indexes"] = value
    return normalized


def _work_params_for_request_key(
    kind: str,
    params: Mapping[str, object],
) -> dict[str, object]:
    if kind not in {"index_corpus", "zotero_sync"}:
        return dict(params)
    return {
        key: value
        for key, value in params.items()
        if key not in _ANALYSIS_FOLLOWUP_FIELDS
    }


def _upgrade_analysis_followup_in_connection(
    connection: sqlite3.Connection,
    *,
    existing: sqlite3.Row,
    incoming_params: Mapping[str, object],
    now: str,
) -> sqlite3.Row:
    if str(existing["kind"]) not in {"index_corpus", "zotero_sync"} or not bool(
        incoming_params.get("rebuild_analysis", False)
    ):
        return existing
    current = load_json_object(existing["params_json"])
    if bool(current.get("rebuild_analysis", False)):
        return existing
    for key in _ANALYSIS_FOLLOWUP_FIELDS:
        current[key] = incoming_params[key]
    connection.execute(
        """
        UPDATE jobs
        SET params_json = ?, updated_at = ?, revision = revision + 1
        WHERE id = ? AND status IN ('queued', 'running', 'cancelling')
        """,
        (_canonical_json(current), now, str(existing["id"])),
    )
    updated = connection.execute(
        "SELECT * FROM jobs WHERE id = ?",
        (str(existing["id"]),),
    ).fetchone()
    if updated is None:
        raise RuntimeError("Active job disappeared during follow-up upgrade.")
    return updated


def _normalize_analysis_followup(values: dict[str, object]) -> None:
    rebuild = values.get("rebuild_analysis", False)
    if not isinstance(rebuild, bool):
        raise ValueError("rebuild_analysis must be a boolean.")
    values["rebuild_analysis"] = rebuild
    _bounded_int(
        values,
        "analysis_seed",
        default=42,
        minimum=0,
        maximum=2**31 - 1,
    )
    _bounded_int(
        values,
        "analysis_neighbors",
        default=5,
        minimum=1,
        maximum=50,
    )
    _bounded_int(
        values,
        "analysis_limit",
        default=1000,
        minimum=1,
        maximum=2_000,
    )


def _enqueue_requested_analysis_in_connection(
    connection: sqlite3.Connection,
    *,
    parent_kind: str,
    parent_params: Mapping[str, object],
    now: str,
) -> bool:
    if parent_kind not in {"index_corpus", "zotero_sync"} or not bool(
        parent_params.get("rebuild_analysis", False)
    ):
        return False
    params = _normalize_params(
        "rebuild_analysis",
        {
            "seed": parent_params.get("analysis_seed", 42),
            "neighbors": parent_params.get("analysis_neighbors", 5),
            "limit": parent_params.get("analysis_limit", 1000),
        },
    )
    request_key = _request_key("rebuild_analysis", None, params)
    existing = connection.execute(
        """
        SELECT 1 FROM jobs
        WHERE request_key = ?
          AND status IN ('queued', 'running', 'cancelling')
        LIMIT 1
        """,
        (request_key,),
    ).fetchone()
    if existing is not None:
        return False
    sequence = int(
        connection.execute(
            "SELECT COALESCE(MAX(queue_sequence), 0) + 1 FROM jobs"
        ).fetchone()[0]
    )
    connection.execute(
        """
        INSERT INTO jobs(
          id, queue_sequence, kind, source_id, request_key, status,
          params_json, created_at, updated_at
        )
        VALUES (?, ?, 'rebuild_analysis', NULL, ?, 'queued', ?, ?, ?)
        """,
        (
            f"job_{uuid4().hex}",
            sequence,
            request_key,
            _canonical_json(params),
            now,
            now,
        ),
    )
    return True


def _bounded_int(
    values: dict[str, object],
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> None:
    value = values.get(name, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer.")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}.")
    values[name] = value


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _request_key(kind: str, source_id: str | None, params: Mapping[str, object]) -> str:
    payload = _canonical_json(
        {
            "version": "paper-galaxy-job-request-v1",
            "kind": kind,
            "source_id": source_id,
            "params": dict(params),
        }
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _canonical_json(value: Mapping[str, object]) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _job_from_row(row: sqlite3.Row) -> JobRecord:
    return JobRecord(
        id=str(row["id"]),
        queue_sequence=int(row["queue_sequence"]),
        kind=str(row["kind"]),
        source_id=str(row["source_id"]) if row["source_id"] is not None else None,
        request_key=str(row["request_key"]),
        status=str(row["status"]),
        params=load_json_object(row["params_json"]),
        current=int(row["progress_current"]),
        total=(
            int(row["progress_total"]) if row["progress_total"] is not None else None
        ),
        message=str(row["message"]),
        result_summary=load_json_object(row["result_summary_json"]),
        error_code=str(row["error_code"]) if row["error_code"] is not None else None,
        error_message=(
            str(row["error_message"]) if row["error_message"] is not None else None
        ),
        cancel_requested=bool(row["cancel_requested"]),
        owner_pid=int(row["owner_pid"]) if row["owner_pid"] is not None else None,
        owner_instance_id=(
            str(row["owner_instance_id"])
            if row["owner_instance_id"] is not None
            else None
        ),
        heartbeat_at=(
            str(row["heartbeat_at"]) if row["heartbeat_at"] is not None else None
        ),
        revision=int(row["revision"]),
        created_at=str(row["created_at"]),
        started_at=str(row["started_at"]) if row["started_at"] is not None else None,
        finished_at=(
            str(row["finished_at"]) if row["finished_at"] is not None else None
        ),
        updated_at=str(row["updated_at"]),
    )


def _safe_message(value: str) -> str:
    return " ".join(value.split())[:_MAX_MESSAGE_LENGTH]


def _safe_result_summary(
    kind: str,
    value: Mapping[str, object],
) -> dict[str, object]:
    allowed = _RESULT_FIELDS.get(kind, frozenset())
    result: dict[str, object] = {}
    for key in sorted(allowed):
        item = value.get(key)
        if isinstance(item, bool):
            result[key] = item
        elif isinstance(item, int):
            result[key] = max(0, item)
        elif isinstance(item, str):
            selected = " ".join(item.split())[:200]
            if (
                selected
                and not PurePosixPath(selected).is_absolute()
                and not PureWindowsPath(selected).is_absolute()
            ):
                result[key] = selected
        elif item is None and key == "run_id":
            result[key] = None
    return result


def _safe_error(error: BaseException) -> tuple[str, str]:
    if isinstance(error, FileNotFoundError):
        return (
            "source_unavailable",
            "A registered local source is unavailable. Verify it in Sources and retry.",
        )
    return (
        type(error).__name__,
        "The local job failed. Run the equivalent CLI command for detailed local "
        "diagnostics, then retry.",
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")
