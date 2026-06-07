"""Tests for mcradio.config module."""

from __future__ import annotations

import os
from pathlib import Path

from mcradio.config import get_config_path, get_music_dir, load_stations_config


def test_get_music_dir_default(monkeypatch):
    monkeypatch.delenv("MCRADIO_MUSIC_DIR", raising=False)
    assert get_music_dir() == Path("/opt/icecast/music")


def test_get_music_dir_env(monkeypatch):
    monkeypatch.setenv("MCRADIO_MUSIC_DIR", "/tmp/test-music")
    assert get_music_dir() == Path("/tmp/test-music")


def test_get_config_path_default(monkeypatch):
    monkeypatch.delenv("MCRADIO_CONFIG", raising=False)
    assert get_config_path() == Path("/opt/icecast/stations.yaml")


def test_get_config_path_env(monkeypatch):
    monkeypatch.setenv("MCRADIO_CONFIG", "/tmp/my-config.yaml")
    assert get_config_path() == Path("/tmp/my-config.yaml")


def test_load_stations_config(config_file: Path):
    data = load_stations_config(config_file)
    assert "stations" in data
    assert len(data["stations"]) == 1
    assert data["stations"][0]["id"] == "lofi"


def test_load_stations_config_missing():
    data = load_stations_config(Path("/nonexistent/path.yaml"))
    assert data == {}
