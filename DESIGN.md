# Minecraft Icecast Radio — System Design Document

> **Project:** In-game radio station for a private Minecraft server
> **Components:** Lua client (CC:Tweaked) · Python bridge server · Icecast streaming
> **Author:** vakermit · **Version:** 1.0 · **Date:** 2026-05-18

---

## 1. System Architecture

### 1.1 Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     MINECRAFT SERVER (LAN)                       │
│                                                                  │
│  ┌──────────────┐     HTTP GET      ┌──────────────────────┐   │
│  │  CC:Tweaked   │ ◄──────────────── │   Python Bridge      │   │
│  │  Computer     │   16KB DFPWM      │   Server             │   │
│  │              │   chunks           │                      │   │
│  │  ┌────────┐ │                    │  ┌────────────────┐  │   │
│  │  │ radio  │ │   GET /metadata    │  │ Station Worker │  │   │
│  │  │ program│ │ ◄──────────────── │  │  (per station) │  │   │
│  │  └────┬───┘ │   JSON             │  │                │  │   │
│  │       │     │                    │  │ Icecast ──►    │  │   │
│  │  ┌────▼───┐ │                    │  │ FFmpeg  ──►    │  │   │
│  │  │Speaker │ │                    │  │ Ring Buffer    │  │   │
│  │  │Periph. │ │                    │  └────────────────┘  │   │
│  │  └────────┘ │                    │                      │   │
│  └──────────────┘                    └──────────┬───────────┘   │
│                                                  │              │
└──────────────────────────────────────────────────┼──────────────┘
                                                   │ HTTP GET
                                                   ▼
                                         ┌──────────────────┐
                                         │  Icecast Server  │
                                         │  (local or LAN)  │
                                         │                  │
                                         │  /jazz.mp3       │
                                         │  /rock.ogg       │
                                         │  /lofi.mp3       │
                                         └──────────────────┘
```

### 1.2 Component Responsibilities

| Component | Role | Language | Key Constraint |
|-----------|------|----------|----------------|
| **Lua Client** (`radio`) | UI + audio playback loop | Lua 5.2 (CC:Tweaked) | No raw sockets, no FFI, HTTP/WS only |
| **Python Bridge** | Transcode + serve + metadata | Python 3.10+ | Real-time DFPWM conversion, fan-out to N clients |
| **Icecast** | Audio source (internet radio) | C (binary) | Standard Icecast 2.x, MP3/OGG mount points |
| **Speaker Peripheral** | Audio output in-game | CC:Tweaked mod | 48kHz mono DFPWM only, pull-driven |

### 1.3 Deployment Topology

```
Single Host (private Minecraft server):
├── Minecraft Server (Java, default port 25565)
│   └── CC:Tweaked mod installed
│       └── HTTP config allows 127.0.0.1:5309
├── Python Bridge Server (port 5309, user: icecast)
│   └── FFmpeg binary in PATH
└── Icecast Server (port 8000, user: icecast)
    └── Mount points configured per station
```

All three services run on the same machine. CC:Tweaked connects to the bridge at `127.0.0.1:5309` (localhost only — no network exposure). The `icecast` service user owns both the Python bridge and Icecast processes. Port 5309 ("Jenny") is above 1024 so no root needed.

---

## 2. Protocol Specification

### 2.1 Design Decision: HTTP GET (Pull Model)

**Why not WebSocket push?** The speaker hardware is pull-driven — it fires `speaker_audio_empty` when ready for the next chunk. Push creates an impedance mismatch: the server pushes data the client isn't ready for, requiring client-side buffering that duplicates what the speaker already handles. HTTP GET matches the hardware physics.

**Why not chunked HTTP streaming?** CC:Tweaked's HTTP response handling for persistent streams is implementation-dependent. Each discrete GET is self-healing — if the server restarts, the next GET reconnects automatically. No connection state to manage.

### 2.2 Endpoints

**Base URL:** `http://{bridge_host}:5309`

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
      "description": "Chill beats to mine to"
    },
    {
      "id": "jazz",
      "name": "Smooth Jazz FM",
      "genre": "Jazz",
      "frequency": "101.3",
      "description": "Late night jazz vibes"
    }
  ]
}
```

#### `GET /stream/{station_id}`

Returns the next 16KB DFPWM audio chunk. Binary response.

- **Content-Type:** `application/octet-stream`
- **Response body:** Exactly 16,384 bytes of DFPWM data
- **Behavior:** Blocks until a chunk is available (max 3s timeout server-side)
- **Headers returned:**
  - `X-Station-Active: true|false` — is this station currently streaming?
  - `X-Buffer-Level: 0-100` — server buffer fullness (diagnostic)
- **Error responses:**
  - `404` — station ID not found
  - `503` — station is offline (Icecast source disconnected)
  - `204` — no data available yet (buffer empty, try again)

#### `GET /now-playing/{station_id}`

Returns current track metadata.

```json
{
  "station_id": "lofi",
  "station_name": "Lo-Fi Beats",
  "title": "Midnight Stroll",
  "artist": "ChillHop Records",
  "listeners": 3,
  "uptime_seconds": 7240
}
```

#### `GET /health`

Server health check for debugging.

```json
{
  "status": "ok",
  "stations_active": 3,
  "total_listeners": 5,
  "uptime_seconds": 86400,
  "ffmpeg_version": "6.1.1"
}
```

### 2.3 Protocol Versioning

All responses include `X-Radio-Protocol: 1` header. Future breaking changes increment this. Lua client checks on startup and displays "Radio needs update!" if version mismatch.

---

## 3. Audio Pipeline

### 3.1 Full Chain

```
Icecast mount ──► HTTP GET stream ──► FFmpeg subprocess ──► DFPWM bytes ──► Ring Buffer ──► HTTP response ──► Lua decode ──► Speaker
  (MP3/OGG)        (continuous)        (real-time)          (16KB chunks)    (per station)    (on demand)       (cc.audio)     (48kHz mono)
