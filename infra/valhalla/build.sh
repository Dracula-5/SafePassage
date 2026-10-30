#!/usr/bin/env bash
# Build the Valhalla routing graph for the demo region (Andhra Pradesh) from the official
# Geofabrik OSM extract. Re-runnable; skips steps whose outputs exist.
#   DATA_DIR      where extracts and tiles live (default ~/reliefroute-data)
#   REGION_BBOX   osmium bbox min_lon,min_lat,max_lon,max_lat for the clip (default: Andhra Pradesh + margin)
set -euo pipefail

DATA_DIR="${DATA_DIR:-$HOME/reliefroute-data}"
REGION_BBOX="${REGION_BBOX:-76.6,12.5,84.9,20.1}"
SOURCE_URL="https://download.geofabrik.de/asia/india/southern-zone-latest.osm.pbf"
mkdir -p "$DATA_DIR/valhalla"
cd "$DATA_DIR"

if [ ! -f southern-zone-latest.osm.pbf ]; then
  curl -fL --retry 3 -o southern-zone-latest.osm.pbf "$SOURCE_URL"
fi
expected="$(curl -fsSL "$SOURCE_URL.md5" | awk '{print $1}')"
actual="$(md5 -q southern-zone-latest.osm.pbf 2>/dev/null || md5sum southern-zone-latest.osm.pbf | awk '{print $1}')"
[ "$expected" = "$actual" ] || { echo "checksum mismatch: $expected != $actual" >&2; exit 1; }

if [ ! -f valhalla/andhra-pradesh.osm.pbf ]; then
  osmium extract --bbox "$REGION_BBOX" --strategy complete_ways -o valhalla/andhra-pradesh.osm.pbf southern-zone-latest.osm.pbf
fi
echo "extract ready: $DATA_DIR/valhalla/andhra-pradesh.osm.pbf"
echo "now: docker compose --profile routing up -d valhalla   (first start builds tiles, ~5 min)"
echo "then: python3 infra/valhalla/raise_limits.py && docker compose restart valhalla"
