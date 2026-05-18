#!/bin/bash
set -euo pipefail

# Download music for a station using yt-dlp
# Usage: ./download.sh <station_id> <url_or_search>
#
# Examples:
#   ./download.sh lofi "https://youtube.com/playlist?list=PLxxxxx"
#   ./download.sh lofi "ytsearch10:lo-fi hip hop instrumental"
#   ./download.sh jazz "https://youtube.com/watch?v=xxxxx"

STATION_ID="${1:?Usage: $0 <station_id> <url_or_search>}"
SOURCE="${2:?Usage: $0 <station_id> <url_or_search>}"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RAW_DIR="$PROJECT_DIR/music/raw/$STATION_ID"

mkdir -p "$RAW_DIR"

echo "=== Downloading for station: $STATION_ID ==="
echo "Source: $SOURCE"
echo "Output: $RAW_DIR/"
echo ""

yt-dlp \
    --extract-audio \
    --audio-format opus \
    --audio-quality 0 \
    --min-duration 60 \
    --max-duration 600 \
    -o "$RAW_DIR/%(title)s.%(ext)s" \
    --no-overwrites \
    "$SOURCE"

echo ""
echo "Done. Now transcode with:"
echo "  ./transcode.sh $STATION_ID"