```

### 3.2 DFPWM Encoding Details

| Parameter | Value | Notes |
|-----------|-------|-------|
| Sample rate | 48,000 Hz | Fixed by CC:Tweaked speaker |
| Channels | 1 (mono) | Speaker is mono; stereo → mono mixdown |
| Bit depth | 1-bit | DFPWM codec characteristic |
| Chunk size | 16,384 bytes | 16KB = 131,072 samples = 2.73 seconds |
| Data rate | ~6.0 KB/s | Trivial bandwidth requirement |
| Codec | DFPWM (Delta-modulated Frequency Pulse Width Modulation) | Supported natively by FFmpeg 5.1+ |

### 3.3 FFmpeg Transcoding Command

```bash
ffmpeg -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 \
  -i "http://icecast:8000/lofi.mp3" \
  -f dfpwm -ar 48000 -ac 1 \
  pipe:1
```

Key flags:
- `-reconnect 1` — auto-reconnect on Icecast disconnect
- `-f dfpwm` — output DFPWM format (FFmpeg 5.1+)
- `-ar 48000 -ac 1` — force 48kHz mono
- `pipe:1` — output to stdout for Python to read

### 3.4 Ring Buffer Design (Server-Side)

Each station maintains a ring buffer:

```
┌───────────────────────────────────────────────┐
│ Ring Buffer (per station)                     │
│                                               │
│  Capacity: 8 chunks (8 × 16KB = 128KB)       │
│  ≈ 21.8 seconds of audio buffer              │
│                                               │
│  [C0][C1][C2][C3][C4][C5][C6][C7]           │
│        ▲              ▲                       │
│        │              │                       │
│      read_ptr       write_ptr                 │
│   (next to serve)  (next to fill)            │
│                                               │
│  Write: FFmpeg stdout → fill at write_ptr    │
│  Read: HTTP GET → serve from read_ptr        │
│  Fan-out: multiple readers share one buffer  │
└───────────────────────────────────────────────┘
```

**Fan-out model:** One FFmpeg process per station produces DFPWM. Multiple Lua clients reading the same station share the ring buffer — each client tracks its own read position. No duplicate transcoding.

**Resource per station:**
- 1 FFmpeg process (~15-30MB RAM, ~5% CPU for MP3→DFPWM)
- 128KB ring buffer
- Estimated max: 10 stations = ~300MB RAM, ~50% single core

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
| Icecast → Python (HTTP) | <10ms | LAN, continuous stream |
| FFmpeg transcode | <50ms per chunk | Real-time, pipelined |
| Ring buffer wait | 0-100ms | Usually immediate |
| Python → Lua (HTTP GET) | <10ms | LAN, 16KB payload |
| DFPWM decode (Lua) | <5ms | Built-in cc.audio.dfpwm |
| **Total cold-start** | **<3 seconds** | First chunk available + decode + play |
| **Steady-state** | **<200ms** | Chunk already in buffer, instant |

### 3.7 Cold Start (Player Connects Mid-Stream)

When a new client requests `/stream/{id}`, the server returns the NEXT chunk that completes writing to the ring buffer — not a chunk from the past. This means:
- Audio starts at the live edge (same as other listeners)
- No "catch-up" period
- Maximum wait: one chunk duration (2.73s) if buffer was just read

### 3.8 Station Switching

```
1. Player selects new station in UI
2. Lua: set playing = false (stops fetch loop)
3. Lua: flush buffer (discard any pre-fetched old-station data)
4. Lua: reset DFPWM decoder state (prevent audio garbage)
5. Lua: set station_id = new station
6. Lua: set playing = true (restarts fetch loop against new station)
7. First chunk arrives in <200ms (server buffer has data ready)
8. Audio plays: total switch time <500ms
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
| 503 (station offline) | "Station offline" | Poll every 10s until 200 |
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
# 1. StationManager — loads config, starts/stops station workers
# 2. StationWorker — per-station: FFmpeg process + ring buffer
# 3. HTTPServer — serves /stations, /stream/{id}, /now-playing/{id}
# 4. MetadataPoller — polls Icecast admin API for now-playing info

class StationWorker:
    """One per configured station. Manages FFmpeg + buffer."""
    ffmpeg_process: subprocess.Popen
    ring_buffer: RingBuffer  # 8 chunks × 16KB
    metadata: dict           # current now-playing
    listener_count: int      # active readers

class RadioServer:
    """HTTP server (aiohttp or Flask)."""
    stations: dict[str, StationWorker]
    config: StationConfig    # loaded from YAML
