"""Tests for mcradio.server endpoints using aiohttp test client."""

from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from mcradio.server import RadioServer


@pytest.fixture
def radio_server(config_file: Path, music_dir: Path, client_dir: Path) -> RadioServer:
    server = RadioServer(
        config_path=config_file,
        music_dir=music_dir,
        client_dir=client_dir,
    )
    server.load_config()
    return server


@pytest.fixture
async def client(radio_server: RadioServer):
    async with TestClient(TestServer(radio_server.app)) as c:
        yield c


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


async def test_health(client: TestClient):
    resp = await client.get("/health")
    assert resp.status == 200
    body = await resp.json()
    assert body["status"] == "ok"
    assert body["stations_active"] == 1
    assert body["total_tracks_cached"] == 1
    assert "X-Radio-Protocol" in resp.headers


# ---------------------------------------------------------------------------
# /stations
# ---------------------------------------------------------------------------


async def test_stations(client: TestClient):
    resp = await client.get("/stations")
    assert resp.status == 200
    body = await resp.json()
    assert "stations" in body
    assert len(body["stations"]) == 1
    station = body["stations"][0]
    assert station["id"] == "lofi"
    assert station["name"] == "Lo-Fi Beats"
    assert station["track_count"] == 1


# ---------------------------------------------------------------------------
# /stream/{station_id}
# ---------------------------------------------------------------------------


async def test_stream_returns_chunk(client: TestClient):
    resp = await client.get("/stream/lofi")
    assert resp.status == 200
    body = await resp.read()
    assert len(body) == 16384
    assert resp.headers["X-Station-Active"] == "true"
    assert "X-Track-Position" in resp.headers


async def test_stream_second_chunk(client: TestClient):
    await client.get("/stream/lofi")
    resp = await client.get("/stream/lofi")
    assert resp.status == 200
    body = await resp.read()
    assert len(body) == 16384


async def test_stream_unknown_station(client: TestClient):
    resp = await client.get("/stream/nonexistent")
    assert resp.status == 404


async def test_stream_empty_station(client: TestClient, radio_server: RadioServer):
    radio_server.stations["lofi"].playlist = []
    resp = await client.get("/stream/lofi")
    assert resp.status == 503


# ---------------------------------------------------------------------------
# /now-playing/{station_id}
# ---------------------------------------------------------------------------


async def test_now_playing(client: TestClient):
    resp = await client.get("/now-playing/lofi")
    assert resp.status == 200
    body = await resp.json()
    assert body["station_id"] == "lofi"
    assert body["title"] == "Test Track"
    assert body["artist"] == "Test Artist"


async def test_now_playing_unknown(client: TestClient):
    resp = await client.get("/now-playing/nonexistent")
    assert resp.status == 404


# ---------------------------------------------------------------------------
# /client/{filename}
# ---------------------------------------------------------------------------


async def test_client_file_serves_lua(client: TestClient):
    resp = await client.get("/client/radio.lua")
    assert resp.status == 200
    body = await resp.text()
    assert "test radio client" in body


async def test_client_file_installer(client: TestClient):
    resp = await client.get("/client/installer.lua")
    assert resp.status == 200
    body = await resp.text()
    assert "test installer" in body


async def test_client_file_not_found(client: TestClient):
    resp = await client.get("/client/nonexistent.lua")
    assert resp.status == 404


async def test_client_file_rejects_non_lua(client: TestClient):
    resp = await client.get("/client/secrets.txt")
    assert resp.status == 404


async def test_client_file_rejects_traversal(client: TestClient):
    # aiohttp normalizes ../ at the router level — never reaches handler
    resp = await client.get("/client/../pyproject.toml")
    assert resp.status in (403, 404)
