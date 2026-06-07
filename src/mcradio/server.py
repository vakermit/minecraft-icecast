"""
Minecraft CC:Tweaked Radio Server
Serves pre-transcoded DFPWM audio as 16KB chunks over HTTP.
Radio model: all listeners on a station share the same playback position.

Port 5309 ("Jenny") — localhost only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import signal
import sys
import time
from pathlib import Path
from typing import Any

import yaml
from aiohttp import web

from mcradio.config import get_client_dir, get_config_path, get_music_dir

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CHUNK_SIZE: int = 16384  # 16KB — exactly one CC:Tweaked speaker buffer
PROTOCOL_VERSION: int = 1

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("radio")


# ---------------------------------------------------------------------------
# Station State
# ---------------------------------------------------------------------------


class StationState:
    """Holds playback state for a single radio station.

    All listeners on the same station share position — radio model.
    The station advances its position every time a chunk is served.
    """

    def __init__(
        self,
        station_id: str,
        name: str,
        genre: str,
        frequency: str,
        description: str,
        dfpwm_dir: Path,
        rotation: str = "sequential",
    ) -> None:
        self.station_id: str = station_id
        self.name: str = name
        self.genre: str = genre
        self.frequency: str = frequency
        self.description: str = description
        self.dfpwm_dir: Path = dfpwm_dir
        self.rotation: str = rotation  # "shuffle" or "sequential"

        # Playlist: sorted list of .dfpwm files in the station directory
        self.playlist: list[Path] = self._scan_playlist()
        self.current_track: int = 0
        self.current_offset: int = 0
        self.listener_count: int = 0

        # File handle for the currently open track (opened lazily)
        self._file_handle: Any = None

        # Apply initial shuffle if rotation mode is shuffle
        if self.rotation == "shuffle" and self.playlist:
            random.shuffle(self.playlist)

    def _scan_playlist(self) -> list[Path]:
        """Scan the station's DFPWM directory for tracks, sorted by name."""
        if not self.dfpwm_dir.is_dir():
            return []
        tracks = sorted(self.dfpwm_dir.glob("*.dfpwm"))
        return tracks

    def reload_playlist(self) -> None:
        """Rescan tracks from disk and rebuild the playlist."""
        self._close_file()
        self.playlist = self._scan_playlist()
        if self.rotation == "shuffle" and self.playlist:
            random.shuffle(self.playlist)
        # Reset position to avoid out-of-bounds
        self.current_track = 0
        self.current_offset = 0

    @property
    def has_tracks(self) -> bool:
        return len(self.playlist) > 0

    @property
    def track_count(self) -> int:
        return len(self.playlist)

    @property
    def current_track_path(self) -> Path | None:
        if not self.has_tracks:
            return None
        return self.playlist[self.current_track % len(self.playlist)]

    @property
    def current_track_stem(self) -> str | None:
        path = self.current_track_path
        if path is None:
            return None
        return path.stem

    @property
    def track_position_percent(self) -> int:
        """Percentage through the current track (0-100)."""
        path = self.current_track_path
        if path is None:
            return 0
        try:
            file_size = path.stat().st_size
        except OSError:
            return 0
        if file_size == 0:
            return 0
        return min(100, int((self.current_offset / file_size) * 100))

    def _open_current_track(self) -> bool:
        """Open the current track file. Returns True on success."""
        self._close_file()
        path = self.current_track_path
        if path is None:
            return False
        try:
            self._file_handle = open(path, "rb")
            if self.current_offset > 0:
                self._file_handle.seek(self.current_offset)
            return True
        except OSError as e:
            logger.error("Failed to open track %s: %s", path, e)
            return False

    def _close_file(self) -> None:
        if self._file_handle is not None:
            try:
                self._file_handle.close()
            except OSError:
                pass
            self._file_handle = None

    def _advance_track(self) -> None:
        """Move to the next track in the playlist. Wraps to start.

        If rotation is 'shuffle', reshuffle when wrapping around.
        """
        self._close_file()
        self.current_offset = 0
        if not self.has_tracks:
            return

        next_idx = (self.current_track + 1) % len(self.playlist)

        # If we wrapped around and rotation is shuffle, reshuffle
        if next_idx == 0 and self.rotation == "shuffle":
            random.shuffle(self.playlist)

        self.current_track = next_idx
        logger.info(
            "Station '%s' advanced to track %d/%d: %s",
            self.station_id,
            self.current_track + 1,
            len(self.playlist),
            self.playlist[self.current_track].name,
        )

    def read_chunk(self) -> bytes:
        """Read the next CHUNK_SIZE bytes from the current track.

        Returns exactly CHUNK_SIZE bytes — pads with zeros (silence) if at EOF.
        Advances to the next track when the current one is exhausted.
        """
        if not self.has_tracks:
            return b"\x00" * CHUNK_SIZE

        # Ensure file is open
        if self._file_handle is None:
            if not self._open_current_track():
                # Cannot open file — try next track
                self._advance_track()
                if not self._open_current_track():
                    # Still cannot open — return silence
                    return b"\x00" * CHUNK_SIZE

        data = self._file_handle.read(CHUNK_SIZE)

        if data is None:
            data = b""

        if len(data) < CHUNK_SIZE:
            # EOF reached — pad remainder with silence, advance track
            padding = b"\x00" * (CHUNK_SIZE - len(data))
            chunk = data + padding
            self._advance_track()
            # Pre-open next track for the next request
            self._open_current_track()
            self.current_offset = 0
            return chunk

        # Normal case: full chunk read
        self.current_offset += len(data)
        return data

    def close(self) -> None:
        """Clean up file handles."""
        self._close_file()