```

### 5.2 Transcoding Library Choice

**Decision: FFmpeg subprocess** (not pure-Python)

| Option | Pros | Cons | Verdict |
|--------|------|------|---------|
| FFmpeg subprocess | Battle-tested, DFPWM support (5.1+), auto-reconnect, handles all input formats | External dependency, process management | ✅ **Winner** |
| Pure Python encoder | No dependency, portable | DFPWM encoder doesn't exist in Python, would need to write one | ❌ Too much work |
| Pre-transcoded files | Zero CPU at runtime | Not live radio — defeats the purpose | ❌ Wrong model |

FFmpeg is the only option that handles real-time DFPWM encoding from arbitrary audio sources. The subprocess model is also self-healing: if FFmpeg crashes, Python restarts it.

### 5.3 Concurrency Model

```python
# asyncio-based (aiohttp server)
# Each station worker runs in its own asyncio task:
#   - Reads from FFmpeg stdout (async pipe)
#   - Writes 16KB chunks to ring buffer
#
# HTTP handlers are async:
#   - /stream/{id}: await ring_buffer.read_next(client_position)
#   - Non-blocking for the server even if one client is slow
```

### 5.4 Station Configuration (`stations.yaml`)

```yaml
server:
  host: "0.0.0.0"
  port: 5309

stations:
  - id: lofi
    name: "Lo-Fi Beats"
    genre: "Electronic"
    frequency: "98.7"
    source_url: "http://icecast:8000/lofi.mp3"
    description: "Chill beats to mine to"

  - id: jazz
    name: "Smooth Jazz FM"
    genre: "Jazz"
    frequency: "101.3"
    source_url: "http://icecast:8000/jazz.mp3"
    description: "Late night jazz vibes"

  - id: rock
    name: "Classic Rock Radio"
    genre: "Rock"
    frequency: "104.9"
    source_url: "http://icecast:8000/rock.ogg"
    description: "The classics never die"
```

**Hot-reload:** Server watches `stations.yaml` with `watchdog` library. New stations start immediately. Removed stations drain current listeners then stop. Changed URLs reconnect the FFmpeg process.

### 5.5 Metadata Polling

The Python server polls Icecast's admin API every 5 seconds per station:

```
GET http://icecast:8000/admin/stats?mount=/lofi.mp3
Authorization: Basic {admin_credentials}
```

Parses the XML response for `<title>`, `<artist>`, `<listeners>`. Stores in `StationWorker.metadata`. Lua clients fetch via `/now-playing/{id}`.

### 5.6 Resource Estimates

| Stations | FFmpeg RAM | Buffer RAM | CPU (idle) | CPU (all streaming) |
|----------|-----------|-----------|-----------|-------------------|
| 3 | ~90 MB | 384 KB | ~2% | ~15% |
| 5 | ~150 MB | 640 KB | ~3% | ~25% |
| 10 | ~300 MB | 1.3 MB | ~5% | ~50% |

Acceptable for a home server. 5 stations is the sweet spot — enough variety without resource concern.

---

## 6. Metadata System

### 6.1 Data Flow

```
Icecast (ICY metadata) ──► Python MetadataPoller (5s interval) ──► StationWorker.metadata
                                                                          │
Lua metadataLoop (5s poll) ◄── GET /now-playing/{id} ◄────────────────────┘
         │
         ▼
    UI render (title, artist)
```

### 6.2 Why Separate from Audio

Metadata rides a separate endpoint (not in audio stream headers) because:
1. **Timing independence:** Metadata updates mid-chunk. Audio chunks are 2.73s — title change shouldn't wait for next chunk boundary.
2. **UI independence:** Metadata poll runs in its own coroutine. If audio stalls, UI still updates.
3. **Simplicity:** Binary audio endpoint stays simple. No parsing frame headers in Lua.

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
| FFmpeg crash (one station) | All listeners on that station | Auto-restart FFmpeg within 3s. Listeners see "Tuning..." |
| Icecast disconnect | All listeners on affected mount | FFmpeg `-reconnect` handles it. Buffer sustains ~22s gap |
| Python server crash | All listeners (all stations) | systemd/supervisor auto-restart. Lua clients auto-retry |
| One Lua client crash | One player | Other players unaffected. Player restarts `radio` program |
| Speaker destroyed | One player's audio | Lua detects, shows "No speaker!" — other code unaffected |
| Network partition | All listeners | Lua clients show "Connecting..." with auto-retry |

### 7.2 Graceful Degradation

```
Level 0 (healthy):    Audio + metadata flowing normally
Level 1 (buffering):  Server buffer depleting, audio still playing from pre-fetch
Level 2 (stalling):   Buffer empty, "Tuning..." displayed, silence
Level 3 (offline):    Station unreachable, "Station offline", return to list
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

**Goal:** One station playing pre-transcoded DFPWM files, charming UI, kid-proof resilience.

**Architecture:** Static DFPWM files served via HTTP (no live transcoding yet). The Python server is essentially a smart file server that reads pre-converted DFPWM data and serves 16KB chunks on demand. Nothing can crash at runtime except the server process itself.

**Entry criteria:** FFmpeg 5.1+ installed (for offline conversion only), CC:Tweaked on Minecraft server.

**Pre-work (one-time):**
```bash
# Convert MP3 files to DFPWM (offline, before server runs)
ffmpeg -i song1.mp3 -f dfpwm -ar 48000 -ac 1 stations/lofi/song1.dfpwm
ffmpeg -i song2.mp3 -f dfpwm -ar 48000 -ac 1 stations/lofi/song2.dfpwm
```

