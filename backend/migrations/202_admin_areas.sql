-- Owner: B (private table, not part of the contract). Official OSM administrative boundaries,
-- used to place SACHET alerts by the area names in CAP <areaDesc> when SACHET will not serve
-- the alert's own polygon.
CREATE TABLE admin_areas (
    id          bigserial PRIMARY KEY,
    source_ref  text NOT NULL UNIQUE,          -- e.g. relation/123
    admin_level smallint NOT NULL,
    name        text NOT NULL,
    name_norm   text NOT NULL,                 -- normalized for matching (see ingestion/area_names.py)
    geom        geometry(MultiPolygon, 4326) NOT NULL,
    fetched_at  timestamptz NOT NULL
);
CREATE INDEX admin_areas_name_idx ON admin_areas (name_norm, admin_level);
CREATE INDEX admin_areas_geom_idx ON admin_areas USING gist (geom);
