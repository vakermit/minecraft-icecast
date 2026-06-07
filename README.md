# minecraft-icecast

In-game internet radio for Minecraft via [CC:Tweaked](https://tweaked.cc/). Players run `radio` on a ComputerCraft computer, hear pre-transcoded DFPWM music through an adjacent speaker, browse stations, and see track metadata — like tuning a real radio.

## How It Works

```
┌─────────────────────────────────────────────────────┐
│  Minecraft Server Host                              │
│                                                     │
│  CC:Tweaked Computer ◄── HTTP GET ── Python Server  │
│    └─ Speaker               (port 5309)             │
│       (48kHz DFPWM)         └─ music/dfpwm/*.dfpwm  │
└─────────────────────────────────────────────────────┘
```

- **Server** serves pre-transcoded DFPWM audio as 16KB chunks over HTTP
- **Client** (Lua) fetches chunks, decodes, plays through speaker peripheral
- **Radio model** — all listeners on a station hear the same position (like real radio)
- **Cache-first** — all audio pre-downloaded and transcoded; serving path is pure file I/O

## Install

```bash
curl -sSL https://raw.githubusercontent.com/vakermit/minecraft-icecast/main/install.sh | sudo bash
```

This creates an `icecast` system user, clones the repo to `/opt/icecast`, sets up a Python venv (prefers `uv`, falls back to `pip`), installs the systemd service, and symlinks the `mcradio` CLI.

**Requirements:** Python 3.11+, FFmpeg 5.1+ (DFPWM codec), git. Optional: yt-dlp (for downloading music).

## Usage

### Server Management

```bash
mcradio start                # start the systemd service
mcradio stop                 # stop it
mcradio status               # check status
mcradio logs -f              # follow logs
```

### Station & Track Management

These commands talk to the running server via HTTP — no sudo needed:

```bash
mcradio stations list
mcradio stations add lofi "Lo-Fi Beats" --genre Electronic --frequency 98.7
mcradio stations remove jazz
mcradio stations reload lofi

mcradio tracks list lofi
mcradio tracks download lofi "ytsearch10:lo-fi hip hop instrumental"
mcradio tracks transcode lofi
mcradio tracks remove lofi "midnight-stroll"

mcradio jobs                 # check background download/transcode progress
```

### In-Game (CC:Tweaked)

On any ComputerCraft computer with an adjacent speaker:

```
> wget run http://127.0.0.1:5309/client/installer.lua
> radio
```

The installer pulls the client files from the running server. Players get station browsing, now-playing display, and volume control.

## Development

```bash
git clone https://github.com/vakermit/minecraft-icecast.git
cd minecraft-icecast
uv venv .venv && source .venv/bin/activate
uv pip install -e ".[dev]"
pytest
```

Run the server locally:

```bash
MCRADIO_MUSIC_DIR=./music MCRADIO_CONFIG=./stations.yaml MCRADIO_CLIENT_DIR=./client mcradio serve
```

## Project Structure

```
├── install.sh              # curl | sudo bash installer
├── pyproject.toml          # package config (hatchling)
├── stations.yaml           # station definitions
├── src/mcradio/
│   ├── cli.py              # Typer CLI (stations/tracks/service commands)
│   ├── server.py           # aiohttp radio server
│   ├── admin.py            # admin API routes
│   ├── worker.py           # background download/transcode jobs
│   └── config.py           # env-var-driven config
├── client/
│   ├── installer.lua       # CC:Tweaked self-installer
│   └── radio.lua           # in-game radio client
├── systemd/
│   └── mcradio.service     # hardened systemd unit
└── tests/                  # 64 pytest tests
```

## Disclaimer

This software is provided **as is**, without warranty of any kind. It downloads audio from external sources using yt-dlp — you are responsible for ensuring your use complies with applicable copyright laws and the terms of service of any source platforms. The authors are not liable for how this tool is used.

## License

[MIT](LICENSE)