**Deliverables:**
- Python server: serves 16KB chunks from DFPWM files on disk
- `/stream/{id}` endpoint returning binary DFPWM chunks (reads sequentially through file, wraps around)
- `/stations` endpoint (single station, hardcoded or from YAML)
- Lua client: fetch loop + speaker playback + double-buffer + pre-fetch chunk 0
- Terminal UI: station name, "Playing..." status, volume control, colored retro look
- Auto-retry on HTTP timeout (resilient from day 1)
- Speaker detection with helpful "Place a speaker next to this computer!" error
- `pcall()` wrapping on speaker calls (handles peripheral destruction gracefully)

**Exit criteria:** Player runs `radio`, hears music within 3 seconds, sees charming UI, can adjust volume. Audio loops indefinitely without drops. A kid can use it with zero instruction.

**Estimated effort:** 8-10 hours

**Dependencies:**
```
pip install aiohttp pyyaml
# FFmpeg 5.1+ for offline DFPWM conversion (not needed at runtime)
# No Icecast needed for Phase 1!
```

**Why static files first (Council decision):** Zero runtime deps means almost nothing can fail. The server is trivial (read bytes from file, serve them). All the complexity that CAN fail (FFmpeg subprocess, Icecast connection, live transcoding) is deferred to Phase 2. This guarantees a working, fun radio on Weekend 1.

---

### Phase 2: Live Radio — "Real stations" (Weekend 2)

**Goal:** Live Icecast streaming with real-time transcoding, multiple stations, metadata.

**Architecture upgrade:** Replace static file serving with live FFmpeg transcoding from Icecast streams. The protocol stays identical (same HTTP GET, same 16KB chunks) — only the server backend changes. Lua client needs zero modifications for basic streaming.

**Entry criteria:** Phase 1 working and tested. Icecast running with 2+ mount points.

**Deliverables:**
- FFmpeg subprocess per station (live transcoding Icecast → DFPWM)
- Ring buffer per station (8 chunks, fan-out to multiple clients)
- `stations.yaml` config with 3+ stations pointing to Icecast mounts
- `/now-playing/{id}` endpoint with Icecast admin metadata polling (3s interval)
- Station worker lifecycle (start/stop/restart per station)
- FFmpeg auto-restart on crash (max 3/minute, then mark offline)
- Lua: station list UI (arrow keys to browse, Enter to select)
- Lua: now-playing display (title + artist from metadata endpoint)
- Lua: station switching with proper buffer flush + decoder reset (§3.8)
- Lua: CC computer unload detection via `os.clock()` gap > 5s → full reconnect
- Hot-reload for station config (add/remove station without server restart)
- "Frequency" cosmetic display (98.7 FM, etc.)

**Exit criteria:** Player browses 3+ live Icecast stations, switches between them in <1s with no wrong-station audio bleed, sees now-playing metadata updating. New station addable via YAML without server restart.

**Estimated effort:** 10-12 hours

---

### Phase 3: Polish & Resilience — "It just works" (Weekend 3)

**Goal:** Production-quality error handling, monitoring, multi-listener fan-out tested.

**Entry criteria:** Phase 2 working with 3+ stations.

**Deliverables:**
- `/health` endpoint with diagnostics
- Proper logging (Python `logging` module, file rotation)
- Fan-out stress test: 5 CC computers on same station simultaneously
- Graceful degradation UI states (Tuning.../Offline/Connecting...)
- Exponential backoff on all retry paths
- FFmpeg auto-restart on crash
- systemd service file for Python server
- CC:Tweaked HTTP config documentation
- In-game signage instructions

**Exit criteria:** Server runs 24+ hours without intervention. Recovers from Icecast restart within 30s. 5 concurrent listeners no audio drops.

**Estimated effort:** 6-8 hours

---

### Phase 4: Experience — "Radio charm" (Weekend 4)

**Goal:** The UI looks and feels like a real retro radio.

**Entry criteria:** Phase 3 stable.

