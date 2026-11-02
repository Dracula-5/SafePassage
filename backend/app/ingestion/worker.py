"""Ingestion worker process: schedules every enabled source and the expiry sweep.

Run from backend/:  python -m app.ingestion.worker            (forever)
                    python -m app.ingestion.worker --once     (one pass over all sources, then exit)
"""

from __future__ import annotations

import argparse
import asyncio
import time

import httpx

from app.core.db import Singleton, close_pool, get_pool
from app.core.bus import close_redis, heartbeat
from app.core.logging import configure_logging, get_logger

from .base import Source
from .runner import poll_once
from .settings import SensesSettings, get_senses_settings
from .sources.aepds import AepdsSource
from .sources.facilities import FacilitiesSource
from .sources.firms import FirmsSource
from .sources.gdacs import GdacsSource
from .sources.open_meteo import OpenMeteoSource
from .sources.osm_extract import OsmExtractSource
from .sources.sachet import SachetSource
from .store import expire_due_hazards, publish_hazard_events

log = get_logger(__name__)
EXPIRY_SWEEP_S = 60
PRUNE_EVERY_S = 24 * 3600
RETRY_AFTER_FAILURE_S = 120


def build_sources(settings: SensesSettings) -> list[Source]:
    sources: list[Source] = [
        # Boundaries first: the SACHET fallback resolves area names against them.
        OsmExtractSource(settings.places_extract_path, settings.admin_extract_path, settings.osm_extract_poll_s),
        SachetSource(settings.sachet_rss_url, settings.sachet_poll_s),
        GdacsSource(settings.gdacs_country, settings.gdacs_lookback_days, settings.gdacs_poll_s),
        OpenMeteoSource(settings.region_bbox, settings.open_meteo_grid_deg, settings.open_meteo_poll_s),
        FacilitiesSource(settings.overpass_urls, settings.region_bbox, settings.facilities_extract_path,
                         settings.facilities_poll_s),
    ]  # fmt: skip
    if settings.aepds_enabled:
        sources.append(AepdsSource(settings.aepds_poll_s))  # after osm_extract: needs mandal boundaries
    if settings.firms_map_key:
        sources.append(FirmsSource(settings.firms_map_key, settings.region_bbox, settings.firms_poll_s))
    else:
        log.warning("source_disabled", source="firms", reason="FIRMS_MAP_KEY not set")
    return sources


async def sweep_expired() -> int:
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        events = await expire_due_hazards(conn)
    await publish_hazard_events(events)
    return len(events)


def next_delay_s(source: Source, ok: bool) -> int:
    """A failed poll retries soon: on a new database AePDS fails until the boundaries are loaded,
    and waiting its full 6 h interval would leave the system without stock data."""
    return source.poll_s if ok else min(source.poll_s, RETRY_AFTER_FAILURE_S)


async def _loop(source: Source, http: httpx.AsyncClient) -> None:
    while True:
        ok = False
        try:
            ok = (await poll_once(source, http, await get_pool())).ok
        except Exception:  # a bug in one source must not stop the others
            log.exception("poll_crashed", source=source.name)
        await asyncio.sleep(next_delay_s(source, ok))


async def prune_telemetry(days: int) -> int:
    """One truck reports about 8,600 fixes a day. agent_events keeps what mattered, for good."""
    pool = await get_pool()
    deleted = await pool.execute("DELETE FROM telemetry WHERE recorded_at < now() - make_interval(days => $1)", days)
    return int(deleted.split()[-1])


async def _sweeper(retention_days: int) -> None:
    next_prune = 0.0
    while True:
        try:
            expired = await sweep_expired()
            if expired:
                log.info("hazards_expired", count=expired)
            if time.monotonic() >= next_prune:
                next_prune = time.monotonic() + PRUNE_EVERY_S
                if pruned := await prune_telemetry(retention_days):
                    log.info("telemetry_pruned", count=pruned)
        except Exception:
            log.exception("sweep_crashed")
        await asyncio.sleep(EXPIRY_SWEEP_S)


async def main(once: bool) -> None:
    settings = get_senses_settings()
    configure_logging(settings.log_level)
    headers = {"User-Agent": settings.http_user_agent}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as http:
        sources = build_sources(settings)
        singleton = Singleton("ingestion")
        try:
            if once:
                for source in sources:
                    await poll_once(source, http, await get_pool())
                await sweep_expired()
                return
            await singleton.acquire()
            await asyncio.gather(*(_loop(s, http) for s in sources), _sweeper(settings.telemetry_retention_days),
                                 singleton.keep(), heartbeat("ingestion"))  # fmt: skip
        finally:
            await singleton.release()
            await close_pool()
            await close_redis()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    asyncio.run(main(parser.parse_args().once))
