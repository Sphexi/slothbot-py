"""Filesystem locations shared by the bot and the container healthcheck (stdlib only)."""

from pathlib import Path

HEARTBEAT_NAME = "heartbeat"


def heartbeat_path(data: Path, override: Path | None) -> Path:
    """Heartbeat file location: the explicit override, else inside the data directory."""
    return override if override is not None else data / HEARTBEAT_NAME