**Deliverables:**
- Full-color terminal UI with the wireframe design from §4.2
- Animated "tuning" effect on station switch (visual flair)
- ASCII art station logos (per-station, configured in YAML)
- Audio visualizer (simple bar animation based on chunk amplitude)
- Startup animation ("Warming up tubes...")
- Favorite stations (saved to CC computer's local storage)
- Auto-resume last station on computer reboot

**Exit criteria:** A kid says "cool!" — the UI delights on first encounter.

**Estimated effort:** 8-10 hours

---

### Phase 5: Social & Advanced — "Our radio station" (Weekend 5+)

**Goal:** Multiplayer features, DJ mode, extensibility.

**Entry criteria:** Phase 4 complete.

**Deliverables:**
- Listener count displayed per station
- "Who's listening" list (player names via rednet or server-side mapping)
- Song request system (queue endpoint + admin approval)
- DJ mode: source client that lets a player broadcast to a mount point
- Redstone integration: speaker volume controlled by redstone signal
- Multi-speaker spatial setup (place 2 speakers for left/right channels)
- Public playlist display on a monitor peripheral
- Integration with game console home screen

**Exit criteria:** Friends interact with the radio socially. Requests work. Multiple speakers create spatial audio.

**Estimated effort:** 12-16 hours (can be split across multiple weekends)

---

## 9. Adversarial Analysis (RedTeam Findings)

### 9.1 Ranked Risk Register

| # | Risk | Severity | Likelihood | Player Experience | Mitigation |
|---|------|----------|------------|-------------------|------------|
| 1 | **CC computer unloaded mid-stream** — player walks away, computer unloads, stale buffer + corrupted DFPWM state on return | Critical | Almost Certain | Garbage audio burst, then silence | Detect unload via `os.clock()` delta > 5s between speaker events. Flush buffers, reset decoder, request fresh chunk with `?reset=1` param |
| 2 | **CPU exhaustion (many stations)** — 10 FFmpeg instances saturate a 4-8 core host | Critical | Likely | All stations stutter; Minecraft server lags if co-hosted | Fan-out architecture: one FFmpeg per station, N clients read from shared ring buffer. Cap at 6 active stations |
| 3 | **Icecast source disconnects** — FFmpeg input EOF → process exit → silence | High | Likely | Abrupt silence, no indication | Detect FFmpeg exit, serve silent DFPWM "dead air" chunk, set metadata `status: offline`. Auto-reconnect with backoff (2s/4s/8s/30s cap) |
| 4 | **Station switch plays old audio** — pre-fetched buffer contains old station data | High | Almost Certain | 2.73s of wrong station before correct audio | On switch: `speaker.stop()`, flush buffers, set `switching` flag, block playback until first new-station chunk arrives. Accept silence over wrong audio. |
| 5 | **Transcoding slower than realtime** — 320kbps stereo source causes FFmpeg lag | High | Possible | Periodic 2.73s dropouts (skipping sound) | Server returns 204 when chunk not ready. Client plays silence, retries next tick. Health endpoint exposes transcode lag metric |
| 6 | **HTTP timeout drains double-buffer** — 5s default timeout blocks coroutine | Medium | Likely | Single dropout, then recovery | Set explicit 2s timeout on `http.get()`. If timeout, play silence for that chunk, retry immediately |
| 7 | **YAML syntax error kills server** — typo prevents all stations loading | Medium | Possible | All radios dead after server restart | Parse-then-swap: validate new config before replacing. Keep last-good in memory. Never replace working config with broken |
| 8 | **DFPWM decoder state on reconnect** — stateful codec starts with wrong state | Medium | Likely | Brief distortion (~0.5s) at reconnection | Server prepends 512 samples of silence as "reset preamble" on `?reset=1` chunks. Fresh decoder on every reconnect |
| 9 | **Metadata staleness (15-30s behind)** — poll interval + Icecast lag | Low | Almost Certain | Wrong song title displayed | Poll every 3s, cache with 1s TTL. Display `~` prefix when staleness > 5s |
| 10 | **Speaker peripheral destroyed mid-playback** — error spam in terminal | Low | Possible | Lua errors flood screen | Wrap `playAudio()` in `pcall()`. On peripheral loss, enter idle state, poll for speaker every 2s, auto-resume |

### 9.2 Top 5 Design Requirements (from RedTeam)

1. **Shared transcode architecture** — One FFmpeg per station, not per client. Clients read from shared ring buffer at independent offsets. Without this, system cannot scale past 3-4 listeners on different stations.
2. **Atomic station switch with buffer flush** — Stop speaker, discard buffers, block playback, reset decoder, request first new-station chunk. Wrong-station bleed is the most noticeable UX bug.
3. **Graceful degradation → silence, never garbage** — Every failure mode produces silence, not distortion/error spam/freeze. Three client states only: `PLAYING`, `BUFFERING` (auto-retry), `OFFLINE` (display message, slow retry).
4. **Chunk-load awareness** — Timestamp every chunk request. If gap between speaker events exceeds 5s (computer was unloaded), treat as full reconnection.
5. **Config validation with safe fallback** — Parse-then-swap. Last-known-good survives broken edits. Station changes apply to new connections only, never interrupt active streams.

---

## 10. Council Synthesis — Key Architecture Decisions

*5-member council debate: Systems Engineer, Game Developer, Embedded/IoT Developer, Parent/UX Designer, Pragmatic Weekend Hacker.*

### 10.1 Consensus Decisions

| Decision | Choice | Confidence | Key Argument |
|----------|--------|------------|--------------|
| Transport protocol | **HTTP GET (pull)** | High | Matches speaker hardware physics; stateless = self-healing |
| Phase 1 transcoding | **Pre-transcoded DFPWM files** (FFmpeg offline) | High | Zero runtime deps, nothing can break at playback time. Weekend Hacker: "ships Saturday" |
| Phase 2+ transcoding | **FFmpeg subprocess** (live from Icecast) | High | Only option for live radio. Added after static-file MVP is proven |
| Server-side buffering | **Ring buffer (8 chunks)** | High | ~22s absorbs any Icecast blip; fan-out friendly |
| Client-side buffering | **Double-buffer + pre-fetch chunk 0** at startup | High | Pre-fetch eliminates first-play silence gap |
| Metadata (Phase 1) | **Response headers on audio GET** | Medium | Free metadata, one request. Game Dev: "no race condition" |
| Metadata (Phase 2+) | **Separate endpoint** added for display-only clients | High | Enables monitor displays, decoupled from audio timing |
| Station config | **YAML hot-reload** | High | Human-readable, teachable (human-readable, easy to edit) |

### 10.2 Council Insight: Two-Track MVP

The Council's most impactful finding: **Phase 1 should use pre-transcoded static DFPWM files, not live Icecast transcoding.**

Rationale:
- Eliminates ALL runtime dependencies (no FFmpeg process, no Icecast running, no network between server and stream source)
- Server becomes a simple HTTP file server — almost nothing can fail
- FFmpeg is used offline at "ingest time" to convert MP3/OGG → DFPWM files
- Same HTTP GET interface works for both static files and live streams — the Lua client doesn't care
- Live streaming is Phase 2: same client, upgraded server

This means Phase 1 is functionally a "jukebox" (pre-loaded tracks) and Phase 2 makes it a "live radio" (Icecast streams). Both use the same protocol.

### 10.3 Unresolved Tensions

- **Cold-start latency:** 16KB chunk = 2.73s. Pre-fetching chunk 0 immediately helps but the first audible sound still waits one full chunk decode. Could serve a smaller "primer" chunk (4KB = 0.68s) as the first response only.
- **Metadata freshness vs simplicity:** Headers on audio response mean metadata is always synchronized with audio, but display-only clients need a separate endpoint. Resolution: do both (trivial to implement in parallel).
- **Pre-transcoded vs live:** Static files are simpler but aren't real radio. The group agrees this is a phasing concern, not a fundamental tension — Phase 1 jukebox → Phase 2 live radio is the correct progression.

---

## 11. Dependencies & Setup

### 11.1 Python Server

```
Python 3.10+
aiohttp >= 3.9
pyyaml >= 6.0
watchdog >= 3.0 (for config hot-reload)
FFmpeg >= 5.1 (DFPWM codec support)
```

### 11.2 Minecraft Server

```
Minecraft 1.19+ (or whatever CC:Tweaked supports)
CC:Tweaked mod (latest)
Server config: computercraft-server.toml
  [[http.rules]]
    host = "127.0.0.1"
    port = 5309
    action = "allow"
```

### 11.3 Icecast

```
Icecast 2.4+
At least one mount point with an active source
Admin credentials for metadata polling (default: admin/hackme — CHANGE THIS)
```

---

## 12. Server Setup & Deployment

### 12.1 Overview

All services run on the same host as the Minecraft server under a dedicated `icecast` system user. The install script handles user creation, directory structure, Python venv, Icecast config, systemd units, and CC:Tweaked HTTP allowlist.

### 12.2 Service User

```bash
# Dedicated user — no login shell, no home directory clutter
sudo useradd --system --shell /usr/sbin/nologin --home-dir /opt/mcradio icecast
sudo mkdir -p /opt/mcradio/{server,stations,logs}
sudo chown -R icecast:icecast /opt/mcradio
```

**Why a dedicated user:**
- Isolates the radio service from Minecraft/root
- systemd runs the service as this user (no root)
- If compromised, blast radius is limited to `/opt/mcradio`
- Clean `ps aux | grep icecast` identification

### 12.3 Directory Structure

```
/opt/mcradio/
├── server/
│   ├── radio_bridge.py       # Main Python server
│   ├── stations.yaml         # Station config
│   ├── requirements.txt      # Python deps
│   └── venv/                 # Python virtual environment
├── stations/
│   └── lofi/                 # Pre-transcoded DFPWM files (Phase 1)
│       ├── track01.dfpwm
│       └── track02.dfpwm
├── logs/
│   └── radio-bridge.log      # Rotated via logrotate
└── icecast/
    └── icecast.xml           # Icecast config (Phase 2+)
```

### 12.4 Install Script (`setup.sh`)

```bash
#!/bin/bash
set -euo pipefail

# Minecraft Radio Bridge — Setup Script
# Run as root (or with sudo)
# Usage: sudo bash setup.sh

RADIO_USER="icecast"
RADIO_HOME="/opt/mcradio"
RADIO_PORT=5309
ICECAST_PORT=8000
MC_SERVER_DIR=""  # Set this to your Minecraft server path

echo "=== Minecraft Radio Bridge Setup ==="
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
mkdir -p "$RADIO_HOME"/{server,stations,logs,icecast}
chown -R "$RADIO_USER:$RADIO_USER" "$RADIO_HOME"
echo "[OK] Directory structure at $RADIO_HOME"

# --- 3. Install system dependencies ---
echo "[..] Installing system packages..."
if command -v apt-get &>/dev/null; then
    apt-get update -qq
    apt-get install -y -qq python3 python3-venv ffmpeg icecast2
elif command -v dnf &>/dev/null; then
    dnf install -y python3 python3-pip ffmpeg icecast
elif command -v pacman &>/dev/null; then
    pacman -S --noconfirm python ffmpeg icecast
else
    echo "[!!] Unknown package manager. Install manually: python3, ffmpeg, icecast2"
    exit 1
fi
echo "[OK] System packages installed"

# --- 4. Verify FFmpeg DFPWM support ---
if ffmpeg -formats 2>/dev/null | grep -q dfpwm; then
    echo "[OK] FFmpeg has DFPWM codec support"
else
    echo "[!!] FFmpeg does NOT support DFPWM. Need FFmpeg 5.1+"
    echo "     Current: $(ffmpeg -version | head -1)"
    exit 1
fi

# --- 5. Python venv + deps ---
sudo -u "$RADIO_USER" python3 -m venv "$RADIO_HOME/server/venv"
sudo -u "$RADIO_USER" "$RADIO_HOME/server/venv/bin/pip" install -q aiohttp pyyaml watchdog
echo "[OK] Python venv created with dependencies"

# --- 6. Install systemd units ---
cat > /etc/systemd/system/mcradio.service << 'EOF'
[Unit]
Description=Minecraft Radio Bridge (DFPWM transcoder + HTTP server)
After=network.target icecast2.service
Wants=icecast2.service

[Service]
Type=simple
User=icecast
Group=icecast
WorkingDirectory=/opt/mcradio/server
ExecStart=/opt/mcradio/server/venv/bin/python radio_bridge.py
Restart=always
RestartSec=3
StandardOutput=append:/opt/mcradio/logs/radio-bridge.log
StandardError=append:/opt/mcradio/logs/radio-bridge.log

# Hardening
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=/opt/mcradio
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
EOF

echo "[OK] Systemd unit installed: mcradio.service"

# --- 7. Logrotate ---
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

# --- 8. CC:Tweaked HTTP config ---
if [ -n "$MC_SERVER_DIR" ] && [ -d "$MC_SERVER_DIR" ]; then
    CC_CONFIG="$MC_SERVER_DIR/config/computercraft-server.toml"
    if [ -f "$CC_CONFIG" ]; then
        if grep -q "127.0.0.1" "$CC_CONFIG" 2>/dev/null; then
            echo "[OK] CC:Tweaked config already allows localhost"
        else
            echo ""
            echo "[!!] Add this to $CC_CONFIG under [[http.rules]]:"
            echo '    [[http.rules]]'
            echo '    host = "127.0.0.1"'
            echo '    port = 5309'
            echo '    action = "allow"'
            echo ""
        fi
    else
        echo "[!!] CC:Tweaked config not found at $CC_CONFIG"
        echo "     Start the server once to generate it, then re-run setup"
    fi
else
    echo "[NOTE] Set MC_SERVER_DIR in this script to auto-configure CC:Tweaked"
    echo "       Manual config needed in computercraft-server.toml:"
    echo '       [[http.rules]]'
    echo '       host = "127.0.0.1"'
    echo '       port = 5309'
    echo '       action = "allow"'
fi

# --- 9. Enable and start ---
systemctl daemon-reload
systemctl enable mcradio.service
echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. Place radio_bridge.py in $RADIO_HOME/server/"
echo "  2. Edit $RADIO_HOME/server/stations.yaml"
echo "  3. For Phase 1: put .dfpwm files in $RADIO_HOME/stations/<station_id>/"
echo "  4. For Phase 2+: configure Icecast mount points"
echo "  5. Start: sudo systemctl start mcradio"
echo "  6. Check: curl http://127.0.0.1:$RADIO_PORT/health"
echo "  7. In Minecraft: place Computer + Speaker, run 'radio'"
echo ""
echo "Management:"
echo "  Status:  sudo systemctl status mcradio"
echo "  Logs:    tail -f $RADIO_HOME/logs/radio-bridge.log"
echo "  Restart: sudo systemctl restart mcradio"
```

### 12.5 systemd Service Unit (Detail)

```ini
[Unit]
Description=Minecraft Radio Bridge (DFPWM transcoder + HTTP server)
After=network.target icecast2.service
Wants=icecast2.service

[Service]
Type=simple
User=icecast
Group=icecast
WorkingDirectory=/opt/mcradio/server
ExecStart=/opt/mcradio/server/venv/bin/python radio_bridge.py
Restart=always
RestartSec=3
StandardOutput=append:/opt/mcradio/logs/radio-bridge.log
StandardError=append:/opt/mcradio/logs/radio-bridge.log

# Hardening — minimize blast radius
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=/opt/mcradio
PrivateTmp=yes
ProtectHome=yes
ProtectKernelTunables=yes
ProtectControlGroups=yes

# Resource limits (prevent runaway FFmpeg)
MemoryMax=512M
CPUQuota=80%
TasksMax=32

[Install]
WantedBy=multi-user.target
```

**Key design decisions:**
- `Restart=always` + `RestartSec=3` — auto-recovery, matches RedTeam requirement #2
- `MemoryMax=512M` — caps total memory (10 stations × ~30MB FFmpeg + Python = ~400MB max)
- `CPUQuota=80%` — prevents radio from starving the Minecraft server of CPU
- `Wants=icecast2.service` — starts Icecast if available, but doesn't fail without it (Phase 1 doesn't need Icecast)
- Hardening directives prevent the service from reading `/home`, writing outside `/opt/mcradio`, or escalating privileges

