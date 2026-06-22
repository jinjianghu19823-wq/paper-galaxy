"""Console and logging helpers."""

import logging as std_logging

from rich.console import Console


def get_console() -> Console:
    """Create a Rich console for CLI output."""

    return Console()


def configure_logging(level: int = std_logging.INFO) -> None:
    """Configure standard library logging for future modules."""

    std_logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
    )
