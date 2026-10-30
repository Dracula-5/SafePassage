"""Valhalla client request/response handling against a scripted transport (no network)."""

import json

import httpx
import pytest

from app.core.resilience import CircuitBreaker, SourceUnavailable
from app.routing.polyline import decode
from app.routing.valhalla import (
    Location, LocationNotRoutableError, NoRouteError, ValhallaClient, ValhallaError,
)


def encode(points, precision=6):
    """Test-side encoder used to build Valhalla-shaped responses."""
    out, prev = [], (0, 0)
    for lon, lat in points:
        cur = (round(lat * 10**precision), round(lon * 10**precision))
        for delta in (cur[0] - prev[0], cur[1] - prev[1]):
            v = ~(delta << 1) if delta < 0 else delta << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        prev = cur
    return "".join(out)


def test_decode_reference_polyline():
    # Google's documented example (precision 5).
    assert decode("_p~iF~ps|U_ulLnnqC_mqNvxq`@", precision=5) == [
        (-120.2, 38.5), (-120.95, 40.7), (-126.453, 43.252)]


def test_decode_roundtrip_precision6():
    pts = [(80.123456, 16.654321), (80.2, 16.7), (79.999999, 16.000001)]
    assert decode(encode(pts)) == pts


def client_with(handler):
    return ValhallaClient("http://valhalla", httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                          breaker=CircuitBreaker("valhalla"), retries=1)


A, B = Location(lon=80.0, lat=16.0), Location(lon=80.1, lat=16.1)


async def test_route_sends_exclusions_and_converts_units():
    seen = {}
    leg_shape = [(80.0, 16.0), (80.05, 16.05), (80.1, 16.1)]

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"trip": {
            "summary": {"length": 12.5, "time": 900},
            "legs": [{"summary": {"length": 12.5, "time": 900}, "shape": encode(leg_shape)}]}})

    ring = [[80.04, 16.04], [80.06, 16.04], [80.06, 16.06], [80.04, 16.04]]
    result = await client_with(handler).route([A, B], exclude=[ring])
    assert seen["exclude_polygons"] == [ring] and seen["costing"] == "truck"
    assert result.distance_m == 12500 and result.duration_s == 900
    assert result.shape == leg_shape


async def test_matrix_maps_nulls_to_none():
    def handler(request):
        return httpx.Response(200, json={"sources_to_targets": [[
            {"from_index": 0, "to_index": 0, "time": 0, "distance": 0},
            {"from_index": 0, "to_index": 1, "time": None, "distance": None}]]})

    m = await client_with(handler).matrix([A], [A, B])
    assert m.durations_s == [[0.0, None]] and m.distances_m == [[0.0, None]]


@pytest.mark.parametrize("code,exc", [(442, NoRouteError), (171, LocationNotRoutableError), (999, ValhallaError)])
async def test_error_codes_become_typed_errors(code, exc):
    client = client_with(lambda r: httpx.Response(400, json={"error_code": code, "error": "x"}))
    with pytest.raises(exc):
        await client.route([A, B])


async def test_server_errors_retry_then_open_breaker():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503, text="busy")

    client = ValhallaClient("http://valhalla", httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                            breaker=CircuitBreaker("valhalla", failure_threshold=1), retries=1)
    with pytest.raises(SourceUnavailable):
        await client.route([A, B])
    assert len(calls) == 2
    with pytest.raises(SourceUnavailable, match="circuit open"):
        await client.route([A, B])
    assert len(calls) == 2


async def test_non_json_error_is_typed():
    client = client_with(lambda r: httpx.Response(404, text="<html>"))
    with pytest.raises(ValhallaError):
        await client.route([A, B])
