"""Shared fixtures for mcradio tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def music_dir(tmp_path: Path) -> Path:
    """Create a minimal music directory with one DFPWM track."""
    dfpwm_dir = tmp_path / "music" / "dfpwm" / "lofi"
    dfpwm_dir.mkdir(parents=True)

    meta_dir = tmp_path / "music" / "metadata" / "lofi"
    meta_dir.mkdir(parents=True)

    # Fake DFPWM file: 32KB of non-zero data (two chunks worth)
    track = dfpwm_dir / "test-track.dfpwm"
    track.write_bytes(b"\x55" * 32768)

    # Metadata JSON
    meta = meta_dir / "test-track.json"
    meta.write_text(json.dumps({
        "title": "Test Track",
        "artist": "Test Artist",
        "duration_seconds": 180,
    }))

    return tmp_path / "music"


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    """Create a minimal stations.yaml."""
    config = tmp_path / "stations.yaml"
    config.write_text(yaml.dump({
        "server": {
            "host": "127.0.0.1",
            "port": 5309,
            "music_dir": str(tmp_path / "music"),
        },
        "stations": [
            {
                "id": "lofi",
                "name": "Lo-Fi Beats",
                "genre": "Electronic",
                "frequency": "98.7",
                "description": "Chill beats to mine to",
            }
        ],
    }))
    return config


@pytest.fixture
def client_dir(tmp_path: Path) -> Path:
    """Create a client directory with test Lua files."""
    cdir = tmp_path / "client"
    cdir.mkdir()
    (cdir / "radio.lua").write_text("-- test radio client\nprint('hello')\n")
    (cdir / "installer.lua").write_text("-- test installer\nprint('install')\n")
    return cdir
