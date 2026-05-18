#!/bin/bash
set -euo pipefail

# Transcode audio files to DFPWM for the radio server
# Usage: ./transcode.sh <station_id> [source_dir]
#
# Examples:
#   ./transcode.sh lofi                    # transcodes music/raw/lofi/*.* → music/dfpwm/lofi/*.dfpwm
#   ./transcode.sh lofi ~/Downloads/music  # transcodes from custom source dir

STATION_ID="${1:?Usage: $0 <station_id> [source_dir]}"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RAW_DIR="${2:-$PROJECT_DIR/music/raw/$STATION_ID}"
DFPWM_DIR="$PROJECT_DIR/music/dfpwm/$STATION_ID"
META_DIR="$PROJECT_DIR/music/metadata/$STATION_ID"

mkdir -p "$DFPWM_DIR" "$META_DIR"

if [ ! -d "$RAW_DIR" ]; then
    echo "Source directory not found: $RAW_DIR"
    echo "Put audio files there, or pass a custom source dir as arg 2"
    exit 1
fi

COUNT=0
SKIPPED=0

for f in "$RAW_DIR"/*.{mp3,ogg,opus,flac,wav,m4a,webm} 2>/dev/null; do
    [ -f "$f" ] || continue

    STEM=$(basename "${f%.*}")
    OUT="$DFPWM_DIR/$STEM.dfpwm"

    if [ -f "$OUT" ]; then
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    echo "  Transcoding: $STEM"
    ffmpeg -hide_banner -loglevel error -i "$f" -f dfpwm -ar 48000 -ac 1 "$OUT"

    # Generate basic metadata JSON if not exists
    META_FILE="$META_DIR/$STEM.json"
    if [ ! -f "$META_FILE" ]; then
        TITLE=$(echo "$STEM" | sed 's/[-_]/ /g' | sed 's/\b\(.\)/\u\1/g')
        DURATION=$(ffprobe -v quiet -show_entries format=duration -of csv=p=0 "$f" 2>/dev/null | cut -d. -f1)
        DURATION=${DURATION:-0}
        cat > "$META_FILE" << METAEOF
{
  "title": "$TITLE",
  "artist": "Unknown Artist",
  "duration_seconds": $DURATION,
  "source_file": "$(basename "$f")",
  "acquired_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
METAEOF
    fi

    COUNT=$((COUNT + 1))
done

echo ""
echo "Done: $COUNT transcoded, $SKIPPED skipped (already exist)"
echo "DFPWM files: $DFPWM_DIR/"
echo "Metadata:    $META_DIR/"
