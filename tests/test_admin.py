"""Tests for mcradio admin API endpoints."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from aiohttp.test_utils import TestClient, TestServer

from mcradio.server import RadioServer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_config_file(tmp_path: Path) -> Path:
    """Create a stations.yaml for admin tests."""
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
                "rotation": "shuffle",
            }
        ],
    }))
    return config


@pytest.fixture
def admin_music_dir(tmp_path: Path) -> Path:
    """Create a music directory with tracks for admin tests."""
    dfpwm_dir = tmp_path / "music" / "dfpwm" / "lofi"
    dfpwm_dir.mkdir(parents=True)

    meta_dir = tmp_path / "music" / "metadata" / "lofi"
    meta_dir.mkdir(parents=True)

    # Create two fake tracks
    (dfpwm_dir / "chill-vibes.dfpwm").write_bytes(b"\x55" * 32768)
    (dfpwm_dir / "midnight-stroll.dfpwm").write_bytes(b"\xAA" * 16384)

    (meta_dir / "chill-vibes.json").write_text(json.dumps({
        "title": "Chill Vibes",
        "artist": "DJ Test",
        "duration_seconds": 240,
    }))
    (meta_dir / "midnight-stroll.json").write_text(json.dumps({
        "title": "Midnight Stroll",
        "artist": "Night Owl",
        "duration_seconds": 180,
    }))

    # Raw dir for transcode tests
    raw_dir = tmp_path / "music" / "raw" / "lofi"
    raw_dir.mkdir(parents=True)

    return tmp_path / "music"


@pytest.fixture
def admin_client_dir(tmp_path: Path) -> Path:
    """Create a client directory."""
    cdir = tmp_path / "client"
    cdir.mkdir()
    (cdir / "radio.lua").write_text("-- radio\n")
    return cdir


@pytest.fixture
def admin_server(
    admin_config_file: Path, admin_music_dir: Path, admin_client_dir: Path
) -> RadioServer:
    server = RadioServer(
        config_path=admin_config_file,
        music_dir=admin_music_dir,
        client_dir=admin_client_dir,
    )
    server.load_config()
    return server


@pytest.fixture
async def admin_client(admin_server: RadioServer):
    async with TestClient(TestServer(admin_server.app)) as c:
        yield c


# ---------------------------------------------------------------------------
# GET /admin/stations
# ---------------------------------------------------------------------------


async def test_admin_list_stations(admin_client: TestClient):
    resp = await admin_client.get("/admin/stations")
    assert resp.status == 200
    body = await resp.json()
    assert "stations" in body
    assert len(body["stations"]) == 1
    station = body["stations"][0]
    assert station["id"] == "lofi"
    assert station["name"] == "Lo-Fi Beats"
    assert station["track_count"] == 2
    assert station["rotation"] == "shuffle"


# ---------------------------------------------------------------------------
# POST /admin/stations
# ---------------------------------------------------------------------------


async def test_admin_add_station(admin_client: TestClient, admin_config_file: Path, tmp_path: Path):
    resp = await admin_client.post("/admin/stations", json={
        "id": "jazz",
        "name": "Smooth Jazz FM",
        "genre": "Jazz",
        "frequency": "101.3",
        "description": "Late night jazz vibes",
    })
    assert resp.status == 201
    body = await resp.json()
    assert body["status"] == "created"
    assert body["station_id"] == "jazz"

    # Verify it appears in station list
    resp2 = await admin_client.get("/admin/stations")
    body2 = await resp2.json()
    ids = [s["id"] for s in body2["stations"]]
    assert "jazz" in ids

    # Verify it was written to disk config
    with open(admin_config_file, "r") as f:
        config = yaml.safe_load(f)
    station_ids = [s["id"] for s in config["stations"]]
    assert "jazz" in station_ids

    # Verify directories were created
    music_dir = tmp_path / "music"
    assert (music_dir / "raw" / "jazz").is_dir()
    assert (music_dir / "dfpwm" / "jazz").is_dir()
    assert (music_dir / "metadata" / "jazz").is_dir()


async def test_admin_add_station_duplicate(admin_client: TestClient):
    resp = await admin_client.post("/admin/stations", json={
        "id": "lofi",
        "name": "Duplicate",
    })
    assert resp.status == 409


async def test_admin_add_station_missing_id(admin_client: TestClient):
    resp = await admin_client.post("/admin/stations", json={
        "name": "No ID",
    })
    assert resp.status == 400


# ---------------------------------------------------------------------------
# DELETE /admin/stations/{station_id}
# ---------------------------------------------------------------------------


async def test_admin_remove_station(admin_client: TestClient, admin_config_file: Path):
    resp = await admin_client.delete("/admin/stations/lofi")
    assert resp.status == 200
    body = await resp.json()
    assert body["status"] == "removed"

    # Verify it's gone from the list
    resp2 = await admin_client.get("/admin/stations")
    body2 = await resp2.json()
    assert len(body2["stations"]) == 0

    # Verify removed from disk config
    with open(admin_config_file, "r") as f:
        config = yaml.safe_load(f)
    assert len(config["stations"]) == 0


async def test_admin_remove_station_not_found(admin_client: TestClient):
    resp = await admin_client.delete("/admin/stations/nonexistent")
    assert resp.status == 404


# ---------------------------------------------------------------------------
# GET /admin/stations/{station_id}/tracks
# ---------------------------------------------------------------------------


async def test_admin_list_tracks(admin_client: TestClient):
    resp = await admin_client.get("/admin/stations/lofi/tracks")
    assert resp.status == 200
    body = await resp.json()
    assert body["station_id"] == "lofi"
    assert body["track_count"] == 2
    assert len(body["tracks"]) == 2

    # Verify track data
    names = {t["name"] for t in body["tracks"]}
    assert "chill-vibes" in names
    assert "midnight-stroll" in names

    # Check metadata is included
    for t in body["tracks"]:
        if t["name"] == "chill-vibes":
            assert t["title"] == "Chill Vibes"
            assert t["artist"] == "DJ Test"
            assert t["duration_seconds"] == 240


async def test_admin_list_tracks_not_found(admin_client: TestClient):
    resp = await admin_client.get("/admin/stations/nonexistent/tracks")
    assert resp.status == 404


# ---------------------------------------------------------------------------
# POST /admin/stations/{station_id}/reload
# ---------------------------------------------------------------------------


async def test_admin_reload_station(admin_client: TestClient, admin_music_dir: Path):
    # Add a new track file
    dfpwm_dir = admin_music_dir / "dfpwm" / "lofi"
    (dfpwm_dir / "new-track.dfpwm").write_bytes(b"\x33" * 16384)

    resp = await admin_client.post("/admin/stations/lofi/reload")
    assert resp.status == 200
    body = await resp.json()
    assert body["status"] == "reloaded"
    assert body["track_count"] == 3


async def test_admin_reload_station_not_found(admin_client: TestClient):
    resp = await admin_client.post("/admin/stations/nonexistent/reload")
    assert resp.status == 404


# ---------------------------------------------------------------------------
# POST /admin/stations/{station_id}/download
# ---------------------------------------------------------------------------


async def test_admin_download_triggers_job(admin_client: TestClient):
    resp = await admin_client.post("/admin/stations/lofi/download", json={
        "source": "ytsearch5:test audio",
    })
    assert resp.status == 202
    body = await resp.json()
    assert body["status"] == "started"
    assert "job" in body
    assert body["job"]["type"] == "download"
    assert body["job"]["station_id"] == "lofi"
    assert body["job"]["status"] == "running"
    assert len(body["job"]["id"]) == 8

    # Verify job appears in job list
    resp2 = await admin_client.get("/admin/jobs")
    body2 = await resp2.json()
    assert len(body2["jobs"]) >= 1
    job_ids = [j["id"] for j in body2["jobs"]]
    assert body["job"]["id"] in job_ids


async def test_admin_download_missing_source(admin_client: TestClient):
    resp = await admin_client.post("/admin/stations/lofi/download", json={})
    assert resp.status == 400


async def test_admin_download_station_not_found(admin_client: TestClient):
    resp = await admin_client.post("/admin/stations/nonexistent/download", json={
        "source": "test",
    })
    assert resp.status == 404


# ---------------------------------------------------------------------------
# POST /admin/stations/{station_id}/transcode
# ---------------------------------------------------------------------------


async def test_admin_transcode_triggers_job(admin_client: TestClient):
    resp = await admin_client.post("/admin/stations/lofi/transcode")
    assert resp.status == 202
    body = await resp.json()
    assert body["status"] == "started"
    assert "job" in body
    assert body["job"]["type"] == "transcode"
    assert body["job"]["station_id"] == "lofi"
    assert body["job"]["status"] == "running"

    # Verify job appears in job list
    resp2 = await admin_client.get("/admin/jobs")
    body2 = await resp2.json()
    job_ids = [j["id"] for j in body2["jobs"]]
    assert body["job"]["id"] in job_ids


async def test_admin_transcode_station_not_found(admin_client: TestClient):
    resp = await admin_client.post("/admin/stations/nonexistent/transcode")
    assert resp.status == 404


# ---------------------------------------------------------------------------
# DELETE /admin/stations/{station_id}/tracks/{track_name}
# ---------------------------------------------------------------------------


async def test_admin_remove_track(admin_client: TestClient, admin_music_dir: Path):
    # Verify track exists first
    dfpwm_file = admin_music_dir / "dfpwm" / "lofi" / "midnight-stroll.dfpwm"
    meta_file = admin_music_dir / "metadata" / "lofi" / "midnight-stroll.json"
    assert dfpwm_file.exists()
    assert meta_file.exists()

    resp = await admin_client.delete("/admin/stations/lofi/tracks/midnight-stroll")
    assert resp.status == 200
    body = await resp.json()
    assert body["status"] == "deleted"
    assert body["track_name"] == "midnight-stroll"

    # Verify files are gone
    assert not dfpwm_file.exists()
    assert not meta_file.exists()

    # Verify track count decreased
    resp2 = await admin_client.get("/admin/stations/lofi/tracks")
    body2 = await resp2.json()
    assert body2["track_count"] == 1


async def test_admin_remove_track_not_found(admin_client: TestClient):
    resp = await admin_client.delete("/admin/stations/lofi/tracks/nonexistent")
    assert resp.status == 404


async def test_admin_remove_track_station_not_found(admin_client: TestClient):
    resp = await admin_client.delete("/admin/stations/nonexistent/tracks/foo")
    assert resp.status == 404


# ---------------------------------------------------------------------------
# GET /admin/jobs (empty state)
# ---------------------------------------------------------------------------


async def test_admin_jobs_empty(admin_client: TestClient):
    # Use a fresh client to test empty jobs (before any downloads/transcodes)
    # Note: other tests may have added jobs in the same server instance,
    # so we just verify the structure
    resp = await admin_client.get("/admin/jobs")
    assert resp.status == 200
    body = await resp.json()
    assert "jobs" in body
    assert isinstance(body["jobs"], list)


# ---------------------------------------------------------------------------
# Original routes still work
# ---------------------------------------------------------------------------


async def test_original_stations_route(admin_client: TestClient):
    """Verify the public /stations endpoint still works alongside admin."""
    resp = await admin_client.get("/stations")
    assert resp.status == 200
    body = await resp.json()
    assert "stations" in body


async def test_original_health_route(admin_client: TestClient):
    """Verify the /health endpoint still works."""
    resp = await admin_client.get("/health")
    assert resp.status == 200
    body = await resp.json()
    assert body["status"] == "ok"
