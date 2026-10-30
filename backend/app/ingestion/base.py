"""Common shape of a polled source."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import asyncpg
import httpx

from app.core.resilience import CircuitBreaker

from .models import AdminArea, NormalizedFacility, NormalizedHazard, NormalizedPlace


@dataclass
class SupplierStock:
    """Stock published by an external system (not a portal check-in), with its pickup site."""

    external_ref: str
    supplier_name: str
    site_name: str
    lon: float
    lat: float
    item: object  # ItemCode
    quantity: float
    unit_weight_kg: float
    reported_at: object  # datetime
    attributes: dict


@dataclass
class FetchResult:
    hazards: list[NormalizedHazard]
    facilities: list[NormalizedFacility]
    used_fallback: str | None = None      # name of the fallback source actually used, if any
    places: list[NormalizedPlace] = field(default_factory=list)
    admin_areas: list[AdminArea] = field(default_factory=list)
    supplier_stock: list[SupplierStock] = field(default_factory=list)
    skipped: int = 0                      # records dropped as malformed (logged individually)


class Source(Protocol):
    name: str
    poll_s: int
    ttl_s: int
    breaker: CircuitBreaker

    async def fetch(self, http: httpx.AsyncClient, conn: asyncpg.Pool) -> FetchResult: ...
