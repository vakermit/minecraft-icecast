# Minecraft Icecast Radio — System Design Document

> **Project:** "icecast" — in-game radio station for a private Minecraft server
> **Components:** Lua client (CC:Tweaked) · Python radio server (yt-dlp + FFmpeg + metadata) 
> **Author:** vakermit · **Version:** 2.0 · **Date:** 2026-05-18

---

## 1. System Architecture

### 1.1 Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                     MINECRAFT SERVER HOST                             │
│                                                                       │
│  ┌──────────────┐     HTTP GET      ┌───────────────────────────┐   │
│  │  CC:Tweaked   │ ◄──────────────── │   Python Radio Server     │   │
│  │  Computer     │   16KB DFPWM      │   (port 5309)             │   │
│  │              │   chunks           │                           │   │
│  │  ┌────────┐ │                    │  ┌─────────────────────┐  │   │
│  │  │ radio  │ │   GET /metadata    │  │ Music Library       │  │   │
│  │  │ program│ │ ◄──────────────── │  │                     │  │   │
│  │  └────┬───┘ │   JSON             │  │ yt-dlp ──► cache/  │  │   │
│  │       │     │                    │  │ FFmpeg ──► .dfpwm   │  │   │
│  │  ┌────▼───┐ │                    │  │ Metadata ──► .json  │  │   │
│  │  │Speaker │ │                    │  │ Playlist ──► rotate │  │   │
│  │  │Periph. │ │                    │  └─────────────────────┘  │   │
│  │  └────────┘ │                    │                           │   │
│  └──────────────┘                    └───────────────────────────┘   │
│                                                                       │
│  ┌──────────────┐                                                    │
│  │  Minecraft    │                                                    │
│  │  Server       │                                                    │
│  └──────────────┘                                                    │
└──────────────────────────────────────────────────────────────────────┘
                    │
                    │ yt-dlp (on-demand, cached)
                    ▼
          ┌──────────────────┐
          │   Internet       │
          │                  │
          │  YouTube         │
          │  SoundCloud      │
          │  Bandcamp        │
          │  etc.            │
          └──────────────────┘
```

### 1.2 Component Responsibilities

| Component | Role | Language | Key Constraint |
|-----------|------|----------|----------------|
| **Lua Client** (`radio`) | UI + audio playback loop | Lua 5.2 (CC:Tweaked) | No raw sockets, no FFI, HTTP/WS only |
| **Python Radio Server** | Music acquisition, transcode, serve, metadata | Python 3.10+ | DFPWM conversion, fan-out to N clients, local cache |
| **Music Library** (cache) | Downloaded tracks + DFPWM conversions + metadata JSON | Filesystem | Disk space, organized by station/genre |
| **Speaker Peripheral** | Audio output in-game | CC:Tweaked mod | 48kHz mono DFPWM only, pull-driven |

### 1.3 Deployment Topology

```
Single Host (private Minecraft server):
├── Minecraft Server (Java, default port 25565)
│   └── CC:Tweaked mod installed
│       └── HTTP config allows 127.0.0.1:5309
└── Python Radio Server (port 5309, user: icecast)
    ├── yt-dlp binary in PATH (music acquisition)
    ├── FFmpeg binary in PATH (DFPWM transcoding)
    └── Music cache at /opt/mcradio/music/
```

All services run on the same machine. CC:Tweaked connects to the radio server at `127.0.0.1:5309` (localhost only — no network exposure). The `icecast` service user owns the Python process and music cache. Port 5309 ("Jenny") is above 1024 so no root needed.

---

## 2. Protocol Specification

### 2.1 Design Decision: HTTP GET (Pull Model)

**Why not WebSocket push?** The speaker hardware is pull-driven — it fires `speaker_audio_empty` when ready for the next chunk. Push creates an impedance mismatch: the server pushes data the client isn't ready for, requiring client-side buffering that duplicates what the speaker already handles. HTTP GET matches the hardware physics.

**Why not chunked HTTP streaming?** CC:Tweaked's HTTP response handling for persistent streams is implementation-dependent. Each discrete GET is self-healing — if the server restarts, the next GET reconnects automatically. No connection state to manage.

### 2.2 Endpoints

**Base URL:** `http://127.0.0.1:5309`

#### `GET /stations`

Returns the station manifest.

```json
{
  "stations": [
    {
      "id": "lofi",
      "name": "Lo-Fi Beats",
      "genre": "Electronic",
      "frequency": "98.7",
      "description": "Chill beats to mine to",
      "track_count": 42
    },
    {
      "id": "jazz",
      "name": "Smooth Jazz FM",
      "genre": "Jazz",
      "frequency": "101.3",
      "description": "Late night jazz vibes",
      "track_count": 28
    }
  ]
}
```

#### `GET /stream/{station_id}`

Returns the next 16KB DFPWM audio chunk. Binary response.

- **Content-Type:** `application/octet-stream`
- **Response body:** Exactly 16,384 bytes of DFPWM data
- **Behavior:** Serves the next chunk from the station's current track position
- **Headers returned:**
  - `X-Station-Active: true|false` — is this station currently playing?
  - `X-Track-Position: 0-100` — percentage through current track
- **Error responses:**
  - `404` — station ID not found
  - `503` — station has no tracks cached yet
  - `204` — no data available yet (transcoding in progress)

#### `GET /now-playing/{station_id}`

Returns current track metadata.

```json
{
  "station_id": "lofi",
  "station_name": "Lo-Fi Beats",
  "title": "Midnight Stroll",
  "artist": "ChillHop Records",
  "album": "Late Night Vibes Vol. 3",
  "duration_seconds": 214,
  "position_seconds": 87,
  "listeners": 3,
  "next_track": "Rainy Café"
}
```

#### `GET /health`

Server health check for debugging.

```json
{
  "status": "ok",
  "stations_active": 3,
  "total_listeners": 5,
  "total_tracks_cached": 156,
  "cache_size_mb": 892,
  "uptime_seconds": 86400,
  "ffmpeg_version": "6.1.1",
  "ytdlp_version": "2024.12.06"
}
```

### 2.3 Protocol Versioning

All responses include `X-Radio-Protocol: 1` header. Future breaking changes increment this. Lua client checks on startup and displays "Radio needs update!" if version mismatch.

---

## 3. Audio Pipeline