### 12.6 CC:Tweaked HTTP Configuration

In `<minecraft-server>/config/computercraft-server.toml`:

```toml
[[http.rules]]
host = "127.0.0.1"
port = 5309
action = "allow"
```

This allows CC:Tweaked computers to make HTTP requests to the radio bridge on localhost only. No external network access granted. The rule is port-specific — computers cannot reach other localhost services.

**If using CC:Tweaked's default deny-all for local addresses**, you may also need:

```toml
[[http.rules]]
host = "127.0.0.1"
action = "allow"
```

Check the existing rules in the file — CC:Tweaked ships with `[[http.rules]]` entries that deny `127.0.0.0/8` and `10.0.0.0/8` by default. Your allow rule must appear BEFORE the deny rule (first match wins).

### 12.7 Icecast Configuration (Phase 2+)

Minimal `/opt/mcradio/icecast/icecast.xml` for the radio:

```xml
<icecast>
    <location>Minecraft Radio</location>
    <hostname>localhost</hostname>

    <limits>
        <clients>32</clients>
        <sources>10</sources>
    </limits>

    <authentication>
        <source-password>changeme_source</source-password>
        <admin-user>admin</admin-user>
        <admin-password>changeme_admin</admin-password>
    </authentication>

    <listen-socket>
        <port>8000</port>
        <bind-address>127.0.0.1</bind-address>
    </listen-socket>

    <paths>
        <logdir>/opt/mcradio/logs</logdir>
        <webroot>/usr/share/icecast2/web</webroot>
    </paths>

    <logging>
        <accesslog>icecast-access.log</accesslog>
        <errorlog>icecast-error.log</errorlog>
        <loglevel>3</loglevel>
    </logging>
</icecast>
```

