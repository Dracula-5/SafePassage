-- Contract patch 001 (approved 2026-10-30): populated places for the Needs agent.
-- Owner of the table: B (writes). A reads through v_places only.
-- Source: OpenStreetMap place=* nodes; population from their `population` tag (mostly Census 2011).

CREATE TABLE places (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source              text NOT NULL,                     -- e.g. 'osm'
    source_ref          text NOT NULL,                     -- e.g. 'node/123'
    place_type          text NOT NULL,                     -- city, town, village, hamlet, suburb, neighbourhood
    name                text,
    geom                geometry(Point, 4326) NOT NULL,
    population          integer CHECK (population >= 0),   -- NULL when the source has no figure
    population_source   text,                              -- e.g. 'census2011', 'pmgsy', as tagged
    fetched_at          timestamptz NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source, source_ref)
);
CREATE INDEX places_geom_idx ON places USING gist (geom);

-- Population figures are a lower bound: many settlements have none. Consumers must report
-- coverage (places with population / all places) next to any total they compute.
CREATE VIEW v_places AS
SELECT id, source, source_ref, place_type, name, geom, population, population_source, fetched_at
FROM places;
