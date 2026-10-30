#!/usr/bin/env bash
# Populated places (Census figures via OSM `population` tags) and administrative boundaries
# (districts = admin_level 5, mandals/taluks = 6, villages = 9) from the same OSM extract.
# Used by the ingestion worker for v_places and for the SACHET areaDesc fallback.
set -euo pipefail
DATA_DIR="${DATA_DIR:-$HOME/reliefroute-data}"
SRC="$DATA_DIR/valhalla/andhra-pradesh.osm.pbf"
OUT="$DATA_DIR/osm"
mkdir -p "$OUT"
osmium tags-filter --overwrite -o "$OUT/places.osm.pbf" "$SRC" n/place=city,town,village,hamlet,suburb,neighbourhood
osmium export --overwrite --add-unique-id=type_id -f geojsonseq -o "$OUT/places.geojsonseq" "$OUT/places.osm.pbf"
osmium tags-filter --overwrite -o "$OUT/admin.osm.pbf" "$SRC" r/boundary=administrative
osmium export --overwrite --add-unique-id=type_id --geometry-types=polygon -f geojsonseq -o "$OUT/admin.geojsonseq" "$OUT/admin.osm.pbf"
osmium fileinfo -g header.option.osmosis_replication_timestamp "$DATA_DIR/southern-zone-latest.osm.pbf" > "$OUT/places_admin.timestamp"
echo "places: $(wc -l < "$OUT/places.geojsonseq")  admin areas: $(wc -l < "$OUT/admin.geojsonseq")  data as of $(cat "$OUT/places_admin.timestamp")"
