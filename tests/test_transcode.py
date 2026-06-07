"""Tests for the transcode pipeline via CLI."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mcradio.cli import app

runner = CliRunner()


@pytest.fixture
def raw_audio(tmp_path: Path) -> Path:
    """Generate a short silent WAV file for transcode testing."""
    raw_dir = tmp_path / "music" / "raw" / "test-station"
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


def test_transcode_creates_dfpwm(raw_audio: Path):
    """Test that transcode converts WAV to DFPWM."""
    result = runner.invoke(app, [
        "transcode", "test-station",
        "--music-dir", str(raw_audio),
    ])
    assert result.exit_code == 0
    assert "1 transcoded" in result.output

    dfpwm_file = raw_audio / "dfpwm" / "test-station" / "silence.dfpwm"
    assert dfpwm_file.exists()
    assert dfpwm_file.stat().st_size > 0


def test_transcode_skips_existing(raw_audio: Path):
    """Test that transcode skips already-transcoded files."""
    # First run
    runner.invoke(app, ["transcode", "test-station", "--music-dir", str(raw_audio)])

    # Second run — should skip
    result = runner.invoke(app, [
        "transcode", "test-station",
        "--music-dir", str(raw_audio),
    ])
    assert result.exit_code == 0
    assert "1 skipped" in result.output


def test_transcode_creates_metadata(raw_audio: Path):
    """Test that transcode generates metadata JSON."""
    runner.invoke(app, ["transcode", "test-station", "--music-dir", str(raw_audio)])

    meta_file = raw_audio / "metadata" / "test-station" / "silence.json"
    assert meta_file.exists()

    import json
    meta = json.loads(meta_file.read_text())
    assert "title" in meta
    assert "duration_seconds" in meta


def test_transcode_empty_dir(tmp_path: Path):
    """Test transcode with no audio files."""
    empty = tmp_path / "music" / "raw" / "empty-station"
    empty.mkdir(parents=True)

    result = runner.invoke(app, [
        "transcode", "empty-station",
        "--music-dir", str(tmp_path / "music"),
    ])
    assert result.exit_code == 0
    assert "No audio files" in result.output


def test_transcode_missing_dir(tmp_path: Path):
    """Test transcode with nonexistent source directory."""
    result = runner.invoke(app, [
        "transcode", "nonexistent",
        "--music-dir", str(tmp_path / "music"),
    ])
    assert result.exit_code == 1
    assert "not found" in result.output
