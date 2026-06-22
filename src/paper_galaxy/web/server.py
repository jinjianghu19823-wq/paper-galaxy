"""Local FastAPI server entrypoints for Phase 3."""

from __future__ import annotations

import ipaddress
import webbrowser
from pathlib import Path
from typing import Any

from paper_galaxy.errors import MissingDependencyError
from paper_galaxy.logging import get_console
from paper_galaxy.web.api import WebAppConfig, register_api_routes


def create_app(
    project_dir: Path | str,
    *,
    seed: int = 42,
    clusters: int | None = None,
    neighbors: int = 5,
    map_limit: int = 1000,
) -> Any:
    """Create the local Paper Galaxy FastAPI app."""

    try:
        from fastapi import FastAPI
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles
    except ImportError as exc:
        raise MissingDependencyError("fastapi") from exc

    resolved_project_dir = Path(project_dir).expanduser().resolve()
    config = WebAppConfig(
        project_dir=resolved_project_dir,
        seed=seed,
        clusters=clusters,
        neighbors=neighbors,
        map_limit=map_limit,
    )
    static_dir = Path(__file__).parent / "static"
    app = FastAPI(title="Paper Galaxy", docs_url=None, redoc_url=None)
    register_api_routes(app, config)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    return app


def serve_app(
    *,
    project_dir: Path | str,
    host: str = "127.0.0.1",
    port: int = 8765,
    reload: bool = False,
    open_browser: bool = False,
    seed: int = 42,
    clusters: int | None = None,
    neighbors: int = 5,
    map_limit: int = 1000,
) -> None:
    """Start the local Paper Galaxy web app."""

    try:
        import uvicorn
    except ImportError as exc:
        raise MissingDependencyError("uvicorn") from exc

    app = create_app(
        project_dir,
        seed=seed,
        clusters=clusters,
        neighbors=neighbors,
        map_limit=map_limit,
    )
    console = get_console()
    url = f"http://{host}:{port}"
    console.print(f"Serving Paper Galaxy at {url}")
    if not _is_loopback_host(host):
        console.print(
            "Warning: non-loopback host selected. The app may be reachable "
            "from other devices on your local network."
        )
    if open_browser:
        webbrowser.open(url)
    uvicorn.run(app, host=host, port=port, reload=reload)


def _is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
