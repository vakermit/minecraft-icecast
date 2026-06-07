"""Tests for the transcode/download worker logic."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from mcradio.worker import JobManager


@pytest.fixture
def raw_audio(tmp_path: Path) -> Path:
    """Generate a short silent WAV file for transcode testing."""
    raw_dir = tmp_path / "music" / "raw" / "test_station"
    raw_dir.mkdir(parents=True)

    wav_file = raw_dir / "silence.wav"

    # Generate 3 seconds of silence using ffmpeg
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
            "-t", "3", str(wav_file),
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        pytest.skip("ffmpeg not available or cannot generate test audio")

    return tmp_path / "music"


@pytest.fixture
def job_manager() -> JobManager:
    """Create a JobManager without reload callback."""
    return JobManager(reload_station_callback=None)


async def test_transcode_creates_dfpwm(raw_audio: Path, job_manager: JobManager):
    """Test that the worker transcode job converts WAV to DFPWM."""
    job = job_manager.submit_transcode("test_station", raw_audio)
    assert job["type"] == "transcode"
    assert job["status"] == "running"

    # Wait for the job to complete
    for _ in range(50):
        await asyncio.sleep(0.1)
        if job["status"] != "running":
            break

    assert job["status"] == "done"

    dfpwm_file = raw_audio / "dfpwm" / "test_station" / "silence.dfpwm"
    assert dfpwm_file.exists()
    assert dfpwm_file.stat().st_size > 0


async def test_transcode_skips_existing(raw_audio: Path, job_manager: JobManager):
    """Test that the worker skips already-transcoded files."""
    # First run
    job1 = job_manager.submit_transcode("test_station", raw_audio)
    for _ in range(50):
        await asyncio.sleep(0.1)
        if job1["status"] != "running":
            break
    assert job1["status"] == "done"

    # Second run — should skip
    job2 = job_manager.submit_transcode("test_station", raw_audio)
    for _ in range(50):
        await asyncio.sleep(0.1)
        if job2["status"] != "running":
            break
    assert job2["status"] == "done"
    # Output should mention "0 transcoded" and "1 skipped"
    final_line = job2["output"][-1] if job2["output"] else ""
    assert "0 transcoded" in final_line
    assert "1 skipped" in final_line


async def test_transcode_creates_metadata(raw_audio: Path, job_manager: JobManager):
    """Test that transcode generates metadata JSON."""
    job = job_manager.submit_transcode("test_station", raw_audio)
    for _ in range(50):
        await asyncio.sleep(0.1)
        if job["status"] != "running":
            break

    meta_file = raw_audio / "metadata" / "test_station" / "silence.json"
    assert meta_file.exists()

    meta = json.loads(meta_file.read_text())
    assert "title" in meta
    assert "duration_seconds" in meta


async def test_transcode_empty_dir(tmp_path: Path, job_manager: JobManager):
    """Test transcode with no audio files."""
    empty = tmp_path / "music" / "raw" / "empty_station"
    empty.mkdir(parents=True)

    job = job_manager.submit_transcode("empty_station", tmp_path / "music")
    for _ in range(50):
        await asyncio.sleep(0.1)
        if job["status"] != "running":
            break

    assert job["status"] == "failed"
    assert any("No audio files" in line for line in job["output"])


async def test_transcode_missing_dir(tmp_path: Path, job_manager: JobManager):
    """Test transcode with nonexistent source directory."""
    job = job_manager.submit_transcode("nonexistent", tmp_path / "music")
    for _ in range(50):
        await asyncio.sleep(0.1)
        if job["status"] != "running":
            break

    assert job["status"] == "failed"
    assert any("not found" in line for line in job["output"])


async def test_job_manager_retention(job_manager: JobManager):
    """Test that only MAX_RETAINED_JOBS are kept."""
    from mcradio.worker import MAX_RETAINED_JOBS

    for i in range(MAX_RETAINED_JOBS + 5):
        job_manager.submit_transcode(f"station_{i}", Path("/nonexistent"))

    # Wait a moment for jobs to start
    await asyncio.sleep(0.3)

    assert len(job_manager._jobs) <= MAX_RETAINED_JOBS


async def test_job_manager_reload_callback(tmp_path: Path):
    """Test that reload callback is called after successful transcode."""
    reloaded: list[str] = []

    def reload_fn(station_id: str) -> None:
        reloaded.append(station_id)

    jm = JobManager(reload_station_callback=reload_fn)

    # Set up a valid raw directory with an audio file
    raw_dir = tmp_path / "music" / "raw" / "test_station"
    raw_dir.mkdir(parents=True)

    result = subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
            "-t", "1", str(raw_dir / "test.wav"),
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        pytest.skip("ffmpeg not available")

    job = jm.submit_transcode("test_station", tmp_path / "music")
    for _ in range(50):
        await asyncio.sleep(0.1)
        if job["status"] != "running":
            break

    assert job["status"] == "done"
    assert "test_station" in reloaded