### 3.1 Full Chain

```
Internet (YouTube/SC/BC) ──► yt-dlp download ──► MP3/OGG/OPUS file ──► FFmpeg ──► .dfpwm cache ──► Ring Buffer ──► HTTP response ──► Lua decode ──► Speaker
                               (on-demand)          (cached)            (offline)    (on disk)     (per station)    (on demand)       (cc.audio)     (48kHz mono)
```

**Two-stage pipeline:**
1. **Acquisition (background):** yt-dlp downloads tracks → stored as original format in `music/raw/`
2. **Transcoding (background):** FFmpeg converts raw → DFPWM → stored in `music/dfpwm/`
3. **Serving (real-time):** Python reads pre-transcoded DFPWM from disk → serves 16KB chunks

All transcoding happens OFFLINE (not in the audio serving path). The serving path is just "read bytes from file" — zero CPU overhead, zero failure modes.

### 3.2 DFPWM Encoding Details

| Parameter | Value | Notes |
|-----------|-------|-------|
| Sample rate | 48,000 Hz | Fixed by CC:Tweaked speaker |
| Channels | 1 (mono) | Speaker is mono; stereo → mono mixdown |
| Bit depth | 1-bit | DFPWM codec characteristic |
| Chunk size | 16,384 bytes | 16KB = 131,072 samples = 2.73 seconds |
| Data rate | ~6.0 KB/s | Trivial bandwidth requirement |
| Codec | DFPWM (Delta-modulated Frequency Pulse Width Modulation) | Supported natively by FFmpeg 5.1+ |
| File size | ~6 KB/s × duration | 3-min track ≈ 1.1 MB as DFPWM |

### 3.3 FFmpeg Transcoding Command (Offline)

```bash
ffmpeg -i "music/raw/lofi/midnight-stroll.opus" \
  -f dfpwm -ar 48000 -ac 1 \
  "music/dfpwm/lofi/midnight-stroll.dfpwm"
```

Key points:
- `-f dfpwm` — output DFPWM format (FFmpeg 5.1+)
- `-ar 48000 -ac 1` — force 48kHz mono
- Input format is auto-detected (MP3, OGG, OPUS, FLAC, WAV, etc.)
- This runs as a background job, NOT in the audio serving path

### 3.4 Ring Buffer Design (Server-Side)

Each station maintains a playback position through its playlist:

```
┌───────────────────────────────────────────────────────────┐
│ Station Playback State                                    │
│                                                           │
│  Playlist: [track1.dfpwm, track2.dfpwm, ..., trackN.dfpwm]│
│                    ▲                                       │
│                    │                                       │
│              current_track (index)                         │
│              current_offset (byte position in file)       │
│                                                           │
│  Read: HTTP GET → read 16KB from current_offset          │
│        → advance offset by 16384                          │
│        → if EOF: advance to next track, reset offset     │
│                                                           │
│  Fan-out: all listeners on same station share position    │
│           (radio model — everyone hears the same thing)   │
└───────────────────────────────────────────────────────────┘
```

**Radio model (not jukebox):** All listeners on the same station hear the same track at the same position — like a real radio station. Late joiners pick up wherever the station currently is.

**Track rotation:** When a track ends, advance to the next in the playlist. When the playlist ends, loop or shuffle (configurable per station).

### 3.5 Buffering Strategy: Double-Buffer (Client-Side)

```lua
-- Client maintains 2 chunk slots:
-- Slot A: currently playing (or about to play)
-- Slot B: pre-fetched (next chunk ready)

-- Fetch loop (coroutine 1):
while playing do
    local chunk = http.get(stream_url, nil, true).readAll()
    buffer:push(chunk)  -- blocks if buffer full (2 slots)
end

-- Playback loop (coroutine 2):
while playing do
    local chunk = buffer:pop()  -- blocks if empty
    local samples = dfpwm.decode(chunk)
    speaker.playAudio(samples)
    os.pullEvent("speaker_audio_empty")  -- wait ~2.73s
end
```

**Why double-buffer (not ring-N):**
- 1 chunk ahead absorbs any single-request latency spike
- On localhost, one chunk ahead is ~2.7s of safety margin
- Larger client buffer wastes CC computer memory for near-zero benefit
- Keeps Lua client simple (~10 lines for buffer logic)

### 3.6 Latency Budget

| Stage | Time | Notes |
|-------|------|-------|
| Disk read (DFPWM file) | <1ms | SSD, sequential read, 16KB |
| Python → Lua (HTTP GET) | <10ms | Localhost, 16KB payload |
| DFPWM decode (Lua) | <5ms | Built-in cc.audio.dfpwm |
| **Total cold-start** | **<500ms** | File already on disk, just read + serve |
| **Steady-state** | **<50ms** | Chunk already in buffer, instant |

Since all audio is pre-transcoded and cached on disk, there's no transcoding latency in the serving path. Cold start is near-instant.

### 3.7 Cold Start (Player Connects Mid-Track)

When a new client requests `/stream/{id}`, the server returns the next chunk from the station's CURRENT position — not the beginning of the current track. This means:
- Audio starts at the live position (same as other listeners — radio model)
- No "catch-up" period
- Player joins mid-song (like tuning a real radio)

### 3.8 Station Switching

```
1. Player selects new station in UI
2. Lua: set playing = false (stops fetch loop)
3. Lua: flush buffer (discard any pre-fetched old-station data)
4. Lua: reset DFPWM decoder state (prevent audio garbage)
5. Lua: set station_id = new station
6. Lua: set playing = true (restarts fetch loop against new station)
7. First chunk arrives in <50ms (read from disk, instant)
8. Audio plays: total switch time <200ms
```

**Critical: DFPWM decoder state reset.** The DFPWM decoder is stateful — connecting to a new stream with stale state produces garbage. `cc.audio.dfpwm.make_decoder()` creates a fresh decoder on every station switch.

---

## 4. Lua Client Design

### 4.1 Architecture

```lua
-- Three parallel coroutines via parallel.waitForAll()
parallel.waitForAll(
    audioLoop,      -- fetch + decode + play
    uiLoop,         -- draw terminal UI + handle input
    metadataLoop    -- poll /now-playing every 5s
)
```

### 4.2 Terminal UI Wireframe

