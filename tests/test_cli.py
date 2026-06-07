"""Tests for mcradio CLI commands."""

from __future__ import annotations

from typer.testing import CliRunner

from mcradio.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------


def test_cli_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "mcradio" in result.output.lower() or "minecraft" in result.output.lower()


def test_cli_serve_help():
    result = runner.invoke(app, ["serve", "--help"])
    assert result.exit_code == 0
    assert "--config" in result.output


def test_cli_start_help():
    result = runner.invoke(app, ["start", "--help"])
    assert result.exit_code == 0


def test_cli_stop_help():
    result = runner.invoke(app, ["stop", "--help"])
    assert result.exit_code == 0


def test_cli_status_help():
    result = runner.invoke(app, ["status", "--help"])
    assert result.exit_code == 0


def test_cli_logs_help():
    result = runner.invoke(app, ["logs", "--help"])
    assert result.exit_code == 0
    assert "--follow" in result.output or "-f" in result.output


def test_cli_jobs_help():
    result = runner.invoke(app, ["jobs", "--help"])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Stations subcommands
# ---------------------------------------------------------------------------


def test_cli_stations_help():
    result = runner.invoke(app, ["stations", "--help"])
    assert result.exit_code == 0
    assert "list" in result.output
    assert "add" in result.output
    assert "remove" in result.output
    assert "reload" in result.output


def test_cli_stations_list_help():
    result = runner.invoke(app, ["stations", "list", "--help"])
    assert result.exit_code == 0


def test_cli_stations_add_help():
    result = runner.invoke(app, ["stations", "add", "--help"])
    assert result.exit_code == 0
    assert "--genre" in result.output or "-g" in result.output
    assert "--frequency" in result.output or "-f" in result.output
    assert "--description" in result.output or "-d" in result.output


def test_cli_stations_remove_help():
    result = runner.invoke(app, ["stations", "remove", "--help"])
    assert result.exit_code == 0
    assert "station_id" in result.output.lower() or "STATION_ID" in result.output


def test_cli_stations_reload_help():
    result = runner.invoke(app, ["stations", "reload", "--help"])
    assert result.exit_code == 0
    assert "station_id" in result.output.lower() or "STATION_ID" in result.output


# ---------------------------------------------------------------------------
# Tracks subcommands
# ---------------------------------------------------------------------------


def test_cli_tracks_help():
    result = runner.invoke(app, ["tracks", "--help"])
    assert result.exit_code == 0
    assert "list" in result.output
    assert "download" in result.output
    assert "transcode" in result.output
    assert "remove" in result.output


def test_cli_tracks_list_help():
    result = runner.invoke(app, ["tracks", "list", "--help"])
    assert result.exit_code == 0
    assert "station_id" in result.output.lower() or "STATION_ID" in result.output


def test_cli_tracks_download_help():
    result = runner.invoke(app, ["tracks", "download", "--help"])
    assert result.exit_code == 0
    assert "station_id" in result.output.lower() or "STATION_ID" in result.output
    assert "source" in result.output.lower() or "SOURCE" in result.output


def test_cli_tracks_transcode_help():
    result = runner.invoke(app, ["tracks", "transcode", "--help"])
    assert result.exit_code == 0
    assert "station_id" in result.output.lower() or "STATION_ID" in result.output


def test_cli_tracks_remove_help():
    result = runner.invoke(app, ["tracks", "remove", "--help"])
    assert result.exit_code == 0
    assert "station_id" in result.output.lower() or "STATION_ID" in result.output
    assert "track_name" in result.output.lower() or "TRACK_NAME" in result.output
