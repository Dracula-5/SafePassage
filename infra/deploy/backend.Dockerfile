# Backend image: API, ingestion worker and brain worker share it. Build from the repo root.
FROM debian:bookworm-slim AS mapdata
RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates curl osmium-tool \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /work
COPY infra/valhalla/build.sh ./build.sh
COPY infra/osm/extract_facilities.sh ./extract_facilities.sh
COPY infra/osm/extract_places_admin.sh ./extract_places_admin.sh
RUN DATA_DIR=/data bash ./build.sh \
    && DATA_DIR=/data bash ./extract_facilities.sh \
    && DATA_DIR=/data bash ./extract_places_admin.sh

FROM python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337

WORKDIR /app
COPY contracts ./contracts
COPY backend/requirements ./backend/requirements
# constraints.txt pins every package, so a rebuild can't quietly pull in something new.
RUN pip install --no-cache-dir -c backend/requirements/constraints.txt -e ./contracts/python \
    -r backend/requirements/brain.txt -r backend/requirements/senses.txt
COPY backend/app ./backend/app
COPY backend/migrations ./backend/migrations
COPY --from=mapdata /data/osm/facilities.geojsonseq /data/osm/places.geojsonseq /data/osm/admin.geojsonseq ./infra/osm/data/

# documents must exist here: a new named volume takes its owner from the image, or PO PDFs can't be written.
RUN useradd --create-home relief && mkdir -p /data/documents && chown -R relief /data
USER relief
WORKDIR /app/backend
ENV HOME=/home/relief PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header", "--timeout-keep-alive", "75"]
