#!/usr/bin/env python3
"""Smoke-test an installed Paper Galaxy wheel through its loopback workspace."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import re
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

_URL_PATTERN = re.compile(r"Serving Paper Galaxy at (http://[^\s]+)")
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def main() -> int:
    args = _parse_args()
    executable = args.executable.expanduser().resolve()
    corpus = args.corpus.expanduser().resolve()
    if not executable.is_file():
        raise SystemExit(f"Installed paper-galaxy executable not found: {executable}")
    if not corpus.is_dir():
        raise SystemExit(f"Synthetic corpus directory not found: {corpus}")

    source_before = _tree_digest(corpus)
    with tempfile.TemporaryDirectory(prefix="paper-galaxy-wheel-smoke-") as raw:
        project = Path(raw) / "project"
        command = [
            str(executable),
            "launch",
            "--project-dir",
            str(project),
            "--corpus",
            str(corpus),
            "--no-open",
            "--port",
            "0",
        ]
        environment = dict(os.environ)
        environment["PYTHONUNBUFFERED"] = "1"
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        output: list[str] = []
        lines: queue.Queue[str] = queue.Queue()
        reader = threading.Thread(
            target=_read_lines,
            args=(process, lines, output),
            daemon=True,
        )
        reader.start()
        try:
            base_url = _wait_for_url(
                process,
                lines,
                output,
                timeout=args.timeout,
            )
            health = _wait_for_json(base_url, "/api/health", timeout=args.timeout)
            jobs = _wait_for_jobs(base_url, timeout=args.timeout)
            sources = _get_json(base_url, "/api/sources?limit=100&offset=0")
            search = _get_json(
                base_url,
                f"/api/search?{urlencode({'q': 'operator', 'limit': 5})}",
            )
            map_payload = _get_json(base_url, "/api/map?limit=100")
        finally:
            _shutdown(process, timeout=15.0)
            reader.join(timeout=2.0)

        source_after = _tree_digest(corpus)
        if source_after != source_before:
            raise RuntimeError("Installed launch smoke modified the source corpus.")
        _assert_smoke_contract(
            health=health,
            jobs=jobs,
            sources=sources,
            search=search,
            map_payload=map_payload,
            process=process,
            output=output,
        )
        result = {
            "status": "passed",
            "health": health,
            "source_count": len(sources.get("sources", [])),
            "job_statuses": [job.get("status") for job in jobs],
            "job_kinds": sorted(
                str(job.get("kind")) for job in jobs if job.get("kind")
            ),
            "search_result_count": len(search.get("results", [])),
            "map_document_count": len(map_payload.get("documents", [])),
            "source_unchanged": True,
            "process_exit_code": process.returncode,
        }
        if args.json_out is not None:
            destination = args.json_out.expanduser().resolve()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        print(json.dumps(result, sort_keys=True))
        return 0


def _assert_smoke_contract(
    *,
    health: dict[str, Any],
    jobs: list[dict[str, Any]],
    sources: dict[str, Any],
    search: dict[str, Any],
    map_payload: dict[str, Any],
    process: subprocess.Popen[str],
    output: list[str],
) -> None:
    if health.get("status") != "ok":
        raise RuntimeError("Installed launch health endpoint did not report ok.")
    if health.get("database_exists") is not True:
        raise RuntimeError("Installed launch did not create its project database.")
    if health.get("project_configured") is not True:
        raise RuntimeError("Installed launch did not create project configuration.")
    source_rows = sources.get("sources")
    if not isinstance(source_rows, list) or len(source_rows) != 1:
        raise RuntimeError("Installed launch did not register exactly one source.")
    completed_kinds = {
        str(job.get("kind")) for job in jobs if job.get("status") == "completed"
    }
    required_kinds = {"index_corpus", "rebuild_analysis"}
    if not required_kinds <= completed_kinds:
        raise RuntimeError("Installed launch did not complete index and analysis jobs.")
    results = search.get("results")
    if not isinstance(results, list) or not results:
        raise RuntimeError("Installed launch search returned no synthetic result.")
    documents = map_payload.get("documents")
    if not isinstance(documents, list) or not documents:
        raise RuntimeError("Installed launch map returned no synthetic document.")
    if process.returncode != 0:
        tail = "".join(output[-12:]).strip()
        raise RuntimeError(
            f"Installed launch did not shut down cleanly ({process.returncode}): {tail}"
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, default=Path("examples/tiny_corpus"))
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--json-out", type=Path)
    return parser.parse_args()


def _read_lines(
    process: subprocess.Popen[str],
    lines: queue.Queue[str],
    output: list[str],
) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        output.append(line)
        lines.put(line)


def _wait_for_url(
    process: subprocess.Popen[str],
    lines: queue.Queue[str],
    output: list[str],
    *,
    timeout: float,
) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            tail = "".join(output[-12:]).strip()
            raise RuntimeError(
                f"launch exited before startup with code {process.returncode}: {tail}"
            )
        try:
            line = lines.get(timeout=min(0.25, max(0.01, deadline - time.monotonic())))
        except queue.Empty:
            continue
        match = _URL_PATTERN.search(_ANSI_ESCAPE.sub("", line))
        if match:
            return match.group(1).rstrip("/")
    raise TimeoutError("Timed out waiting for the loopback launch URL.")


def _wait_for_json(base_url: str, path: str, *, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return _get_json(base_url, path)
        except Exception as exc:
            last_error = exc
            time.sleep(0.1)
    raise TimeoutError(f"Timed out waiting for {path}: {last_error}")


def _wait_for_jobs(base_url: str, *, timeout: float) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = _get_json(base_url, "/api/jobs?limit=100&offset=0")
        jobs = [job for job in payload.get("jobs", []) if isinstance(job, dict)]
        if jobs and not any(
            job.get("status") in {"queued", "running", "cancelling"} for job in jobs
        ):
            failures = [job for job in jobs if job.get("status") != "completed"]
            if failures:
                raise RuntimeError(f"Local launch jobs did not complete: {failures}")
            return jobs
        time.sleep(0.1)
    raise TimeoutError("Timed out waiting for local launch jobs.")


def _get_json(base_url: str, path: str) -> dict[str, Any]:
    request = Request(f"{base_url}{path}", headers={"Accept": "application/json"})
    with urlopen(request, timeout=3.0) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Loopback API returned a non-object payload.")
    return payload


def _shutdown(process: subprocess.Popen[str], *, timeout: float) -> None:
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5.0)


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        if path.is_symlink():
            digest.update(b"symlink")
            digest.update(os.readlink(path).encode("utf-8"))
        elif path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