```
┌─────────────────────────────────┐
│  ♪ RADIO ♪         [98.7 FM]   │
│─────────────────────────────────│
│                                 │
│  ► Lo-Fi Beats                  │
│    Now Playing:                 │
│    "Midnight Stroll"            │
│     - ChillHop Records          │
│                                 │
│  ▮▮▮▮▮▮▮▮▮░░░░  Vol: ████░ 80% │
│─────────────────────────────────│
│  [↑/↓] Station  [←/→] Volume   │
│  [Enter] Select  [Q] Quit      │
└─────────────────────────────────┘
```

- Colors: CC terminal supports 16 colors. Header in yellow, station name in white, metadata in light gray, controls in cyan.
- Responsive to terminal size (standard CC computer: 51×19, advanced: 51×19 or monitor-extended)
- "Frequency" numbers are cosmetic (98.7, 101.3, etc.) — adds radio charm

### 4.3 Peripheral Detection

```lua
local speaker = peripheral.find("speaker")
if not speaker then
    print("No speaker found! Place a Speaker next to this computer.")
    return
end
```

On startup, the client scans for any adjacent speaker peripheral. If none found, displays a helpful error. Periodically re-checks in case speaker is destroyed mid-playback.

### 4.4 Volume Control

CC:Tweaked `speaker.playAudio(samples, volume)` accepts a volume parameter (0.0 - 3.0, default 1.0). Volume is client-side — no server interaction needed.

### 4.5 Error States & Recovery

| Error | Player Sees | Recovery |
|-------|-------------|----------|
| HTTP timeout | "Tuning..." with spinner | Auto-retry in 1s, up to 5 attempts |
| 503 (no tracks) | "Station loading..." | Poll every 10s until 200 |
| 404 (bad station) | "Station not found" | Return to station list |
| Speaker removed | "No speaker! Reconnect..." | Re-scan peripherals every 3s |
| Server unreachable | "Connecting..." with retry count | Exponential backoff 1s→2s→4s→8s→8s |
| Buffer underrun | Brief silence, then resumes | Normal — next chunk fills gap |

### 4.6 Estimated Client Size

- Core audio loop: ~40 lines
- UI rendering: ~60 lines
- Metadata polling: ~15 lines
- Error handling: ~25 lines
- Station selection: ~30 lines
- Config/constants: ~10 lines
- **Total: ~180 lines of Lua**

---

## 5. Python Server Design

### 5.1 Architecture

```python
# Core components:
# 1. StationManager — loads config, manages station playback state
# 2. MusicLibrary — yt-dlp acquisition, FFmpeg transcoding, cache management
# 3. MetadataService — external metadata lookup + cache
# 4. HTTPServer — serves /stations, /stream/{id}, /now-playing/{id}
# 5. AutoDJ — playlist rotation, shuffle, track advancement (Phase 3+)

class MusicLibrary:
    """Manages acquisition and transcoding pipeline."""
    raw_dir: Path        # Downloaded originals (MP3/OPUS/etc)
    dfpwm_dir: Path      # Transcoded DFPWM files
    metadata_cache: dict  # track_id → {title, artist, album, duration}

class StationState:
    """Playback state for one station."""
    playlist: list[Path]  # Ordered DFPWM file paths
    current_track: int    # Index into playlist
    current_offset: int   # Byte position in current track file
    listeners: int        # Active reader count

class RadioServer:
    """HTTP server (aiohttp)."""
    stations: dict[str, StationState]
    library: MusicLibrary
    config: StationConfig  # loaded from YAML
```

### 5.2 Music Acquisition (yt-dlp)

```bash
# Download best audio, output as opus (smallest for caching)
yt-dlp --extract-audio --audio-format opus --audio-quality 0 \
  -o "music/raw/%(playlist_title)s/%(title)s.%(ext)s" \
  "https://youtube.com/playlist?list=PLxxxxxx"
```

**Acquisition modes:**
- **Playlist URL:** Download all tracks from a YouTube/SoundCloud playlist
- **Search query:** `yt-dlp "ytsearch20:lo-fi hip hop"` — grab top 20 results
- **Single track:** Direct URL for specific songs
- **Channel:** All uploads from a channel (filtered by duration)

