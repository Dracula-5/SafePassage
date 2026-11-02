#!/bin/sh
# First step on a new VM: writes infra/deploy/.env with strong random secrets, then checks the machine.
#   sh infra/deploy/setup.sh https://relief.example.org /srv/reliefroute-data
# Safe to run again: an existing .env is kept, because new secrets would lock you out of the database.
set -eu
cd "$(dirname "$0")"
url="${1:-}"
data="${2:-/srv/reliefroute-data}"
problems=0

fail() {
    echo "NOT READY: $1"
    problems=$((problems + 1))
}

case "$url" in
    https://*) ;;
    *) echo "usage: sh infra/deploy/setup.sh https://your-domain [data-folder]"; exit 1 ;;
esac

if [ -e .env ]; then
    echo "keeping the existing infra/deploy/.env"
else
    secret() { openssl rand -hex 32; }
    sed -e "s|^PUBLIC_URL=.*|PUBLIC_URL=$url|" \
        -e "s|^DATA_DIR=.*|DATA_DIR=$data|" \
        -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(secret)|" \
        -e "s|^APP_DB_PASSWORD=.*|APP_DB_PASSWORD=$(secret)|" \
        -e "s|^REDIS_PASSWORD=.*|REDIS_PASSWORD=$(secret)|" \
        -e "s|^OPERATOR_TOKEN=.*|OPERATOR_TOKEN=$(secret)|" \
        -e "s|^ENROLMENT_CODE=.*|ENROLMENT_CODE=$(openssl rand -hex 8)|" .env.example > .env
    chmod 600 .env
    echo "wrote infra/deploy/.env with new secrets. Add GEMINI_API_KEY to it."
fi

command -v docker > /dev/null || fail "Docker is not installed"
memory_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo 2> /dev/null || echo 0)
[ "$memory_kb" -ge 7000000 ] || fail "this machine has under 8 GB of RAM"

for file in valhalla/valhalla_tiles.tar osm/facilities.geojsonseq osm/places.geojsonseq osm/admin.geojsonseq; do
    [ -s "$data/$file" ] || fail "$data/$file is missing: build the map data (docs/deploy.md, step 2)"
done

host=$(echo "$url" | sed -e 's|^https://||' -e 's|[:/].*$||')
address=$(getent hosts "$host" 2> /dev/null | awk '{print $1; exit}')
[ -n "$address" ] || fail "$host does not resolve yet: point its DNS at this VM"

if [ "$problems" -gt 0 ]; then
    echo "$problems thing(s) to fix before deploying."
    exit 1
fi
echo "Ready. Start it with:"
echo "  docker compose -f infra/deploy/docker-compose.prod.yml --env-file infra/deploy/.env up -d --build"
