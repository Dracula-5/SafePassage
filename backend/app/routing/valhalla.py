"""Async Valhalla client: route and travel-time matrix, both hazard-aware.

Every call goes through core.resilience (timeout, jittered retry, circuit breaker).
Valhalla's "no path" answers become typed exceptions so the planner can mark a
destination unreachable and look for an alternate hub instead of crashing.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.core.logging import get_logger
from app.core.resilience import CircuitBreaker, resilient_request

from .polyline import decode

log = get_logger(__name__)

Costing = Literal["truck", "auto"]
Ring = list[list[float]]  # [[lon, lat], ...]

# Valhalla error codes (https://valhalla.github.io/valhalla/api/turn-by-turn/api-reference/#http-status-codes-and-conditions)
NO_PATH_CODES = {442, 443}           # no path could be found between locations
NO_EDGE_CODES = {171, 170, 158}      # location not near a routable road / too far


class Location(BaseModel):
    model_config = ConfigDict(frozen=True)

    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)


class RouteLeg(BaseModel):
    distance_m: float
    duration_s: float
    shape: list[tuple[float, float]]


class RouteResult(BaseModel):
    distance_m: float
    duration_s: float
    shape: list[tuple[float, float]] = Field(description="[(lon, lat)] for the whole trip")
    legs: list[RouteLeg]


class MatrixResult(BaseModel):
    """durations_s[i][j]/distances_m[i][j] from source i to target j; None = no path."""

    durations_s: list[list[float | None]]
    distances_m: list[list[float | None]]


class ValhallaError(Exception):
    def __init__(self, message: str, code: int | None = None, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status


class NoRouteError(ValhallaError):
    """No road path exists (e.g. every path crosses an excluded hazard)."""


class LocationNotRoutableError(ValhallaError):
    """A location is too far from any road Valhalla knows."""


class ValhallaClient:
    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient,
        breaker: CircuitBreaker | None = None,
        timeout_s: float = 15.0,
        retries: int = 2,
    ):
        self._base = base_url.rstrip("/")
        self._client = client
        self._breaker = breaker or CircuitBreaker("valhalla", failure_threshold=3, reset_after_s=30)
        self._timeout_s = timeout_s
        self._retries = retries

    @property
    def breaker(self) -> CircuitBreaker:
        return self._breaker

    async def _post(self, endpoint: str, body: dict) -> dict:
        response = await resilient_request(
            self._client, "POST", f"{self._base}/{endpoint}", self._breaker,
            retries=self._retries, timeout_s=self._timeout_s, json=body,
        )  # fmt: skip
        try:
            data = response.json()
        except ValueError as exc:
            raise ValhallaError(f"non-JSON response ({response.status_code})", status=response.status_code) from exc
        if response.status_code >= 400:
            code, message = data.get("error_code"), data.get("error", response.text)
            log.info("valhalla_rejected", endpoint=endpoint, code=code, error=message)
            if code in NO_PATH_CODES:
                raise NoRouteError(message, code, response.status_code)
            if code in NO_EDGE_CODES:
                raise LocationNotRoutableError(message, code, response.status_code)
            raise ValhallaError(message, code, response.status_code)
        return data

    @staticmethod
    def _body(locations: Sequence[Location], costing: Costing, exclude: Sequence[Ring]) -> dict:
        body: dict = {
            "locations": [{"lon": loc.lon, "lat": loc.lat} for loc in locations],
            "costing": costing,
            "units": "kilometers",
        }
        if exclude:
            body["exclude_polygons"] = [list(ring) for ring in exclude]
        return body

    async def route(
        self, locations: Sequence[Location], exclude: Sequence[Ring] = (), costing: Costing = "truck"
    ) -> RouteResult:
        if len(locations) < 2:
            raise ValueError("route needs at least two locations")
        body = self._body(locations, costing, exclude) | {"directions_type": "none"}
        trip = (await self._post("route", body))["trip"]
        legs = [
            RouteLeg(
                distance_m=leg["summary"]["length"] * 1000,
                duration_s=leg["summary"]["time"],
                shape=decode(leg["shape"], precision=6),
            )
            for leg in trip["legs"]
        ]
        shape = [pt for i, leg in enumerate(legs) for pt in (leg.shape if i == 0 else leg.shape[1:])]
        return RouteResult(
            distance_m=trip["summary"]["length"] * 1000, duration_s=trip["summary"]["time"], shape=shape, legs=legs
        )

    async def matrix(
        self,
        sources: Sequence[Location],
        targets: Sequence[Location],
        exclude: Sequence[Ring] = (),
        costing: Costing = "truck",
    ) -> MatrixResult:
        body = {
            "sources": [{"lon": s.lon, "lat": s.lat} for s in sources],
            "targets": [{"lon": t.lon, "lat": t.lat} for t in targets],
            "costing": costing,
            "units": "kilometers",
        }
        if exclude:
            body["exclude_polygons"] = [list(ring) for ring in exclude]
        data = await self._post("sources_to_targets", body)
        durations: list[list[float | None]] = [[None] * len(targets) for _ in sources]
        distances: list[list[float | None]] = [[None] * len(targets) for _ in sources]
        # Valhalla returns rows per source; unreachable pairs have null time/distance.
        for row in data["sources_to_targets"]:
            for cell in row:
                i, j = cell["from_index"], cell["to_index"]
                if cell.get("time") is not None:
                    durations[i][j] = float(cell["time"])
                if cell.get("distance") is not None:
                    distances[i][j] = float(cell["distance"]) * 1000
        return MatrixResult(durations_s=durations, distances_m=distances)

    async def status(self) -> dict:
        response = await resilient_request(
            self._client, "GET", f"{self._base}/status", self._breaker, retries=0, timeout_s=5
        )
        return response.json()