# ---------------------------------------------------------------------------
# Radio Server
# ---------------------------------------------------------------------------


class RadioServer:
    """HTTP server that serves DFPWM audio chunks to CC:Tweaked clients."""

    def __init__(
        self,
        config_path: Path | None = None,
        music_dir: Path | None = None,
        client_dir: Path | None = None,
    ) -> None:
        self.config_path: Path = config_path or get_config_path()
        self._music_dir_override: Path | None = music_dir
        self._client_dir: Path = client_dir or get_client_dir()
        self.config: dict[str, Any] = {}
        self.stations: dict[str, StationState] = {}
        self.start_time: float = 0.0
        self._music_dir: Path = Path(".")  # Set properly in load_config

        # Background job manager
        from mcradio.worker import JobManager

        self.job_manager: JobManager = JobManager(
            reload_station_callback=self.reload_station
        )

        self.app: web.Application = web.Application()
        self._setup_routes()

    @property
    def music_dir(self) -> Path:
        """Public accessor for the resolved music directory."""
        return self._music_dir

    def _setup_routes(self) -> None:
        self.app.router.add_get("/stations", self._handle_stations)
        self.app.router.add_get("/stream/{station_id}", self._handle_stream)
        self.app.router.add_get("/now-playing/{station_id}", self._handle_now_playing)
        self.app.router.add_get("/health", self._handle_health)
        self.app.router.add_get("/client/{filename}", self._handle_client_file)

        # Mount admin routes
        from mcradio.admin import setup_admin_routes

        setup_admin_routes(self.app, self)

    def load_config(self) -> None:
        """Load stations.yaml and initialize station states."""
        if not self.config_path.is_file():
            logger.error("Config file not found: %s", self.config_path)
            sys.exit(1)

        with open(self.config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

        if self.config is None:
            logger.error("Config file is empty: %s", self.config_path)
            sys.exit(1)

        server_cfg = self.config.get("server", {})

        # Determine music directory
        if self._music_dir_override:
            music_dir = self._music_dir_override
        else:
            cfg_music_dir = server_cfg.get("music_dir", None)
            if cfg_music_dir:
                music_dir = Path(cfg_music_dir)
                # Resolve relative paths against the config file's directory
                if not music_dir.is_absolute():
                    music_dir = (self.config_path.parent / music_dir).resolve()
            else:
                music_dir = get_music_dir()

        self._music_dir = music_dir

        stations_cfg = self.config.get("stations", [])
        if not stations_cfg:
            logger.warning("No stations defined in config")

        for station_def in stations_cfg:
            station_id: str = station_def["id"]
            dfpwm_dir = music_dir / "dfpwm" / station_id
            rotation = station_def.get("rotation", "sequential")

            state = StationState(
                station_id=station_id,
                name=station_def.get("name", station_id),
                genre=station_def.get("genre", ""),
                frequency=station_def.get("frequency", ""),
                description=station_def.get("description", ""),
                dfpwm_dir=dfpwm_dir,
                rotation=rotation,
            )

            self.stations[station_id] = state
            logger.info(
                "Loaded station '%s' (%s) — %d tracks in %s [%s]",
                station_id,
                state.name,
                state.track_count,
                dfpwm_dir,
                rotation,
            )

    def reload_station(self, station_id: str) -> None:
        """Rescan the dfpwm directory and rebuild the playlist for a station."""
        if station_id not in self.stations:
            raise ValueError(f"Station '{station_id}' not found")

        state = self.stations[station_id]
        state.reload_playlist()
        logger.info(
            "Reloaded station '%s' — %d tracks", station_id, state.track_count
        )

    def _add_protocol_header(self, response: web.Response) -> web.Response:
        """Add the X-Radio-Protocol header to every response."""
        response.headers["X-Radio-Protocol"] = str(PROTOCOL_VERSION)
        return response

    # --- Handlers -----------------------------------------------------------

    async def _handle_stations(self, request: web.Request) -> web.Response:
        """GET /stations — Return list of all stations."""
        logger.debug("GET /stations from %s", request.remote)
        stations_list: list[dict[str, Any]] = []
        for state in self.stations.values():
            stations_list.append({
                "id": state.station_id,
                "name": state.name,
                "genre": state.genre,
                "frequency": state.frequency,
                "description": state.description,
                "track_count": state.track_count,
            })

        body = json.dumps({"stations": stations_list}, indent=2)
        response = web.Response(
            text=body,
            content_type="application/json",
        )
        return self._add_protocol_header(response)

    async def _handle_stream(self, request: web.Request) -> web.Response:
        """GET /stream/{station_id} — Return next 16KB DFPWM chunk."""
        station_id: str = request.match_info["station_id"]
        logger.debug("GET /stream/%s from %s", station_id, request.remote)

        if station_id not in self.stations:
            response = web.Response(
                text=json.dumps({"error": "Station not found", "station_id": station_id}),
                status=404,
                content_type="application/json",
            )
            return self._add_protocol_header(response)

        state = self.stations[station_id]

        if not state.has_tracks:
            response = web.Response(
                text=json.dumps({"error": "Station has no tracks", "station_id": station_id}),
                status=503,
                content_type="application/json",
            )
            return self._add_protocol_header(response)

        # Read chunk — this advances the shared station position
        chunk: bytes = state.read_chunk()

        response = web.Response(
            body=chunk,
            content_type="application/octet-stream",
        )
        response.headers["X-Station-Active"] = "true"
        response.headers["X-Track-Position"] = str(state.track_position_percent)
        return self._add_protocol_header(response)

    async def _handle_now_playing(self, request: web.Request) -> web.Response:
        """GET /now-playing/{station_id} — Return current track metadata."""
        station_id: str = request.match_info["station_id"]
        logger.debug("GET /now-playing/%s from %s", station_id, request.remote)

        if station_id not in self.stations:
            response = web.Response(
                text=json.dumps({"error": "Station not found", "station_id": station_id}),
                status=404,
                content_type="application/json",
            )
            return self._add_protocol_header(response)

        state = self.stations[station_id]

        if not state.has_tracks:
            response = web.Response(
                text=json.dumps({
                    "station_id": station_id,
                    "station_name": state.name,
                    "title": "No tracks available",
                    "artist": "",
                    "listeners": state.listener_count,
                }),
                content_type="application/json",
            )
            return self._add_protocol_header(response)

        track_stem = state.current_track_stem
        metadata = self._load_track_metadata(station_id, track_stem)

        body: dict[str, Any] = {
            "station_id": station_id,
            "station_name": state.name,
            "title": metadata.get("title", self._title_from_filename(track_stem)),
            "artist": metadata.get("artist", ""),
            "album": metadata.get("album", ""),
            "duration_seconds": metadata.get("duration_seconds", 0),
            "position_percent": state.track_position_percent,
            "listeners": state.listener_count,
            "track_index": state.current_track + 1,
            "track_total": state.track_count,
        }

        response = web.Response(
            text=json.dumps(body, indent=2),
            content_type="application/json",
        )
        return self._add_protocol_header(response)

    async def _handle_health(self, request: web.Request) -> web.Response:
        """GET /health — Server health check."""
        logger.debug("GET /health from %s", request.remote)

        total_tracks = sum(s.track_count for s in self.stations.values())
        uptime = time.time() - self.start_time

        body: dict[str, Any] = {
            "status": "ok",
            "protocol_version": PROTOCOL_VERSION,
            "stations_active": len(self.stations),
            "total_tracks_cached": total_tracks,
            "uptime_seconds": int(uptime),
        }

        response = web.Response(
            text=json.dumps(body, indent=2),
            content_type="application/json",
        )
        return self._add_protocol_header(response)

    async def _handle_client_file(self, request: web.Request) -> web.Response:
        """GET /client/{filename} — Serve Lua client files."""
        filename: str = request.match_info["filename"]
        logger.debug("GET /client/%s from %s", filename, request.remote)

        # Security: only allow .lua files, no path traversal
        if "/" in filename or "\\" in filename or ".." in filename:
            return web.Response(text="Forbidden", status=403)

        if not filename.endswith(".lua"):
            return web.Response(text="Not found", status=404)

        file_path = self._client_dir / filename
        if not file_path.is_file():
            return web.Response(
                text=json.dumps({"error": "File not found", "filename": filename}),
                status=404,
                content_type="application/json",
            )

        content = file_path.read_text(encoding="utf-8")
        response = web.Response(
            text=content,
            content_type="text/plain",
        )
        return self._add_protocol_header(response)

    # --- Metadata helpers ---------------------------------------------------

    def _load_track_metadata(self, station_id: str, track_stem: str | None) -> dict[str, Any]:
        """Load metadata from JSON file if it exists."""
        if track_stem is None:
            return {}

        metadata_path = self._music_dir / "metadata" / station_id / f"{track_stem}.json"
        if not metadata_path.is_file():
            return {}

        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
                return {}
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Failed to read metadata %s: %s", metadata_path, e)
            return {}

    @staticmethod
    def _title_from_filename(stem: str | None) -> str:
        """Derive a human-readable title from a filename stem.

        Replaces underscores and hyphens with spaces, applies title case.
        """
        if stem is None:
            return "Unknown"
        return stem.replace("_", " ").replace("-", " ").title()

    # --- Lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        """Start the server."""
        self.load_config()
        self.start_time = time.time()

        server_cfg = self.config.get("server", {})
        host: str = server_cfg.get("host", "127.0.0.1")
        port: int = int(server_cfg.get("port", 5309))

        runner = web.AppRunner(self.app)
        await runner.setup()

        site = web.TCPSite(runner, host, port)
        await site.start()

        logger.info("Radio server listening on http://%s:%d", host, port)
        logger.info("Stations: %s", ", ".join(self.stations.keys()) if self.stations else "(none)")
        logger.info("Client files: %s", self._client_dir)

        # Wait for shutdown signal
        stop_event = asyncio.Event()

        loop = asyncio.get_running_loop()

        def _signal_handler() -> None:
            logger.info("Shutdown signal received")
            stop_event.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _signal_handler)

        await stop_event.wait()

        # Graceful shutdown
        logger.info("Shutting down...")
        for state in self.stations.values():
            state.close()
        await runner.cleanup()
        logger.info("Server stopped.")


# ---------------------------------------------------------------------------
# Entry point (called by CLI)
# ---------------------------------------------------------------------------


def run_server(
    config_path: Path | None = None,
    music_dir: Path | None = None,
    client_dir: Path | None = None,
) -> None:
    """Start the radio server. Called by `mcradio serve`."""
    server = RadioServer(
        config_path=config_path,
        music_dir=music_dir,
        client_dir=client_dir,
    )

    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        pass
