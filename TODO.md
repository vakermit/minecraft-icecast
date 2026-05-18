# Icecast Radio — Next Steps

## Setup (requires server access)

- [ ] Create `icecast` system user on Minecraft server host
- [ ] Run `setup.sh` or manually create `/opt/mcradio/` directory structure
- [ ] Install Python 3.10+, FFmpeg 5.1+, yt-dlp on server
- [ ] Create Python venv and install requirements.txt
- [ ] Copy `server/radio_server.py` and `server/stations.yaml` to `/opt/mcradio/server/`
- [ ] Install systemd unit for `mcradio.service`
- [ ] Configure CC:Tweaked HTTP rules (`127.0.0.1:5309` allow, BEFORE deny rules)

## Content (once server is set up)

- [ ] Download first batch of tracks: `./scripts/download.sh lofi "<playlist_url>"`
- [ ] Transcode to DFPWM: `./scripts/transcode.sh lofi`
- [ ] Verify metadata JSONs were generated in `music/metadata/lofi/`

## Testing

- [ ] Start server: `sudo systemctl start mcradio`
- [ ] Curl test: `curl http://127.0.0.1:5309/health`
- [ ] Curl test: `curl http://127.0.0.1:5309/stations`
- [ ] Curl test: `curl http://127.0.0.1:5309/stream/lofi | wc -c` (should be 16384)
- [ ] Copy `client/radio.lua` to a CC:Tweaked computer in-game
- [ ] Place speaker peripheral adjacent to computer
- [ ] Run `radio` — confirm audio plays within 1 second
- [ ] Test volume controls (left/right arrows)
- [ ] Test quit (Q key)
- [ ] Kill server, confirm client shows "Tuning..." and auto-retries
- [ ] UX test: hand a kid the controls, zero instruction

## After Phase 1 Confirmed Working

- [ ] Commit everything, tag `phase-1-complete`
- [ ] Start Phase 2 design: multi-station + auto-acquisition
