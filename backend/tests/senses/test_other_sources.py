import json

import pytest
from shapely import Point

from app.ingestion.sources.facilities import classify, facility, parse_extract, parse_overpass
from app.ingestion.sources.firms import parse_firms_csv
from app.ingestion.sources.gdacs import event_hazards
from app.ingestion.sources.open_meteo import cell_hazards, classify_rain, grid

from .conftest import fixture_text


def test_gdacs_real_cyclone_levels():
    event = json.loads(fixture_text("gdacs_events_india.json"))["features"][0]
    hazards = event_hazards(event, json.loads(fixture_text("gdacs_geometry_tc.json")))
    assert {h.severity.value for h in hazards} == {"extreme", "severe", "moderate"}
    assert all(h.hazard_type == "cyclone" for h in hazards)
    assert len({h.dedupe_key for h in hazards}) == 3


def test_gdacs_past_event_is_expired():
    event = json.loads(fixture_text("gdacs_events_india.json"))["features"][0]
    event["properties"]["iscurrent"] = "false"
    hazards = event_hazards(event, json.loads(fixture_text("gdacs_geometry_tc.json")))
    assert all(h.status == "expired" and h.expires_at is not None for h in hazards)


@pytest.mark.parametrize("mm,term", [(None, None), (64.4, None), (64.5, "heavy rainfall"),
                                     (115.6, "very heavy rainfall"), (204.5, "extremely heavy rainfall")])
def test_imd_rain_categories(mm, term):
    result = classify_rain(mm)
    assert (result[0] if result else None) == term


def test_grid_covers_bbox():
    pts = grid((80.0, 16.0, 81.0, 17.0), 0.5)
    assert pts == [(80.25, 16.25), (80.75, 16.25), (80.25, 16.75), (80.75, 16.75)]


def test_open_meteo_cells_from_real_payload_shape():
    payload = json.loads(fixture_text("open_meteo_two_points.json"))
    payload[1]["daily"]["precipitation_sum"][2] = 130.0  # test-only value to cross the IMD threshold
    hazards = cell_hazards([(80.25, 16.25), (80.75, 16.75)], payload, 0.5)
    assert len(hazards) == 1 and hazards[0].severity == "severe"
    assert hazards[0].geom.bounds == (80.5, 16.5, 81.0, 17.0)


def test_firms_csv_footprint():
    csv = ("latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,"
           "bright_ti5,frp,daynight\n16.5,80.5,330.1,0.4,0.6,2026-10-30,830,N,VIIRS,h,2.0NRT,290.2,5.1,D\n")
    [h] = parse_firms_csv(csv)
    assert h.severity == "severe" and h.hazard_type == "wildfire"
    x0, y0, x1, y1 = h.geom.bounds
    assert round((y1 - y0) * 110.57, 2) == 0.6  # track km


@pytest.mark.parametrize("tags,kind", [
    ({"amenity": "hospital"}, "hospital"), ({"amenity": "clinic"}, "hospital"), ({"amenity": "school"}, "school"),
    ({"amenity": "shelter", "shelter_type": "public_transport"}, None), ({"amenity": "shelter"}, "shelter"),
    ({"emergency": "assembly_point"}, "shelter"), ({"building": "warehouse"}, "warehouse"), ({"shop": "x"}, None),
])
def test_facility_classification(tags, kind):
    assert (classify(tags).value if classify(tags) else None) == kind


def test_unnamed_facilities_are_dropped():
    assert facility("node", 1, {"amenity": "hospital"}, Point(0, 0)) is None


def test_overpass_and_extract_parsing(tmp_path):
    payload = {"elements": [
        {"type": "node", "id": 1, "lat": 16.5, "lon": 80.6, "tags": {"amenity": "hospital", "name": "H", "beds": "50"}},
        {"type": "way", "id": 2, "center": {"lat": 16.6, "lon": 80.7}, "tags": {"building": "warehouse", "name": "W"}},
        {"type": "node", "id": 3, "lat": 1, "lon": 1, "tags": {"amenity": "bench", "name": "B"}}]}
    found = parse_overpass(payload)
    assert [(f.source_ref, f.kind.value) for f in found] == [("node/1", "hospital"), ("way/2", "warehouse")]
    path = tmp_path / "f.geojsonseq"
    path.write_text("\x1e" + json.dumps({"type": "Feature", "id": "w9", "geometry": {"type": "Polygon",
        "coordinates": [[[80, 16], [80.01, 16], [80.01, 16.01], [80, 16]]]},
        "properties": {"amenity": "school", "name": "S", "capacity": "300"}}) + "\n")  # fmt: skip
    [school] = parse_extract(path)
    assert school.source_ref == "way/9" and school.capacity_people == 300
