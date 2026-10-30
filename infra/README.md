# infra (owner: Instance B)

Local stack: `docker compose up -d` starts PostGIS and Redis; `--profile routing` adds Valhalla.

| Service | Host port | Notes |
|---|---|---|
| PostGIS 16 / 3.4 | 55432 | 5432/5433 are often taken by a host Postgres |
| Redis 7 | 6380 | append-only persistence |
| Valhalla 3.9 | 8002 | tiles in `$DATA_DIR/valhalla` (default `~/reliefroute-data/valhalla`) |

## Valhalla for the demo region (Andhra Pradesh)
1. `brew install osmium-tool` (or apt `osmium-tool`).
2. `infra/valhalla/build.sh`: downloads Geofabrik `asia/india/southern-zone`, verifies the md5, and clips it to Andhra Pradesh (269 MB).
3. `docker compose --profile routing up -d valhalla`: the first start builds 579 tiles (about 5 min, about 1.1 GB RAM).
4. `infra/valhalla/raise_limits.py && docker compose restart valhalla`: required, because the defaults reject real hazard polygons.

## Facilities fallback extract
`infra/osm/extract_facilities.sh` cuts hospitals, clinics, shelters, schools, warehouses and fuel stations out of the same OSM extract. The ingestion worker uses this file when the Overpass API is unavailable (it often returns 502/timeouts).