**Caching strategy:**
- Raw downloads kept in `music/raw/{station_id}/`
- Transcoded DFPWM in `music/dfpwm/{station_id}/`
- Metadata JSON in `music/metadata/{station_id}/`
- Once transcoded, raw file can be deleted (configurable retention)
- De-duplication by content hash (same track won't download twice)

### 5.3 Transcoding Pipeline (Offline)

```python
async def transcode_track(raw_path: Path, station_id: str) -> Path:
    """Convert any audio file to DFPWM. Runs as background task."""
    dfpwm_path = DFPWM_DIR / station_id / f"{raw_path.stem}.dfpwm"
    
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-i", str(raw_path),
        "-f", "dfpwm", "-ar", "48000", "-ac", "1",
        str(dfpwm_path),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE
    )
    await proc.wait()
    return dfpwm_path
```

Transcoding is a **background pipeline** — it runs whenever new tracks are acquired, NOT in the audio serving path. The serving path only reads pre-transcoded `.dfpwm` files from disk.

### 5.4 Metadata Service

**Source:** External metadata API for enriching track info beyond what yt-dlp provides.

**Options (in preference order):**
1. **yt-dlp metadata** (free, always available) — title, uploader, duration, thumbnail URL. Available at download time.
2. **MusicBrainz** (free, open) — artist, album, genre, release year. Lookup by title+artist.
3. **Last.fm API** (free tier) — similar artists, tags, play counts. Enrichment data.

**Caching:** All metadata cached as JSON alongside DFPWM files. Never re-fetched unless explicitly refreshed. One lookup per track at acquisition time.

```json
// music/metadata/lofi/midnight-stroll.json
{
  "title": "Midnight Stroll",
  "artist": "ChillHop Records",
  "album": "Late Night Vibes Vol. 3",
  "duration_seconds": 214,
  "genre": "lo-fi hip hop",
  "year": 2023,
  "source_url": "https://youtube.com/watch?v=xxxxx",
  "acquired_at": "2026-05-18T12:00:00Z",
  "dfpwm_path": "music/dfpwm/lofi/midnight-stroll.dfpwm",
  "dfpwm_size_bytes": 1284096
}
```

### 5.5 Station Configuration (`stations.yaml`)

```yaml
server:
  host: "127.0.0.1"
  port: 5309

metadata:
  musicbrainz: true
  lastfm_api_key: ""  # optional enrichment

stations:
  - id: lofi
    name: "Lo-Fi Beats"
    genre: "Electronic"
    frequency: "98.7"
    description: "Chill beats to mine to"
    sources:
      - type: playlist
        url: "https://youtube.com/playlist?list=PLlofi123"
      - type: search
        query: "lo-fi hip hop instrumental"
        max_tracks: 30
    rotation: shuffle    # shuffle | sequential | weighted
    max_tracks: 100      # cap per station

  - id: jazz
    name: "Smooth Jazz FM"
    genre: "Jazz"
    frequency: "101.3"
    description: "Late night jazz vibes"
    sources:
      - type: playlist
        url: "https://youtube.com/playlist?list=PLjazz456"
    rotation: sequential
    max_tracks: 50

  - id: rock
    name: "Classic Rock Radio"
    genre: "Rock"
    frequency: "104.9"
    description: "The classics never die"
    sources:
      - type: search
        query: "classic rock full songs"
        max_tracks: 50
      - type: search
        query: "70s rock hits"
        max_tracks: 30
    rotation: shuffle
    max_tracks: 80
```

**Hot-reload:** Server watches `stations.yaml` with `watchdog` library. New stations trigger acquisition. Removed stations stop playback. Changed sources trigger re-acquisition.

### 5.6 Resource Estimates

| Metric | Value | Notes |
|--------|-------|-------|
| DFPWM file size | ~1.1 MB per 3-min track | 6 KB/s × 180s |
| 100 tracks cached | ~110 MB disk | Trivial |
| 500 tracks cached | ~550 MB disk | Still fine |
| RAM (server idle) | ~50 MB | Python + aiohttp |
| RAM (serving) | ~60 MB | + station state + metadata cache |
| CPU (serving) | <1% | Just reading files from disk |
| CPU (transcoding) | ~30% per track | Background, one at a time |
| Network (acquisition) | Burst during download | yt-dlp, then idle |

No FFmpeg running at serve time. The serving path is pure file I/O — trivially light.

---

## 6. Metadata System

### 6.1 Data Flow

```
yt-dlp (at download) ──► title, artist, duration ──► metadata/{station}/{track}.json
MusicBrainz (lookup)  ──► album, genre, year     ──►        (cached)
                                                              │
Lua metadataLoop (5s poll) ◄── GET /now-playing/{id} ◄───────┘
         │
         ▼
    UI render (title, artist, album)
```

### 6.2 Why Separate from Audio

Metadata rides a separate endpoint (not in audio stream headers) because:
1. **Timing independence:** Track change happens at chunk boundary. Metadata can update immediately.
2. **UI independence:** Metadata poll runs in its own coroutine. If audio stalls, UI still updates.
3. **Richer data:** JSON allows album, duration, progress — more than fits in HTTP headers.

### 6.3 Station Discovery

Players discover available stations via `/stations`. The Lua client shows the full list on startup. No need for in-game items or special blocks — just run the `radio` program on any CC computer with an adjacent speaker.

For discoverability in the Minecraft world:
- Place signs near radio computers: "Right-click to listen!"
- The `radio` program can be placed on the computer's startup (auto-runs on chunk load)

---

## 7. Error Handling & Resilience

### 7.1 Failure Cascade Prevention

**Design principle:** No single failure kills more than one listener's experience.

| Failure | Blast Radius | Mitigation |
|---------|-------------|------------|
| yt-dlp download fails | Zero (background) | Retry with backoff. Station plays from existing cache. |
| FFmpeg transcode fails | Zero (background) | Skip track, log error, continue with next. |
| Track file corrupted | One station (briefly) | Detect short/zero reads, skip to next track. |
| Python server crash | All listeners | systemd auto-restart. Lua clients auto-retry. |
| One Lua client crash | One player | Other players unaffected. |
| Speaker destroyed | One player's audio | Lua detects, shows "No speaker!" |
| Disk full | New downloads fail | Monitor disk space in /health. Alert threshold. |

### 7.2 Graceful Degradation

```
Level 0 (healthy):    Audio + metadata flowing normally
Level 1 (buffering):  Brief network hiccup, pre-fetched chunk covers it
Level 2 (stalling):   Server restarting, "Tuning..." displayed
Level 3 (offline):    Server down, "Station offline", auto-retry
```

### 7.3 DFPWM Decoder State Reset

**Critical invariant:** On ANY reconnection or station switch, create a fresh DFPWM decoder. The DFPWM algorithm is stateful — feeding mid-stream data to a decoder initialized at a different point produces noise/static.

```lua
-- On station switch or reconnect:
decoder = dfpwm.make_decoder()  -- fresh state
buffer:flush()                  -- discard stale data
```

---

## 8. Phased Delivery Plan

### Phase 1: MVP — "It plays music" (Weekend 1)

**Goal:** One station playing pre-downloaded music, charming UI, kid-proof.

**Architecture:** Manually download a few tracks with yt-dlp, transcode to DFPWM offline, Python server reads from disk and serves chunks. Zero runtime complexity — just a file server with state.

**Entry criteria:** FFmpeg 5.1+ installed, yt-dlp installed, CC:Tweaked on Minecraft server.

**Pre-work (one-time, manual):**
```bash
# Download some tracks
yt-dlp --extract-audio --audio-format opus \
  -o "music/raw/lofi/%(title)s.%(ext)s" \
  "https://youtube.com/playlist?list=PLlofi123"

# Transcode to DFPWM
for f in music/raw/lofi/*.opus; do
  ffmpeg -i "$f" -f dfpwm -ar 48000 -ac 1 \
    "music/dfpwm/lofi/$(basename "${f%.*}").dfpwm"
done
```

**Deliverables:**
- Python server: reads DFPWM files sequentially, serves 16KB chunks
- `/stream/{id}` endpoint (reads from disk, advances position, wraps at EOF to next track)
- `/stations` endpoint (single station from YAML)
- `/now-playing/{id}` endpoint (reads from metadata JSON files — title + artist)
- Lua client: fetch loop + speaker playback + double-buffer + pre-fetch chunk 0
- Terminal UI: station name, now-playing, volume control, colored retro look
- Auto-retry on HTTP timeout (resilient from day 1)
- Speaker detection with helpful error
- `pcall()` wrapping on speaker calls

**Exit criteria:** Player runs `radio`, hears music within 1 second, sees track name + artist, can adjust volume. Audio loops through playlist without gaps. A kid can use it unaided.

**Estimated effort:** 8-10 hours

**Dependencies:**
```
pip install aiohttp pyyaml
yt-dlp (for offline track acquisition)
FFmpeg 5.1+ (for offline DFPWM conversion)
# Neither yt-dlp nor FFmpeg needed at runtime in Phase 1!
```

---

### Phase 2: Multi-Station + Auto-Acquire — "Pick your vibe" (Weekend 2)

**Goal:** Multiple stations, automated yt-dlp acquisition, metadata enrichment.

**Architecture upgrade:** Python server automatically downloads tracks from configured sources (playlist URLs, search queries). Background pipeline: download → transcode → add to station playlist. Lua client gets station list and switching.

**Entry criteria:** Phase 1 working and tested.

**Deliverables:**
- `stations.yaml` with 3+ stations, each with source URLs/queries
- Background acquisition worker (yt-dlp downloads on schedule or on-demand)
- Background transcoding pipeline (auto-converts new downloads to DFPWM)
- MusicBrainz metadata lookup + JSON caching per track
- Station list UI (arrow keys to browse, Enter to select)
- Now-playing display (title + artist + album from cached metadata)
- Station switching with proper buffer flush + decoder reset
- Track rotation logic (shuffle or sequential per station config)
- Hot-reload for station config
- "Frequency" cosmetic display (98.7 FM, etc.)
- De-duplication (same track won't download/transcode twice)

**Exit criteria:** Player browses 3+ stations with 20+ tracks each, switches in <200ms, sees metadata. Adding a new playlist URL to YAML triggers automatic acquisition.

**Estimated effort:** 10-12 hours

---

### Phase 3: Auto-DJ + Resilience — "It just works" (Weekend 3)

**Goal:** Feels like real radio — auto-discovers new music, recovers from everything.

**Entry criteria:** Phase 2 working with 3+ stations.

**Deliverables:**
- Auto-DJ: periodic discovery of new tracks (daily/weekly per station config)
- Track aging: prioritize newer additions, retire stale tracks
- `/health` endpoint with full diagnostics (cache size, track counts, last acquisition)
- Proper logging (Python `logging` module, file rotation)
- Fan-out tested: 5 CC computers same station simultaneously
- Graceful degradation UI states (Tuning.../Offline/Connecting...)
- CC computer unload detection (`os.clock()` gap > 5s → flush + reset)
- Exponential backoff on all retry paths
- systemd service running stable
- Disk space monitoring + cleanup of old raw files
- Acquisition rate limiting (don't hammer YouTube)

**Exit criteria:** Server runs 24+ hours. Auto-acquires new tracks daily. 5 concurrent listeners no issues. Disk stays clean.

**Estimated effort:** 8-10 hours

---

### Phase 4: Experience — "Radio charm" (Weekend 4)

**Goal:** The UI looks and feels like a real retro radio.

**Entry criteria:** Phase 3 stable.

**Deliverables:**
- Full-color terminal UI with the wireframe design from §4.2
- Animated "tuning" effect on station switch (visual flair)
- Track progress bar (position within current song)
- Startup animation ("Warming up tubes...")
- Favorite stations (saved to CC computer's local storage)
- Auto-resume last station on computer reboot
- "Up next" display (next track in playlist)
- Genre-based color themes per station

**Exit criteria:** A kid says "cool!" — the UI delights on first encounter.

**Estimated effort:** 8-10 hours

---

### Phase 5: Social & Advanced — "Our radio station" (Weekend 5+)

**Goal:** Multiplayer features, song requests, extensibility.

**Entry criteria:** Phase 4 complete.

**Deliverables:**
- Listener count displayed per station
- Song request system (`/request/{station_id}` POST with search query → yt-dlp → queue)
- Request queue management (next requested track plays after current)
- "Who's listening" display
- Redstone integration: speaker volume controlled by redstone signal
- Multi-speaker spatial setup (place 2 speakers for wider sound)
- Public playlist display on a monitor peripheral
- Integration with game console home screen
- Admin commands (skip track, ban track, clear queue)

**Exit criteria:** Friends request songs and hear them play. Multiple speakers work. Social interaction around the radio.

**Estimated effort:** 12-16 hours (can be split across multiple weekends)

---

## 9. Adversarial Analysis (RedTeam Findings)

### 9.1 Ranked Risk Register

| # | Risk | Severity | Likelihood | Player Experience | Mitigation |
|---|------|----------|------------|-------------------|------------|
| 1 | **CC computer unloaded mid-stream** — player walks away, stale buffer + corrupted DFPWM state on return | Critical | Almost Certain | Garbage audio burst, then silence | Detect unload via `os.clock()` delta > 5s. Flush buffers, reset decoder, request fresh chunk |
| 2 | **yt-dlp blocked/rate-limited** — YouTube throttles or blocks downloads | High | Likely | No new tracks acquired (existing cache still plays) | Backoff + retry. Rotate user agents. Cache means service is never DOWN, just stale |
| 3 | **Disk full from downloads** — uncapped acquisition fills disk | High | Possible | Server errors on new writes | Max tracks per station (capped in YAML). Auto-cleanup of raw files after transcode. Disk monitoring in /health |
| 4 | **Station switch plays old audio** — pre-fetched buffer contains old station data | High | Almost Certain | Wrong station audio bleeds | On switch: flush buffers, block playback, reset decoder. Silence > wrong audio |
| 5 | **Corrupted DFPWM file** — bad transcode produces garbage audio | Medium | Possible | Distorted/static audio on one track | Validate DFPWM file size matches expected duration. Skip + log bad tracks |
| 6 | **yt-dlp downloads wrong content** — search query returns unrelated results | Medium | Likely | Random video audio plays as "music" | Filter by duration (2-8 min). Manual review of initial downloads. Allow blocklist |
| 7 | **Metadata lookup fails** — MusicBrainz down or no match | Low | Possible | "Unknown Artist" displayed | Fall back to yt-dlp metadata (always available). Cache aggressively |
| 8 | **DFPWM decoder state on reconnect** — stateful codec produces brief distortion | Medium | Likely | Brief distortion (~0.5s) | Fresh decoder on every reconnect/switch |
| 9 | **Multiple stations exhausting disk** — 5 stations × 100 tracks × original + DFPWM | Medium | Possible | Disk pressure | Delete raw after transcode (configurable). DFPWM is tiny (~1MB/track) |
| 10 | **Speaker peripheral destroyed mid-playback** | Low | Possible | Error spam | Wrap `playAudio()` in `pcall()`. Poll for speaker, auto-resume |

### 9.2 Top 5 Design Requirements (from RedTeam)

1. **Cache-first architecture** — The serving path NEVER depends on the internet. If yt-dlp is blocked, YouTube is down, or the host has no internet, all cached tracks still play perfectly. Downloads are background enrichment, not runtime dependency.
2. **Atomic station switch with buffer flush** — Stop speaker, discard buffers, block playback, reset decoder. Wrong-station bleed is the most noticeable UX bug.
3. **Disk management** — Cap tracks per station. Auto-cleanup raw files. Monitor in /health. Never let the music cache starve the Minecraft server of disk.
4. **Content filtering** — Duration filter (2-8 min), blocklist support, manual approval mode for initial station population.
5. **Graceful degradation → silence, never garbage** — Every failure produces silence, not distortion. Three client states: PLAYING, BUFFERING, OFFLINE.

---

## 10. Council Synthesis — Key Architecture Decisions

*5-member council debate: Systems Engineer, Game Developer, Embedded/IoT Developer, Parent/UX Designer, Pragmatic Weekend Hacker.*

### 10.1 Consensus Decisions

| Decision | Choice | Confidence | Key Argument |
|----------|--------|------------|--------------|
| Transport protocol | **HTTP GET (pull)** | High | Matches speaker hardware physics; stateless = self-healing |
| Audio source | **yt-dlp + local cache** | High | No external runtime deps. Internet needed only for acquisition |
| Transcoding | **FFmpeg offline (not in serving path)** | High | Zero CPU at serve time. Serving = read file. Nothing can fail |
| Metadata | **yt-dlp metadata + MusicBrainz enrichment, all cached as JSON** | High | Free, open, cached forever. Never re-fetched at serve time |
| Server-side playback | **Shared position (radio model)** | High | All listeners hear same thing simultaneously — real radio feel |
| Client-side buffering | **Double-buffer + pre-fetch chunk 0** | High | Pre-fetch eliminates first-play silence gap |
| Station config | **YAML hot-reload** | High | Human-readable, teachable |

### 10.2 Key Insight: Cache-First Makes Everything Simpler

By downloading and transcoding everything BEFORE serving, the runtime system is trivially simple:
- **Server:** Read bytes from file. Serve them. That's it.
- **No FFmpeg processes running.** No streaming connections. No real-time transcoding.
- **If the internet goes away:** Everything still works from cache.
- **CPU usage at serve time:** Effectively zero.
- **Failure modes:** Reduced to "can Python read a file?" — almost nothing can break.

The complexity (yt-dlp, FFmpeg, metadata lookup) is pushed to a **background pipeline** that runs independently. Even if the pipeline fails completely, the radio keeps playing from its existing library.

### 10.3 Phasing Insight

- **Phase 1:** Manual acquisition (run yt-dlp by hand) + serve from cache = jukebox
- **Phase 2:** Automated acquisition (server runs yt-dlp on schedule) = auto-replenishing jukebox
- **Phase 3+:** Auto-DJ logic (discovery, rotation, aging) = feels like real radio

Same serving code throughout. The evolution is entirely in the acquisition pipeline sophistication.

---

## 11. Dependencies & Setup

### 11.1 Python Server

```
Python 3.10+
aiohttp >= 3.9
pyyaml >= 6.0
watchdog >= 3.0 (for config hot-reload)
musicbrainzngs >= 0.7 (metadata enrichment, Phase 2+)
```

### 11.2 System Tools

```
FFmpeg >= 5.1 (DFPWM codec support — used offline for transcoding)
yt-dlp (latest — music acquisition)
```

### 11.3 Minecraft Server

```
Minecraft 1.19+ (or whatever CC:Tweaked supports)
CC:Tweaked mod (latest)
Server config: computercraft-server.toml
  [[http.rules]]
    host = "127.0.0.1"
    port = 5309
    action = "allow"
```

---

## 12. Server Setup & Deployment

### 12.1 Overview

The radio server runs on the same host as Minecraft under a dedicated `icecast` system user. The install script handles user creation, directory structure, Python venv, system tool verification, systemd unit, and CC:Tweaked HTTP allowlist.

### 12.2 Service User

```bash
sudo useradd --system --shell /usr/sbin/nologin --home-dir /opt/mcradio icecast
sudo mkdir -p /opt/mcradio/{server,music/{raw,dfpwm,metadata},logs}
sudo chown -R icecast:icecast /opt/mcradio
```

### 12.3 Directory Structure

```
/opt/mcradio/
├── server/
│   ├── radio_server.py      # Main Python server
│   ├── stations.yaml        # Station config
│   ├── requirements.txt     # Python deps
│   └── venv/                # Python virtual environment
├── music/
│   ├── raw/                 # Downloaded originals (can be cleaned)
│   │   ├── lofi/
│   │   └── jazz/
│   ├── dfpwm/              # Transcoded DFPWM (the served files)
│   │   ├── lofi/
│   │   └── jazz/
│   └── metadata/           # Cached track metadata JSON
│       ├── lofi/
│       └── jazz/
└── logs/
    └── radio-server.log
```

### 12.4 Install Script (`setup.sh`)

```bash
#!/bin/bash
set -euo pipefail

# Minecraft Radio Server — Setup Script
# Usage: sudo bash setup.sh

RADIO_USER="icecast"
RADIO_HOME="/opt/mcradio"
RADIO_PORT=5309
MC_SERVER_DIR=""  # Set this to your Minecraft server path

echo "=== Minecraft Radio Server Setup ==="
echo "Port: $RADIO_PORT (5-3-0-9... Jenny, I got your number)"
echo ""

# --- 1. Create service user ---
if id "$RADIO_USER" &>/dev/null; then
    echo "[OK] User '$RADIO_USER' already exists"
else
    useradd --system --shell /usr/sbin/nologin --home-dir "$RADIO_HOME" "$RADIO_USER"
    echo "[OK] Created system user '$RADIO_USER'"
fi

# --- 2. Create directory structure ---
mkdir -p "$RADIO_HOME"/{server,music/{raw,dfpwm,metadata},logs}
chown -R "$RADIO_USER:$RADIO_USER" "$RADIO_HOME"
echo "[OK] Directory structure at $RADIO_HOME"

# --- 3. Install system dependencies ---
echo "[..] Installing system packages..."
if command -v apt-get &>/dev/null; then
    apt-get update -qq
    apt-get install -y -qq python3 python3-venv ffmpeg
elif command -v dnf &>/dev/null; then
    dnf install -y python3 python3-pip ffmpeg
elif command -v pacman &>/dev/null; then
    pacman -S --noconfirm python ffmpeg
else
    echo "[!!] Unknown package manager. Install manually: python3, ffmpeg"
    exit 1
fi

# Install yt-dlp (latest from pip, not distro package)
pip3 install -q --upgrade yt-dlp
echo "[OK] System packages installed"

# --- 4. Verify FFmpeg DFPWM support ---
if ffmpeg -formats 2>/dev/null | grep -q dfpwm; then
    echo "[OK] FFmpeg has DFPWM codec support"
else
    echo "[!!] FFmpeg does NOT support DFPWM. Need FFmpeg 5.1+"
    echo "     Current: $(ffmpeg -version | head -1)"
    exit 1
fi

# --- 5. Verify yt-dlp ---
if command -v yt-dlp &>/dev/null; then
    echo "[OK] yt-dlp available: $(yt-dlp --version)"
else
    echo "[!!] yt-dlp not found. Install: pip3 install yt-dlp"
    exit 1
fi

# --- 6. Python venv + deps ---
sudo -u "$RADIO_USER" python3 -m venv "$RADIO_HOME/server/venv"
sudo -u "$RADIO_USER" "$RADIO_HOME/server/venv/bin/pip" install -q \
    aiohttp pyyaml watchdog musicbrainzngs yt-dlp
echo "[OK] Python venv created with dependencies"

# --- 7. Install systemd unit ---
cat > /etc/systemd/system/mcradio.service << 'EOF'
[Unit]
Description=Minecraft Radio Server (yt-dlp + DFPWM + HTTP)
After=network.target

[Service]
Type=simple
User=icecast
Group=icecast
WorkingDirectory=/opt/mcradio/server
ExecStart=/opt/mcradio/server/venv/bin/python radio_server.py
Restart=always
RestartSec=3
StandardOutput=append:/opt/mcradio/logs/radio-server.log
StandardError=append:/opt/mcradio/logs/radio-server.log

# Hardening
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=/opt/mcradio
PrivateTmp=yes
ProtectHome=yes
ProtectKernelTunables=yes
ProtectControlGroups=yes

# Resource limits
MemoryMax=512M
CPUQuota=80%
TasksMax=32

[Install]
WantedBy=multi-user.target
EOF

echo "[OK] Systemd unit installed: mcradio.service"

# --- 8. Logrotate ---
cat > /etc/logrotate.d/mcradio << 'EOF'
/opt/mcradio/logs/*.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
    copytruncate
}
EOF
echo "[OK] Logrotate configured (7-day retention)"

# --- 9. CC:Tweaked HTTP config ---
if [ -n "$MC_SERVER_DIR" ] && [ -d "$MC_SERVER_DIR" ]; then
    CC_CONFIG="$MC_SERVER_DIR/config/computercraft-server.toml"
    if [ -f "$CC_CONFIG" ]; then
        if grep -q "5309" "$CC_CONFIG" 2>/dev/null; then
            echo "[OK] CC:Tweaked config already allows port 5309"
        else
            echo ""
            echo "[!!] Add this to $CC_CONFIG (BEFORE any deny rules):"
            echo '    [[http.rules]]'
            echo '    host = "127.0.0.1"'
            echo '    port = 5309'
            echo '    action = "allow"'
            echo ""
        fi
    fi
else
    echo "[NOTE] Set MC_SERVER_DIR to auto-check CC:Tweaked config"
    echo "       Manual config needed in computercraft-server.toml:"
    echo '       [[http.rules]]'
    echo '       host = "127.0.0.1"'
    echo '       port = 5309'
    echo '       action = "allow"'
fi

# --- 10. Enable service ---
systemctl daemon-reload
systemctl enable mcradio.service
echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. Place radio_server.py in $RADIO_HOME/server/"
echo "  2. Edit $RADIO_HOME/server/stations.yaml"
echo "  3. Download some tracks:"
echo "     sudo -u icecast yt-dlp --extract-audio --audio-format opus \\"
echo "       -o '$RADIO_HOME/music/raw/lofi/%(title)s.%(ext)s' \\"
echo "       'https://youtube.com/playlist?list=YOUR_PLAYLIST'"
echo "  4. Transcode to DFPWM:"
echo "     for f in $RADIO_HOME/music/raw/lofi/*.opus; do"
echo "       sudo -u icecast ffmpeg -i \"\$f\" -f dfpwm -ar 48000 -ac 1 \\"
echo "         \"$RADIO_HOME/music/dfpwm/lofi/\$(basename \"\${f%.*}\").dfpwm\""
echo "     done"
echo "  5. Start: sudo systemctl start mcradio"
echo "  6. Check: curl http://127.0.0.1:$RADIO_PORT/health"
echo "  7. In Minecraft: place Computer + Speaker, run 'radio'"
echo ""
echo "Management:"
echo "  Status:  sudo systemctl status mcradio"
echo "  Logs:    tail -f $RADIO_HOME/logs/radio-server.log"
echo "  Restart: sudo systemctl restart mcradio"
```

### 12.5 systemd Service Unit (Detail)

```ini
[Unit]
Description=Minecraft Radio Server (yt-dlp + DFPWM + HTTP)
After=network.target

[Service]
Type=simple
User=icecast
Group=icecast
WorkingDirectory=/opt/mcradio/server
ExecStart=/opt/mcradio/server/venv/bin/python radio_server.py
Restart=always
RestartSec=3
StandardOutput=append:/opt/mcradio/logs/radio-server.log
StandardError=append:/opt/mcradio/logs/radio-server.log

# Hardening — minimize blast radius
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=/opt/mcradio
PrivateTmp=yes
ProtectHome=yes
ProtectKernelTunables=yes
ProtectControlGroups=yes

# Resource limits
MemoryMax=512M
CPUQuota=80%
TasksMax=32

[Install]
WantedBy=multi-user.target
```

**Key design decisions:**
- `Restart=always` + `RestartSec=3` — auto-recovery
- `MemoryMax=512M` — caps memory (Python + any background transcoding)
- `CPUQuota=80%` — prevents radio from starving Minecraft of CPU during transcoding bursts
- No `Wants=icecast2.service` — no Icecast dependency at all
- Hardening prevents reading `/home`, writing outside `/opt/mcradio`, or escalating privileges

### 12.6 CC:Tweaked HTTP Configuration

In `<minecraft-server>/config/computercraft-server.toml`:

```toml
[[http.rules]]
host = "127.0.0.1"
port = 5309
action = "allow"
```

This allows CC:Tweaked computers to reach the radio server on localhost only. The rule must appear BEFORE any deny rules for `127.0.0.0/8` (first match wins in CC:Tweaked).

### 12.7 Firewall Notes

No firewall changes needed. All traffic is localhost:
- CC:Tweaked → Radio server: `127.0.0.1:5309`
- Radio server → Internet: outbound only (yt-dlp downloads, metadata lookups)
- Players connect to Minecraft on its own port (25565) as usual

The radio adds ZERO inbound network surface area to the host.

---

## 13. Testing Strategy (Per Phase)

| Phase | Test | Method |
|-------|------|--------|
| 1 | Audio plays | Manual: run `radio`, hear music |
| 1 | Volume works | Adjust volume, confirm audible change |
| 1 | Timeout recovery | Kill Python server, restart, confirm auto-reconnect |
| 1 | Track advancement | Let track end, confirm next track starts seamlessly |
| 2 | Station switching | Switch 5 times rapidly, no audio garbage |
| 2 | Metadata accuracy | Check now-playing matches actual track |
| 2 | Auto-acquisition | Add playlist URL to YAML, confirm tracks appear |
| 2 | Hot reload | Add station to YAML, confirm appears in list |
| 3 | Concurrent listeners | 5 CC computers same station, no drops |
| 3 | 24-hour soak | Leave running overnight, check in morning |
| 3 | Auto-DJ rotation | Confirm new tracks get added over 24h |
| 3 | Disk management | Confirm raw files cleaned after transcode |
| 4 | UX test (kid) | Hand to a kid, observe if they can use it unaided |
| 5 | Request system | Submit request, confirm it downloads + plays |

---

## 14. Future Extensibility Hooks (Not Implemented)

Documented here so future phases know where to attach:

- **Song requests:** `/request/{station_id}` POST with search query → yt-dlp → transcode → insert into station queue.
- **Redstone volume:** Lua reads redstone signal level (0-15), maps to volume (0.0-3.0).
- **Multi-speaker:** Lua scans for multiple speakers, sends same audio to all.
- **Monitor display:** Separate `radio-display` program shows now-playing on an adjacent CC monitor.
- **Home screen integration:** Game console main menu launches `radio` as a sub-program.
- **Liked tracks:** Player "likes" a track → it gets weighted higher in rotation.
- **Cross-station shuffle:** "Radio Roulette" mode that plays random tracks from ALL stations.
- **Scheduled programming:** Time-based station changes (morning jazz, evening rock).

---

## Appendix A: Sequence Diagrams

### A.1 Normal Playback Flow

```
Lua Client          Python Server           Filesystem
    │                    │                       │
    │ GET /stations      │                       │
    │───────────────────►│                       │
    │◄───────────────────│                       │
    │  [station list]    │                       │
    │                    │                       │
    │ GET /stream/lofi   │                       │
    │───────────────────►│                       │
    │                    │  read(16KB)            │
    │                    │──────────────────────►│
    │                    │◄──────────────────────│
    │◄───────────────────│  [16KB DFPWM]         │
    │                    │                       │
    │ speaker.playAudio()│                       │
    │ ~~~~2.73s~~~~      │                       │
    │                    │                       │
    │ [speaker_audio_empty]                      │
    │                    │                       │
    │ GET /stream/lofi   │                       │
    │───────────────────►│  read(next 16KB)      │
    │                    │──────────────────────►│
    │◄───────────────────│  [16KB DFPWM]         │
    │ ...repeats...      │                       │
```

### A.2 Track Advancement (End of Track)

```
Lua Client          Python Server           Filesystem
    │                    │                       │
    │ GET /stream/lofi   │                       │
    │───────────────────►│                       │
    │                    │  read(16KB) → EOF!     │
    │                    │  advance to next track │
    │                    │  read(16KB) from start │
    │                    │──────────────────────►│
    │◄───────────────────│  [16KB from track 2]  │
    │                    │                       │
    │ speaker.playAudio()│  ← seamless transition │
```

### A.3 Background Acquisition (Phase 2+)

```
AcquisitionWorker       yt-dlp              FFmpeg         Filesystem
    │                    │                    │                │
    │  [scheduled/triggered]                  │                │
    │  download(url)     │                    │                │
    │───────────────────►│                    │                │
    │                    │ [downloads audio]   │                │
    │◄───────────────────│ track.opus          │                │
    │                    │                    │                │
    │  transcode(track)  │                    │                │
    │────────────────────────────────────────►│                │
    │                    │                    │ [opus→dfpwm]   │
    │◄────────────────────────────────────────│ track.dfpwm    │
    │                                                          │
    │  write metadata JSON                                     │
    │─────────────────────────────────────────────────────────►│
    │                                                          │
    │  add to station playlist                                 │
    │  (next rotation includes new track)                      │
```

### A.4 Station Switch Flow

```
Lua Client          Python Server
    │                    │
    │ [User presses ↓, selects "jazz"]
    │                    │
    │ playing = false    │
    │ buffer:flush()     │
    │ decoder = dfpwm.make_decoder()
    │ station_id = "jazz"│
    │ playing = true     │
    │                    │
    │ GET /stream/jazz   │
    │───────────────────►│
    │◄───────────────────│ [16KB from jazz at current position]
    │                    │
    │ speaker.playAudio()│ ← new station audio in <200ms
```
