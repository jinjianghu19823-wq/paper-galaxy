"""Best-effort Zotero Desktop detection."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from paper_galaxy.zotero.local_api import (
    DEFAULT_LOCAL_API_URL,
    LocalZoteroAPIClient,
    ZoteroAPIError,
)
from paper_galaxy.zotero.models import ZoteroDetection
from paper_galaxy.zotero.sqlite_fallback import inspect_zotero_sqlite


def detect_zotero(
    *,
    api_url: str = DEFAULT_LOCAL_API_URL,
    data_dir: Path | None = None,
    timeout: float = 2.0,
) -> ZoteroDetection:
    """Detect local Zotero API and likely data directory without writing to Zotero."""

    api_reachable = False
    api_error: str | None = None
    client = LocalZoteroAPIClient(api_url, timeout=timeout)
    try:
        client.root()
        api_reachable = True
    except ZoteroAPIError as exc:
        api_error = str(exc)

    detected_data_dir = (data_dir.expanduser().resolve() if data_dir else None) or (
        _first_existing_data_dir() or _default_data_dir_guess()
    )
    database_path = detected_data_dir / "zotero.sqlite"
    storage_path = detected_data_dir / "storage"
    sqlite_diagnostics = (
        inspect_zotero_sqlite(database_path) if database_path.exists() else {}
    )
    return ZoteroDetection(
        api_url=api_url,
        api_reachable=api_reachable,
        api_error=api_error,
        data_dir=detected_data_dir,
        database_exists=database_path.exists(),
        storage_exists=storage_path.is_dir(),
        sqlite_diagnostics=sqlite_diagnostics,
    )


def default_data_dir_guesses() -> list[Path]:
    """Return platform-appropriate Zotero data directory guesses."""

    home = Path.home()
    guesses = [home / "Zotero"]
    if sys.platform == "win32":
        userprofile = os.environ.get("USERPROFILE")
        if userprofile:
            guesses.insert(0, Path(userprofile) / "Zotero")
    return guesses


def detection_payload(detection: ZoteroDetection) -> dict[str, object]:
    """Return JSON-safe detection details."""

    recommended = "paper-galaxy zotero status"
    if not detection.api_reachable:
        recommended = "Open Zotero Desktop, then run: paper-galaxy zotero status"
    if not detection.storage_exists:
        recommended += " --data-dir /path/to/Zotero"
    return {
        "api_url": detection.api_url,
        "api_reachable": detection.api_reachable,
        "api_error": detection.api_error,
        "data_dir": str(detection.data_dir) if detection.data_dir else None,
        "database_exists": detection.database_exists,
        "storage_exists": detection.storage_exists,
        "sqlite_diagnostics": detection.sqlite_diagnostics,
        "note": (
            "Best effort only; Zotero Settings -> Advanced -> Files and "
            "Folders -> Show Data Directory is authoritative."
        ),
        "recommended_next_command": recommended,
    }


def _first_existing_data_dir() -> Path | None:
    for guess in default_data_dir_guesses():
        if guess.exists():
            return guess.expanduser().resolve()
    return None


def _default_data_dir_guess() -> Path:
    return default_data_dir_guesses()[0].expanduser()
