"""mcradio CLI — manage the Minecraft radio server."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

# ---------------------------------------------------------------------------
# App and sub-apps
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="mcradio",
    help="Minecraft CC:Tweaked internet radio — DFPWM streaming server + Lua client",
    no_args_is_help=True,
)

stations_app = typer.Typer(
    name="stations",
    help="Manage radio stations",
    no_args_is_help=True,
)

tracks_app = typer.Typer(
    name="tracks",
    help="Manage station tracks (download, transcode, remove)",
    no_args_is_help=True,
)

app.add_typer(stations_app, name="stations")
app.add_typer(tracks_app, name="tracks")

console = Console()

# ---------------------------------------------------------------------------
# API helper
# ---------------------------------------------------------------------------

_BASE_URL: str = os.environ.get("MCRADIO_URL", "http://127.0.0.1:5309")


def _api(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Make an HTTP request to the mcradio service.

    Args:
        method: HTTP method (GET, POST, DELETE)
        path: URL path (e.g. /admin/stations)
        body: Optional JSON body for POST requests

    Returns:
        Parsed JSON response dict.

    Raises:
        typer.Exit: On connection refused or HTTP errors.
    """
    url = f"{_BASE_URL}{path}"

    data: bytes | None = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            if raw:
                return json.loads(raw)
            return {}
    except urllib.error.HTTPError as e:
        try:
            error_body = e.read().decode("utf-8")
            error_data = json.loads(error_body)
            msg = error_data.get("error", f"HTTP {e.code}")
        except (json.JSONDecodeError, ValueError):
            msg = f"HTTP {e.code}: {e.reason}"
        console.print(f"[red]Error:[/red] {msg}")
        raise typer.Exit(1)
    except urllib.error.URLError as e:
        if "Connection refused" in str(e.reason) or "refused" in str(e.reason).lower():
            console.print("[red]mcradio service is not running.[/red] Start with: mcradio start")
        else:
            console.print(f"[red]Connection error:[/red] {e.reason}")
        raise typer.Exit(1)
    except OSError as e:
        console.print(f"[red]Network error:[/red] {e}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Stations subcommands
# ---------------------------------------------------------------------------


@stations_app.command("list")
def stations_list() -> None:
    """List all stations with track counts."""
    data = _api("GET", "/admin/stations")
    stations = data.get("stations", [])

    if not stations:
        console.print("[yellow]No stations configured.[/yellow]")
        return

    table = Table(title="Radio Stations")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Genre")
    table.add_column("Freq")
    table.add_column("Rotation")
    table.add_column("Tracks", justify="right")
    table.add_column("Description", style="dim")

    for s in stations:
        table.add_row(
            s["id"],
            s["name"],
            s.get("genre", ""),
            s.get("frequency", ""),
            s.get("rotation", "sequential"),
            str(s.get("track_count", 0)),
            s.get("description", ""),
        )

    console.print(table)


@stations_app.command("add")
def stations_add(
    station_id: str = typer.Argument(help="Station ID (e.g. 'lofi')"),
    name: str = typer.Argument(help="Display name (e.g. 'Lo-Fi Beats')"),
    genre: str = typer.Option("", "--genre", "-g", help="Genre tag"),
    frequency: str = typer.Option("", "--frequency", "-f", help="Frequency (e.g. 98.7)"),
    description: str = typer.Option("", "--description", "-d", help="Description"),
    rotation: str = typer.Option("sequential", "--rotation", "-r", help="Track rotation: shuffle or sequential"),
) -> None:
    """Add a new radio station."""
    body: dict[str, Any] = {
        "id": station_id,
        "name": name,
    }
    if genre:
        body["genre"] = genre
    if frequency:
        body["frequency"] = frequency
    if description:
        body["description"] = description
    if rotation:
        body["rotation"] = rotation

    data = _api("POST", "/admin/stations", body=body)
    console.print(f"[green]Station '{station_id}' created.[/green]")


@stations_app.command("remove")
def stations_remove(
    station_id: str = typer.Argument(help="Station ID to remove"),
) -> None:
    """Remove a radio station (deregister only — files remain on disk)."""
    data = _api("DELETE", f"/admin/stations/{station_id}")
    console.print(f"[yellow]Station '{station_id}' removed.[/yellow]")


@stations_app.command("reload")
def stations_reload(
    station_id: str = typer.Argument(help="Station ID to reload"),
) -> None:
    """Rescan tracks from disk for a station."""
    data = _api("POST", f"/admin/stations/{station_id}/reload")
    track_count = data.get("track_count", "?")
    console.print(f"[green]Station '{station_id}' reloaded — {track_count} tracks.[/green]")


# ---------------------------------------------------------------------------
# Tracks subcommands
# ---------------------------------------------------------------------------


@tracks_app.command("list")
def tracks_list(
    station_id: str = typer.Argument(help="Station ID"),
) -> None:
    """List all tracks for a station."""
    data = _api("GET", f"/admin/stations/{station_id}/tracks")
    tracks = data.get("tracks", [])

    if not tracks:
        console.print(f"[yellow]No tracks in station '{station_id}'.[/yellow]")
        return

    table = Table(title=f"Tracks — {station_id} ({len(tracks)} total)")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Name", style="cyan")
    table.add_column("Title")
    table.add_column("Artist")
    table.add_column("Duration", justify="right")
    table.add_column("Size", justify="right")

    for i, t in enumerate(tracks, 1):
        duration = t.get("duration_seconds", 0)
        dur_str = f"{duration // 60}:{duration % 60:02d}" if duration else "-"
        size = t.get("size_bytes", 0)
        size_str = f"{size / 1024:.0f}KB" if size else "-"

        table.add_row(
            str(i),
            t["name"],
            t.get("title", ""),
            t.get("artist", ""),
            dur_str,
            size_str,
        )

    console.print(table)


@tracks_app.command("download")
def tracks_download(
    station_id: str = typer.Argument(help="Station ID"),
    source: str = typer.Argument(help="URL or yt-dlp search (e.g. 'ytsearch10:lo-fi hip hop')"),
) -> None:
    """Download audio for a station (async background job)."""
    data = _api("POST", f"/admin/stations/{station_id}/download", body={"source": source})
    job = data.get("job", {})
    console.print(f"[green]Download started.[/green] Job ID: {job.get('id', '?')}")
    console.print("Check progress with: mcradio jobs")


@tracks_app.command("transcode")
def tracks_transcode(
    station_id: str = typer.Argument(help="Station ID"),
) -> None:
    """Transcode raw audio to DFPWM (async background job)."""
    data = _api("POST", f"/admin/stations/{station_id}/transcode")
    job = data.get("job", {})
    console.print(f"[green]Transcode started.[/green] Job ID: {job.get('id', '?')}")
    console.print("Check progress with: mcradio jobs")


@tracks_app.command("remove")
def tracks_remove(
    station_id: str = typer.Argument(help="Station ID"),
    track_name: str = typer.Argument(help="Track stem name (without .dfpwm extension)"),
) -> None:
    """Delete a track from a station (removes dfpwm + metadata)."""
    data = _api("DELETE", f"/admin/stations/{station_id}/tracks/{track_name}")
    console.print(f"[yellow]Track '{track_name}' deleted from '{station_id}'.[/yellow]")


# ---------------------------------------------------------------------------
# Jobs command (top-level)
# ---------------------------------------------------------------------------


@app.command("jobs")
def jobs_list() -> None:
    """List active and completed background jobs."""
    data = _api("GET", "/admin/jobs")
    jobs = data.get("jobs", [])

    if not jobs:
        console.print("[dim]No jobs.[/dim]")
        return

    table = Table(title="Background Jobs")
    table.add_column("ID", style="cyan")
    table.add_column("Type")
    table.add_column("Station")
    table.add_column("Status")
    table.add_column("Created")
    table.add_column("Last Output", style="dim")

    for j in jobs:
        status = j["status"]
        if status == "running":
            status_str = "[bold yellow]running[/bold yellow]"
        elif status == "done":
            status_str = "[green]done[/green]"
        else:
            status_str = "[red]failed[/red]"

        last_output = j["output"][-1] if j["output"] else ""
        # Truncate long output
        if len(last_output) > 50:
            last_output = last_output[:47] + "..."

        table.add_row(
            j["id"],
            j["type"],
            j["station_id"],
            status_str,
            j["created_at"],
            last_output,
        )

    console.print(table)


# ---------------------------------------------------------------------------
# Service management
# ---------------------------------------------------------------------------


@app.command()
def start() -> None:
    """Start the mcradio systemd service."""
    result = subprocess.run(
        ["sudo", "systemctl", "start", "mcradio"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        console.print(f"[red]Failed to start:[/red] {result.stderr.strip()}")
        raise typer.Exit(1)
    console.print("[green]mcradio started.[/green]")
    subprocess.run(["systemctl", "status", "mcradio", "--no-pager", "-l"], check=False)


@app.command()
def stop() -> None:
    """Stop the mcradio systemd service."""
    result = subprocess.run(
        ["sudo", "systemctl", "stop", "mcradio"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        console.print(f"[red]Failed to stop:[/red] {result.stderr.strip()}")
        raise typer.Exit(1)
    console.print("[yellow]mcradio stopped.[/yellow]")


@app.command()
def status() -> None:
    """Show mcradio service status."""
    subprocess.run(["systemctl", "status", "mcradio", "--no-pager", "-l"], check=False)


@app.command()
def logs(
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output"),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to show"),
) -> None:
    """Show mcradio service logs."""
    cmd = ["sudo", "journalctl", "-u", "mcradio", "--no-pager"]
    if follow:
        cmd.append("-f")
    else:
        cmd.extend(["-n", str(lines)])
    subprocess.run(cmd, check=False)


# ---------------------------------------------------------------------------
# Server (foreground)
# ---------------------------------------------------------------------------


@app.command()
def serve(
    config: str = typer.Option(None, "--config", "-c", help="Path to stations.yaml"),
    music_dir: str = typer.Option(None, "--music-dir", "-d", help="Override music directory"),
    client_dir: str = typer.Option(None, "--client-dir", help="Override client files directory"),
) -> None:
    """Start the radio server (foreground). Used by systemd ExecStart."""
    from mcradio.server import run_server

    config_path = Path(config) if config else None
    music_path = Path(music_dir) if music_dir else None
    client_path = Path(client_dir) if client_dir else None

    run_server(config_path=config_path, music_dir=music_path, client_dir=client_path)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