**Security notes:**
- `bind-address: 127.0.0.1` — Icecast only listens on localhost (no external access)
- Change default passwords before Phase 2 deployment
- The Python bridge connects to Icecast at `127.0.0.1:8000` — never exposed to network

### 12.8 Firewall Notes

No firewall changes needed. All traffic is localhost:
- CC:Tweaked → Python bridge: `127.0.0.1:5309`
- Python bridge → Icecast: `127.0.0.1:8000`
- Players connect to Minecraft on its own port (25565) as usual

The radio adds ZERO network surface area to the host.

---

## 13. Testing Strategy (Per Phase)

| Phase | Test | Method |
|-------|------|--------|
| 1 | Audio plays | Manual: run `radio`, hear music |
| 1 | Volume works | Adjust volume, confirm audible change |
| 1 | Timeout recovery | Kill Python server, restart, confirm auto-reconnect |
| 2 | Station switching | Switch 5 times rapidly, no audio garbage |
| 2 | Metadata accuracy | Check now-playing matches Icecast admin page |
| 2 | Hot reload | Add station to YAML, confirm appears in list without restart |
| 3 | Concurrent listeners | 5 CC computers same station, no drops |
| 3 | 24-hour soak | Leave running overnight, check in morning |
| 3 | FFmpeg crash recovery | `kill` FFmpeg PID, confirm auto-restart <5s |
| 4 | UX test (kid) | Hand to a kid, observe if they can use it unaided |
| 5 | Request system | Submit request, confirm it plays |

