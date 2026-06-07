"""Background job manager for async download/transcode operations."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

logger = logging.getLogger("radio.worker")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_CONCURRENT_JOBS: int = 2
MAX_RETAINED_JOBS: int = 20


# ---------------------------------------------------------------------------
# Job types
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_job(job_type: str, station_id: str) -> dict[str, Any]:
    return {
        "id": uuid4().hex[:8],
        "type": job_type,
        "station_id": station_id,
        "status": "running",
        "created_at": _now_iso(),
        "finished_at": None,
        "output": [],
    }


# ---------------------------------------------------------------------------
# JobManager
# ---------------------------------------------------------------------------


class JobManager:
    """Manages background download and transcode jobs with bounded concurrency."""

    def __init__(self, reload_station_callback: Any = None) -> None:
        self._jobs: list[dict[str, Any]] = []
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
        self._reload_station = reload_station_callback

    @property
    def jobs(self) -> list[dict[str, Any]]:
        """Return all retained jobs (newest first)."""
        return list(reversed(self._jobs))

    def _register_job(self, job: dict[str, Any]) -> None:
        self._jobs.append(job)
        # Prune oldest if we exceed retention limit
        while len(self._jobs) > MAX_RETAINED_JOBS:
            self._jobs.pop(0)

    def _finish_job(self, job: dict[str, Any], status: str) -> None:
        job["status"] = status
        job["finished_at"] = _now_iso()

    # --- Download -----------------------------------------------------------

    def submit_download(
        self, station_id: str, source: str, music_dir: Path
    ) -> dict[str, Any]:
        """Submit a download job. Returns the job dict immediately."""
        job = _make_job("download", station_id)
        self._register_job(job)
        asyncio.create_task(self._run_download(job, station_id, source, music_dir))
        return job

    async def _run_download(
        self, job: dict[str, Any], station_id: str, source: str, music_dir: Path
    ) -> None:
        async with self._semaphore:
            raw_dir = music_dir / "raw" / station_id
            raw_dir.mkdir(parents=True, exist_ok=True)

            job["output"].append(f"Downloading to {raw_dir}")

            cmd = [
                "yt-dlp",
                "--extract-audio",
                "--audio-format", "opus",
                "--audio-quality", "0",
                "--min-duration", "60",
                "--max-duration", "600",
                "-o", str(raw_dir / "%(title)s.%(ext)s"),
                "--no-overwrites",
                source,
            ]

            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )

                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=600)

                if stdout:
                    # Keep last 20 lines of output
                    lines = stdout.decode(errors="replace").strip().splitlines()
                    job["output"].extend(lines[-20:])

                if proc.returncode == 0:
                    job["output"].append("Download complete")
                    self._finish_job(job, "done")
                else:
                    job["output"].append(f"yt-dlp exited with code {proc.returncode}")
                    self._finish_job(job, "failed")

            except asyncio.TimeoutError:
                job["output"].append("Download timed out (600s)")
                self._finish_job(job, "failed")
            except FileNotFoundError:
                job["output"].append("yt-dlp not found in PATH")
                self._finish_job(job, "failed")
            except OSError as e:
                job["output"].append(f"OS error: {e}")
                self._finish_job(job, "failed")

    # --- Transcode ----------------------------------------------------------

    def submit_transcode(
        self, station_id: str, music_dir: Path
    ) -> dict[str, Any]:
        """Submit a transcode job. Returns the job dict immediately."""
        job = _make_job("transcode", station_id)
        self._register_job(job)
        asyncio.create_task(self._run_transcode(job, station_id, music_dir))
        return job

    async def _run_transcode(
        self, job: dict[str, Any], station_id: str, music_dir: Path
    ) -> None:
        async with self._semaphore:
            raw_dir = music_dir / "raw" / station_id
            dfpwm_dir = music_dir / "dfpwm" / station_id
            meta_dir = music_dir / "metadata" / station_id

            dfpwm_dir.mkdir(parents=True, exist_ok=True)
            meta_dir.mkdir(parents=True, exist_ok=True)

            if not raw_dir.is_dir():
                job["output"].append(f"Source directory not found: {raw_dir}")
                self._finish_job(job, "failed")
                return

            # Collect audio files
            extensions = ("*.mp3", "*.ogg", "*.opus", "*.flac", "*.wav", "*.m4a", "*.webm")
            audio_files: list[Path] = []
            for ext in extensions:
                audio_files.extend(raw_dir.glob(ext))

            if not audio_files:
                job["output"].append(f"No audio files found in {raw_dir}")
                self._finish_job(job, "failed")
                return

            audio_files.sort()
            count = 0
            skipped = 0
            failed = 0

            for f in audio_files:
                stem = f.stem
                out = dfpwm_dir / f"{stem}.dfpwm"

                if out.exists():
                    skipped += 1
                    continue

                job["output"].append(f"Transcoding: {stem}")

                try:
                    proc = await asyncio.create_subprocess_exec(
                        "ffmpeg", "-hide_banner", "-loglevel", "error",
                        "-i", str(f),
                        "-f", "dfpwm", "-ar", "48000", "-ac", "1",
                        str(out),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )

                    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

                    if proc.returncode != 0:
                        err_text = stderr.decode(errors="replace").strip() if stderr else "unknown error"
                        job["output"].append(f"  Failed: {err_text}")
                        failed += 1
                        continue

                    # Generate metadata if not exists
                    meta_file = meta_dir / f"{stem}.json"
                    if not meta_file.exists():
                        title = stem.replace("-", " ").replace("_", " ").title()

                        # Get duration via ffprobe
                        duration = 0
                        try:
                            probe = await asyncio.create_subprocess_exec(
                                "ffprobe", "-v", "quiet",
                                "-show_entries", "format=duration",
                                "-of", "csv=p=0",
                                str(f),
                                stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.PIPE,
                            )
                            probe_out, _ = await asyncio.wait_for(probe.communicate(), timeout=30)
                            if probe.returncode == 0 and probe_out:
                                try:
                                    duration = int(float(probe_out.decode().strip()))
                                except ValueError:
                                    pass
                        except (asyncio.TimeoutError, FileNotFoundError):
                            pass

                        metadata = {
                            "title": title,
                            "artist": "Unknown Artist",
                            "duration_seconds": duration,
                            "source_file": f.name,
                            "acquired_at": _now_iso(),
                        }
                        meta_file.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

                    count += 1

                except asyncio.TimeoutError:
                    job["output"].append(f"  Timeout transcoding: {stem}")
                    failed += 1
                except FileNotFoundError:
                    job["output"].append("ffmpeg not found in PATH")
                    self._finish_job(job, "failed")
                    return

            job["output"].append(f"Complete: {count} transcoded, {skipped} skipped, {failed} failed")
            self._finish_job(job, "done")

            # Trigger station reload after successful transcode
            if self._reload_station is not None:
                try:
                    self._reload_station(station_id)
                    job["output"].append(f"Station '{station_id}' playlist reloaded")
                except Exception as e:
                    job["output"].append(f"Reload failed: {e}")
