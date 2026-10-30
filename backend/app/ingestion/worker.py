"""Ingestion worker process: schedules every enabled source and the expiry sweep.

Run from backend/:  python -m app.ingestion.worker            (forever)
                    python -m app.ingestion.worker --once     (one pass over all sources, then exit)
"""

from __future__ import annotations

import argparse
import asyncio

import httpx

from app.core.db import close_pool, get_pool
from app.core.bus import close_redis
from app.core.logging import configure_logging, get_logger

from .base import Source
from .runner import poll_once
from .settings import SensesSettings, get_senses_settings
from .sources.facilities import FacilitiesSource
from .sources.firms import FirmsSource
from .sources.gdacs import GdacsSource
from .sources.open_meteo import OpenMeteoSource
from .sources.sachet import SachetSource
from .store import expire_due_hazards, publish_hazard_events

log = get_logger(__name__)
EXPIRY_SWEEP_S = 60


def build_sources(settings: SensesSettings) -> list[Source]:
    sources: list[Source] = [
        SachetSource(settings.sachet_rss_url, settings.sachet_poll_s),
        GdacsSource(settings.gdacs_country, settings.gdacs_lookback_days, settings.gdacs_poll_s),
        OpenMeteoSource(settings.region_bbox, settings.open_meteo_grid_deg, settings.open_meteo_poll_s),
        FacilitiesSource(settings.overpass_urls, settings.region_bbox, settings.facilities_extract_path,
                         settings.facilities_poll_s),
    ]  # fmt: skip
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


async def _loop(source: Source, http: httpx.AsyncClient) -> None:
    while True:
        try:
            await poll_once(source, http, await get_pool())
        except Exception:  # a bug in one source must not stop the others
            log.exception("poll_crashed", source=source.name)
        await asyncio.sleep(source.poll_s)


async def _sweeper() -> None:
    while True:
        try:
            expired = await sweep_expired()
            if expired:
                log.info("hazards_expired", count=expired)
        except Exception:
            log.exception("sweep_crashed")
        await asyncio.sleep(EXPIRY_SWEEP_S)


async def main(once: bool) -> None:
    settings = get_senses_settings()
    configure_logging(settings.log_level)
    headers = {"User-Agent": settings.http_user_agent}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as http:
        sources = build_sources(settings)
        try:
            if once:
                for source in sources:
                    await poll_once(source, http, await get_pool())
                await sweep_expired()
                return
            await asyncio.gather(*(_loop(s, http) for s in sources), _sweeper())
        finally:
            await close_pool()
            await close_redis()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    asyncio.run(main(parser.parse_args().once))
