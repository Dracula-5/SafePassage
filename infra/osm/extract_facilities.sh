#!/usr/bin/env bash
# Facilities from the official OSM extract, used when Overpass is down. Output: GeoJSON sequence.
set -euo pipefail
DATA_DIR="${DATA_DIR:-$HOME/reliefroute-data}"
SRC="$DATA_DIR/valhalla/andhra-pradesh.osm.pbf"
OUT="$DATA_DIR/osm/facilities.geojsonseq"
mkdir -p "$DATA_DIR/osm"
osmium tags-filter --overwrite -o "$DATA_DIR/osm/facilities.osm.pbf" "$SRC" \
  nwr/amenity=hospital,clinic,shelter,school,fuel,refugee_site \
  nwr/social_facility=shelter nwr/emergency=assembly_point nwr/building=warehouse
osmium export --overwrite --add-unique-id=type_id -f geojsonseq -o "$OUT" "$DATA_DIR/osm/facilities.osm.pbf"
# Data age comes from the Geofabrik source header (the clipped file drops it).
osmium fileinfo -g header.option.osmosis_replication_timestamp "$DATA_DIR/southern-zone-latest.osm.pbf" > "$DATA_DIR/osm/facilities.timestamp"
[ -s "$DATA_DIR/osm/facilities.timestamp" ] || { echo "no replication timestamp in source header" >&2; exit 1; }
echo "wrote $OUT ($(wc -l < "$OUT") features, data as of $(cat "$DATA_DIR/osm/facilities.timestamp"))"
