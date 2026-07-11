"""Local FastAPI server entrypoints for Phase 3."""

from __future__ import annotations

import errno
import ipaddress
import socket
import threading
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from paper_galaxy.errors import MissingDependencyError
from paper_galaxy.logging import get_console
from paper_galaxy.storage.sqlite import resolve_database_path
from paper_galaxy.web.api import WebAppConfig, register_api_routes
from paper_galaxy.web.security import LocalWebSecurity, install_local_security


def create_app(
    project_dir: Path | str,
    *,
    seed: int = 42,
    clusters: int | None = None,
    neighbors: int = 5,
    map_limit: int = 1000,
    job_manager: Any | None = None,
    job_manager_started: bool = False,
) -> Any:
    """Create the local Paper Galaxy FastAPI app."""

    _validate_map_options(
        seed=seed,
        clusters=clusters,
        neighbors=neighbors,
        map_limit=map_limit,
    )

    try:
        from fastapi import FastAPI
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles
    except ImportError as exc:
        raise MissingDependencyError("fastapi") from exc

    resolved_project_dir = Path(project_dir).expanduser().resolve()
    security = LocalWebSecurity.create()
    config = WebAppConfig(
        project_dir=resolved_project_dir,
        seed=seed,
        clusters=clusters,
        neighbors=neighbors,
        map_limit=map_limit,
        write_token=security.write_token,
        job_manager=job_manager,
    )
    static_dir = Path(__file__).parent / "static"

    @asynccontextmanager
    async def lifespan(_app: Any) -> Any:
        if job_manager is not None and not job_manager_started:
            job_manager.start()
        try:
            yield
        finally:
            if job_manager is not None:
                job_manager.stop()

    app = FastAPI(
        title="Paper Galaxy",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    register_api_routes(app, config)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    install_local_security(app, security)

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
    fallback_to_free_port: bool = False,
    job_manager: Any | None = None,
) -> None:
    """Start the local Paper Galaxy web app."""

    try:
        import uvicorn
    except ImportError as exc:
        raise MissingDependencyError("uvicorn") from exc

    if not _is_loopback_host(host):
        raise ValueError(
            "Paper Galaxy only binds to a loopback host by default. Use "
            "127.0.0.1 or ::1; network sharing requires a separately secured "
            "deployment."
        )
    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
        raise ValueError("Port must be an integer between 0 and 65535.")
    if reload and (fallback_to_free_port or port == 0):
        raise ValueError(
            "Automatic port selection is unavailable with development reload."
        )
    _validate_map_options(
        seed=seed,
        clusters=clusters,
        neighbors=neighbors,
        map_limit=map_limit,
    )

    resolved_project = Path(project_dir).expanduser().resolve()
    if job_manager is None and resolve_database_path(resolved_project).is_file():
        from paper_galaxy.services.jobs import JobManager

        job_manager = JobManager(resolved_project)
    console = get_console()
    bound_socket: socket.socket | None = None
    actual_port = port
    used_fallback = False
    if fallback_to_free_port or port == 0:
        bound_socket, actual_port, used_fallback = bind_loopback_socket(host, port)
    manager_started = False
    try:
        if job_manager is not None:
            job_manager.start()
            manager_started = True
        app = create_app(
            resolved_project,
            seed=seed,
            clusters=clusters,
            neighbors=neighbors,
            map_limit=map_limit,
            job_manager=job_manager,
            job_manager_started=manager_started,
        )
        url_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        url = f"http://{url_host}:{actual_port}"
        console.print(f"Serving Paper Galaxy at {url}")
        if used_fallback:
            console.print(
                f"Port {port} was unavailable; selected loopback port {actual_port}."
            )
        if open_browser:
            threading.Thread(
                target=_open_browser_when_ready,
                args=(host, actual_port, url),
                name="paper-galaxy-browser-open",
                daemon=True,
            ).start()
        if bound_socket is None:
            uvicorn.run(
                app,
                host=host,
                port=actual_port,
                reload=reload,
                proxy_headers=False,
                server_header=False,
            )
            return
        config = uvicorn.Config(
            app,
            host=host,
            port=actual_port,
            proxy_headers=False,
            server_header=False,
        )
        uvicorn.Server(config).run(sockets=[bound_socket])
    finally:
        if bound_socket is not None:
            bound_socket.close()
        if manager_started and job_manager is not None:
            job_manager.stop()


def bind_loopback_socket(
    host: str,
    requested_port: int,
) -> tuple[socket.socket, int, bool]:
    """Pre-bind one loopback socket, falling back atomically on port conflicts."""

    bind_host, family = _loopback_bind_address(host)
    if (
        not isinstance(requested_port, int)
        or isinstance(requested_port, bool)
        or not 0 <= requested_port <= 65535
    ):
        raise ValueError("Port must be an integer between 0 and 65535.")
    try:
        bound = _bind_socket(family, bind_host, requested_port)
        return bound, int(bound.getsockname()[1]), False
    except OSError as exc:
        if requested_port == 0 or exc.errno != errno.EADDRINUSE:
            raise
    bound = _bind_socket(family, bind_host, 0)
    return bound, int(bound.getsockname()[1]), True


def _bind_socket(family: socket.AddressFamily, host: str, port: int) -> socket.socket:
    bound = socket.socket(family, socket.SOCK_STREAM)
    try:
        if family == socket.AF_INET6:
            bound.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        bound.bind((host, port))
        bound.listen(128)
        return bound
    except BaseException:
        bound.close()
        raise


def _loopback_bind_address(host: str) -> tuple[str, socket.AddressFamily]:
    if host == "localhost":
        return "127.0.0.1", socket.AF_INET
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError(
            "Host must be a numeric loopback address or localhost."
        ) from exc
    if not address.is_loopback:
        raise ValueError("Host must be a loopback address.")
    family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
    return str(address), family


def _open_browser_when_ready(host: str, port: int, url: str) -> None:
    connect_host, _family = _loopback_bind_address(host)
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((connect_host, port), timeout=0.25):
                pass
        except OSError:
            threading.Event().wait(0.05)
            continue
        webbrowser.open(url)
        return


def _is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _validate_map_options(
    *,
    seed: int,
    clusters: int | None,
    neighbors: int,
    map_limit: int,
) -> None:
    if (
        not isinstance(seed, int)
        or isinstance(seed, bool)
        or not 0 <= seed <= 2**31 - 1
    ):
        raise ValueError("Seed must be an integer between 0 and 2147483647.")
    if clusters is not None and (
        not isinstance(clusters, int)
        or isinstance(clusters, bool)
        or not 1 <= clusters <= 200
    ):
        raise ValueError("Clusters must be an integer between 1 and 200.")
    if (
        not isinstance(neighbors, int)
        or isinstance(neighbors, bool)
        or not 1 <= neighbors <= 50
    ):
        raise ValueError("Neighbors must be an integer between 1 and 50.")
    if (
        not isinstance(map_limit, int)
        or isinstance(map_limit, bool)
        or not 1 <= map_limit <= 2_000
    ):
        raise ValueError("Map limit must be an integer between 1 and 2000.")
