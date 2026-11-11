# Build the road router from Geofabrik data so no large map artifact needs to be committed or hosted.
FROM debian:bookworm-slim AS mapdata
RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates curl osmium-tool \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /work
COPY infra/valhalla/build.sh ./build.sh
RUN DATA_DIR=/data bash ./build.sh

FROM ghcr.io/valhalla/valhalla-scripted@sha256:7f37f55ed41131b10f349798ad4fa91f0d8bc5c132fff82aea969b22910e459a

COPY --from=mapdata /data/valhalla/andhra-pradesh.osm.pbf /custom_files/andhra-pradesh.osm.pbf
ENV serve_tiles=True \
    build_tar=True \
    build_admins=True \
    build_time_zones=True \
    use_tiles_ignore_pbf=True \
    server_threads=4
RUN valhalla_build_config --mjolnir-tile-extract /custom_files/valhalla_tiles.tar \
        --mjolnir-tile-dir /custom_files/valhalla_tiles --mjolnir-max-cache-size 268435456 \
        --service-limits-max-exclude-polygons-length 5000000 --service-limits-max-exclude-polygons-vertices 10000 \
        > /custom_files/valhalla.json \
    && chown -R valhalla /custom_files

USER valhalla
EXPOSE 8002
CMD ["build_tiles"]
