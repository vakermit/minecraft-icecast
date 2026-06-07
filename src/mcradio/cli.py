"""mcradio CLI — manage the Minecraft radio server."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import typer
from rich.console import Console

from mcradio.config import get_config_path, get_music_dir

app = typer.Typer(
    name="mcradio",
    help="Minecraft CC:Tweaked internet radio — DFPWM streaming server + Lua client",
    no_args_is_help=True,
)
console = Console()

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
    # Show status
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
# Content management
# ---------------------------------------------------------------------------


@app.command()
def download(
    station_id: str = typer.Argument(help="Station ID (e.g. 'lofi')"),
    source: str = typer.Argument(help="URL or yt-dlp search (e.g. 'ytsearch10:lo-fi hip hop')"),
    music_dir: str = typer.Option(None, "--music-dir", "-d", help="Override music directory"),
) -> None:
    """Download audio for a station using yt-dlp."""
    base = Path(music_dir) if music_dir else get_music_dir()
    raw_dir = base / "raw" / station_id
    raw_dir.mkdir(parents=True, exist_ok=True)

    console.print(f"[bold]Downloading for station:[/bold] {station_id}")
    console.print(f"[dim]Source:[/dim] {source}")
    console.print(f"[dim]Output:[/dim] {raw_dir}/")
    console.print()

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

    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        console.print("[red]yt-dlp exited with errors.[/red]")
        raise typer.Exit(1)

    console.print()
    console.print("[green]Done.[/green] Now transcode with:")
    console.print(f"  mcradio transcode {station_id}")


@app.command()
def transcode(
    station_id: str = typer.Argument(help="Station ID (e.g. 'lofi')"),
    source_dir: str = typer.Option(None, "--source", "-s", help="Override source directory"),
    music_dir: str = typer.Option(None, "--music-dir", "-d", help="Override music directory"),
) -> None:
    """Transcode audio files to DFPWM for the radio server."""
    base = Path(music_dir) if music_dir else get_music_dir()
    raw_dir = Path(source_dir) if source_dir else (base / "raw" / station_id)
    dfpwm_dir = base / "dfpwm" / station_id
    meta_dir = base / "metadata" / station_id

    dfpwm_dir.mkdir(parents=True, exist_ok=True)
    meta_dir.mkdir(parents=True, exist_ok=True)

    if not raw_dir.is_dir():
        console.print(f"[red]Source directory not found:[/red] {raw_dir}")
        console.print("Put audio files there, or pass --source to specify a custom path.")
        raise typer.Exit(1)

    # Collect audio files
    extensions = ("*.mp3", "*.ogg", "*.opus", "*.flac", "*.wav", "*.m4a", "*.webm")
    audio_files: list[Path] = []
    for ext in extensions:
        audio_files.extend(raw_dir.glob(ext))

    if not audio_files:
        console.print(f"[yellow]No audio files found in {raw_dir}[/yellow]")
        raise typer.Exit(0)

    count = 0
    skipped = 0

    for f in sorted(audio_files):
        stem = f.stem
        out = dfpwm_dir / f"{stem}.dfpwm"

        if out.exists():
            skipped += 1
            continue

        console.print(f"  Transcoding: [cyan]{stem}[/cyan]")

        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(f), "-f", "dfpwm", "-ar", "48000", "-ac", "1", str(out)],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            console.print(f"    [red]Failed:[/red] {result.stderr.strip()}")
            continue

        # Generate metadata JSON if not exists
        meta_file = meta_dir / f"{stem}.json"
        if not meta_file.exists():
            title = stem.replace("-", " ").replace("_", " ").title()

            # Get duration via ffprobe
            duration = 0
            probe_result = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)],
                capture_output=True,
                text=True,
            )
            if probe_result.returncode == 0 and probe_result.stdout.strip():
                try:
                    duration = int(float(probe_result.stdout.strip()))
                except ValueError:
                    pass

            metadata = {
                "title": title,
                "artist": "Unknown Artist",
                "duration_seconds": duration,
                "source_file": f.name,
                "acquired_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            meta_file.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        count += 1

    console.print()
    console.print(f"[green]Done:[/green] {count} transcoded, {skipped} skipped (already exist)")
    console.print(f"DFPWM files: {dfpwm_dir}/")
    console.print(f"Metadata:    {meta_dir}/")


# ---------------------------------------------------------------------------
# Server
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
