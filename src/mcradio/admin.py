"""Admin API routes for station and track management."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from aiohttp import web

if TYPE_CHECKING:
    from mcradio.server import RadioServer

logger = logging.getLogger("radio.admin")


# ---------------------------------------------------------------------------
# Route setup
# ---------------------------------------------------------------------------


def setup_admin_routes(app: web.Application, server: "RadioServer") -> None:
    """Register all /admin/* routes on the application."""
    handler = AdminHandler(server)

    app.router.add_get("/admin/stations", handler.list_stations)
    app.router.add_post("/admin/stations", handler.add_station)
    app.router.add_delete("/admin/stations/{station_id}", handler.remove_station)
    app.router.add_post("/admin/stations/{station_id}/reload", handler.reload_station)
    app.router.add_get("/admin/stations/{station_id}/tracks", handler.list_tracks)
    app.router.add_post("/admin/stations/{station_id}/download", handler.download_tracks)
    app.router.add_post("/admin/stations/{station_id}/transcode", handler.transcode_tracks)
    app.router.add_delete(
        "/admin/stations/{station_id}/tracks/{track_name}", handler.remove_track
    )
    app.router.add_get("/admin/jobs", handler.list_jobs)


# ---------------------------------------------------------------------------
# Admin Handler
# ---------------------------------------------------------------------------


class AdminHandler:
    """Handles all /admin/ API requests."""

    def __init__(self, server: "RadioServer") -> None:
        self._server = server

    def _json_response(
        self, data: Any, status: int = 200
    ) -> web.Response:
        return web.Response(
            text=json.dumps(data, indent=2),
            status=status,
            content_type="application/json",
        )

    def _error_response(self, message: str, status: int = 400) -> web.Response:
        return self._json_response({"error": message}, status=status)

    # --- GET /admin/stations ------------------------------------------------

    async def list_stations(self, request: web.Request) -> web.Response:
        """List all stations with track counts and config."""
        stations_list: list[dict[str, Any]] = []
        for state in self._server.stations.values():
            stations_list.append({
                "id": state.station_id,
                "name": state.name,
                "genre": state.genre,
                "frequency": state.frequency,
                "description": state.description,
                "rotation": state.rotation,
                "track_count": state.track_count,
            })

        return self._json_response({"stations": stations_list})

    # --- POST /admin/stations -----------------------------------------------

    async def add_station(self, request: web.Request) -> web.Response:
        """Add a new station."""
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return self._error_response("Invalid JSON body")

        station_id = body.get("id")
        if not station_id:
            return self._error_response("Missing required field: id")

        if not isinstance(station_id, str) or not station_id.isidentifier():
            return self._error_response(
                "Station ID must be a valid identifier (letters, digits, underscores)"
            )

        if station_id in self._server.stations:
            return self._error_response(f"Station '{station_id}' already exists", status=409)

        name = body.get("name", station_id)
        genre = body.get("genre", "")
        frequency = body.get("frequency", "")
        description = body.get("description", "")
        rotation = body.get("rotation", "sequential")

        if rotation not in ("shuffle", "sequential"):
            return self._error_response("rotation must be 'shuffle' or 'sequential'")

        # Create directories
        music_dir = self._server.music_dir
        (music_dir / "raw" / station_id).mkdir(parents=True, exist_ok=True)
        (music_dir / "dfpwm" / station_id).mkdir(parents=True, exist_ok=True)
        (music_dir / "metadata" / station_id).mkdir(parents=True, exist_ok=True)

        # Add to stations.yaml
        self._persist_station_add(station_id, name, genre, frequency, description, rotation)

        # Initialize StationState in memory
        from mcradio.server import StationState

        dfpwm_dir = music_dir / "dfpwm" / station_id
        state = StationState(
            station_id=station_id,
            name=name,
            genre=genre,
            frequency=frequency,
            description=description,
            dfpwm_dir=dfpwm_dir,
            rotation=rotation,
        )
        self._server.stations[station_id] = state

        logger.info("Added station '%s' (%s)", station_id, name)

        return self._json_response(
            {"status": "created", "station_id": station_id}, status=201
        )

    def _persist_station_add(
        self,
        station_id: str,
        name: str,
        genre: str,
        frequency: str,
        description: str,
        rotation: str,
    ) -> None:
        """Add a station entry to stations.yaml on disk."""
        config_path = self._server.config_path

        if config_path.is_file():
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
        else:
            config = {"server": {"host": "127.0.0.1", "port": 5309}, "stations": []}

        if "stations" not in config:
            config["stations"] = []

        new_entry: dict[str, Any] = {"id": station_id, "name": name}
        if genre:
            new_entry["genre"] = genre
        if frequency:
            new_entry["frequency"] = frequency
        if description:
            new_entry["description"] = description
        if rotation != "sequential":
            new_entry["rotation"] = rotation

        config["stations"].append(new_entry)

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    # --- DELETE /admin/stations/{station_id} ---------------------------------

    async def remove_station(self, request: web.Request) -> web.Response:
        """Remove a station (deregister only — files remain)."""
        station_id = request.match_info["station_id"]

        if station_id not in self._server.stations:
            return self._error_response(f"Station '{station_id}' not found", status=404)

        # Close file handles and remove from memory
        self._server.stations[station_id].close()
        del self._server.stations[station_id]

        # Remove from stations.yaml
        self._persist_station_remove(station_id)

        logger.info("Removed station '%s'", station_id)
        return self._json_response({"status": "removed", "station_id": station_id})

    def _persist_station_remove(self, station_id: str) -> None:
        """Remove a station entry from stations.yaml on disk."""
        config_path = self._server.config_path

        if not config_path.is_file():
            return

        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

        stations = config.get("stations", [])
        config["stations"] = [s for s in stations if s.get("id") != station_id]

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    # --- POST /admin/stations/{station_id}/reload ----------------------------

    async def reload_station(self, request: web.Request) -> web.Response:
        """Rescan tracks from disk for a station."""
        station_id = request.match_info["station_id"]

        if station_id not in self._server.stations:
            return self._error_response(f"Station '{station_id}' not found", status=404)

        self._server.reload_station(station_id)

        state = self._server.stations[station_id]
        return self._json_response({
            "status": "reloaded",
            "station_id": station_id,
            "track_count": state.track_count,
        })

    # --- GET /admin/stations/{station_id}/tracks -----------------------------

    async def list_tracks(self, request: web.Request) -> web.Response:
        """List all tracks with metadata for a station."""
        station_id = request.match_info["station_id"]

        if station_id not in self._server.stations:
            return self._error_response(f"Station '{station_id}' not found", status=404)

        state = self._server.stations[station_id]
        music_dir = self._server.music_dir
        tracks: list[dict[str, Any]] = []

        for path in state.playlist:
            stem = path.stem
            metadata = self._server._load_track_metadata(station_id, stem)

            track_info: dict[str, Any] = {
                "name": stem,
                "filename": path.name,
                "size_bytes": path.stat().st_size if path.exists() else 0,
                "title": metadata.get("title", stem.replace("-", " ").replace("_", " ").title()),
                "artist": metadata.get("artist", ""),
                "duration_seconds": metadata.get("duration_seconds", 0),
            }
            tracks.append(track_info)

        return self._json_response({
            "station_id": station_id,
            "track_count": len(tracks),
            "tracks": tracks,
        })

    # --- POST /admin/stations/{station_id}/download --------------------------

    async def download_tracks(self, request: web.Request) -> web.Response:
        """Trigger async download for a station."""
        station_id = request.match_info["station_id"]

        if station_id not in self._server.stations:
            return self._error_response(f"Station '{station_id}' not found", status=404)

        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return self._error_response("Invalid JSON body")

        source = body.get("source")
        if not source:
            return self._error_response("Missing required field: source")

        job = self._server.job_manager.submit_download(
            station_id=station_id,
            source=source,
            music_dir=self._server.music_dir,
        )

        logger.info("Download job %s started for station '%s'", job["id"], station_id)
        return self._json_response({"status": "started", "job": job}, status=202)

    # --- POST /admin/stations/{station_id}/transcode -------------------------

    async def transcode_tracks(self, request: web.Request) -> web.Response:
        """Trigger async transcode for a station."""
        station_id = request.match_info["station_id"]

        if station_id not in self._server.stations:
            return self._error_response(f"Station '{station_id}' not found", status=404)

        job = self._server.job_manager.submit_transcode(
            station_id=station_id,
            music_dir=self._server.music_dir,
        )

        logger.info("Transcode job %s started for station '%s'", job["id"], station_id)
        return self._json_response({"status": "started", "job": job}, status=202)

    # --- DELETE /admin/stations/{station_id}/tracks/{track_name} --------------

    async def remove_track(self, request: web.Request) -> web.Response:
        """Delete a track (dfpwm + metadata) from a station."""
        station_id = request.match_info["station_id"]
        track_name = request.match_info["track_name"]

        if station_id not in self._server.stations:
            return self._error_response(f"Station '{station_id}' not found", status=404)

        music_dir = self._server.music_dir
        dfpwm_file = music_dir / "dfpwm" / station_id / f"{track_name}.dfpwm"
        meta_file = music_dir / "metadata" / station_id / f"{track_name}.json"

        if not dfpwm_file.exists():
            return self._error_response(
                f"Track '{track_name}' not found in station '{station_id}'", status=404
            )

        # Delete files
        dfpwm_file.unlink()
        if meta_file.exists():
            meta_file.unlink()

        # Reload the station playlist
        self._server.reload_station(station_id)

        logger.info("Deleted track '%s' from station '%s'", track_name, station_id)
        return self._json_response({
            "status": "deleted",
            "station_id": station_id,
            "track_name": track_name,
        })

    # --- GET /admin/jobs ----------------------------------------------------

    async def list_jobs(self, request: web.Request) -> web.Response:
        """List all active and completed jobs."""
        jobs = self._server.job_manager.jobs
        return self._json_response({"jobs": jobs})
