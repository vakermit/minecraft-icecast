"""Tests for mcradio CLI commands."""

from __future__ import annotations

from typer.testing import CliRunner

from mcradio.cli import app

runner = CliRunner()


def test_cli_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "mcradio" in result.output.lower() or "minecraft" in result.output.lower()


def test_cli_serve_help():
    result = runner.invoke(app, ["serve", "--help"])
    assert result.exit_code == 0
    assert "--config" in result.output


def test_cli_download_help():
    result = runner.invoke(app, ["download", "--help"])
    assert result.exit_code == 0
    assert "station_id" in result.output.lower() or "STATION_ID" in result.output


def test_cli_transcode_help():
    result = runner.invoke(app, ["transcode", "--help"])
    assert result.exit_code == 0
    assert "station_id" in result.output.lower() or "STATION_ID" in result.output


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