---

## 14. Future Extensibility Hooks (Not Implemented)

Documented here so future phases know where to attach:

- **DJ mode:** Add `/source/{station_id}` POST endpoint accepting DFPWM upload. Lua client with microphone peripheral (if CC:Tweaked ever adds one) or pre-recorded files.
- **Song requests:** Add `/request/{station_id}` POST with `{title}` body. Queue stored in memory, admin `/approve` endpoint.
- **Redstone volume:** Lua reads redstone signal level (0-15), maps to volume (0.0-3.0).
- **Multi-speaker:** Lua scans for multiple speakers, sends same audio to all (pseudo-spatial by player positioning).
- **Monitor display:** Separate program `radio-display` that shows now-playing on an adjacent CC monitor for passersby.
- **Home screen integration:** The game console's main menu launches `radio` as a sub-program.

---

## Appendix A: Sequence Diagrams

### A.1 Normal Playback Flow

```
Lua Client          Python Server          FFmpeg            Icecast
    │                    │                    │                  │
    │ GET /stations      │                    │                  │
    │───────────────────►│                    │                  │
    │◄───────────────────│                    │                  │
    │  [station list]    │                    │                  │
    │                    │                    │                  │
    │ GET /stream/lofi   │                    │                  │
    │───────────────────►│                    │                  │
    │                    │  read(16384)       │                  │
    │                    │───────────────────►│                  │
    │                    │                    │  GET /lofi.mp3   │
    │                    │                    │─────────────────►│
    │                    │                    │◄─────────────────│
    │                    │◄───────────────────│  [MP3 → DFPWM]  │
    │◄───────────────────│  [16KB DFPWM]     │                  │
    │                    │                    │                  │
    │ speaker.playAudio()│                    │                  │
    │ ~~~~2.73s~~~~      │                    │                  │
    │                    │                    │                  │
    │ [speaker_audio_empty event]             │                  │
    │                    │                    │                  │
    │ GET /stream/lofi   │                    │                  │
    │───────────────────►│ [from ring buffer] │                  │
    │◄───────────────────│                    │                  │
    │ [16KB DFPWM]       │                    │                  │
    │ ...repeats...      │                    │                  │
```

### A.2 Station Switch Flow

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
    │◄───────────────────│ [16KB from jazz buffer]
    │                    │
    │ speaker.playAudio()│ ← new station audio in <500ms
```

### A.3 Error Recovery Flow

```
Lua Client          Python Server          FFmpeg
    │                    │                    │
    │ GET /stream/lofi   │                    │
    │───────────────────►│                    │
    │                    │  [FFmpeg crashed!]  │ ✗
    │                    │                    │
    │                    │  restart FFmpeg     │
    │                    │───────────────────►│ (new process)
    │                    │                    │
    │◄───────────────────│  503 (buffering)   │
    │                    │                    │
    │ UI: "Tuning..."    │                    │
    │ sleep(1)           │                    │
    │                    │                    │
    │ GET /stream/lofi   │                    │
    │───────────────────►│                    │
    │                    │◄───────────────────│ [buffer filled]
    │◄───────────────────│  [16KB DFPWM]     │
    │                    │                    │
    │ speaker.playAudio()│  ← audio resumes   │
```
