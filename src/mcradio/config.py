"""Configuration loader for mcradio."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Defaults (overridable via environment)
# ---------------------------------------------------------------------------

DEFAULT_MUSIC_DIR = "/opt/icecast/music"
DEFAULT_CONFIG_PATH = "/opt/icecast/stations.yaml"
DEFAULT_CLIENT_DIR = "/opt/icecast/client"
DEFAULT_LOG_DIR = "/opt/icecast/log"


def get_music_dir() -> Path:
    """Return the music directory, respecting MCRADIO_MUSIC_DIR env var."""
    return Path(os.environ.get("MCRADIO_MUSIC_DIR", DEFAULT_MUSIC_DIR))


def get_config_path() -> Path:
    """Return the config file path, respecting MCRADIO_CONFIG env var."""
    return Path(os.environ.get("MCRADIO_CONFIG", DEFAULT_CONFIG_PATH))


def get_client_dir() -> Path:
    """Return the client directory for serving Lua files."""
    return Path(os.environ.get("MCRADIO_CLIENT_DIR", DEFAULT_CLIENT_DIR))


def get_log_dir() -> Path:
    """Return the log directory."""
    return Path(os.environ.get("MCRADIO_LOG_DIR", DEFAULT_LOG_DIR))


def load_stations_config(config_path: Path | None = None) -> dict[str, Any]:
    """Load and return the stations.yaml configuration.

    Args:
        config_path: Explicit path to config file. Falls back to env/default.

    Returns:
        Parsed YAML dict. Empty dict on missing/invalid file.
    """
    path = config_path or get_config_path()

    if not path.is_file():
        return {}

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        return {}

    return data
