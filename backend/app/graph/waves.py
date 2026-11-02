"""Sends the next wave by itself.

A plan carries what its trucks can. When they finish and the alert is still active, whatever is still needed goes
out as a new plan, through the same steps and the same rules, so a big job needs nobody to start each trip. `needs`
takes off what is already ordered, so nothing goes twice.

A wave that finds no stock or no truck for now tries again a few minutes later: depots refill and trucks come back.
Waves stop when the alert ends, when nothing is left, after `next_wave_max`, when a person rejects one, or when no
truck has finished for `next_wave_window_minutes`. The last plan then says why, for a person. That window also
means a restart does not set off every old alert at once.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import asyncpg

from app.core.logging import get_logger

from . import plans

if TYPE_CHECKING:
    from .nodes import Deps

log = get_logger(__name__)
POLL_S = 30

# Route replacements and evacuations have their own jobs: they neither start a wave nor count as the latest plan.
_MOVES_GOODS = """NOT ({0}.trigger_kind = 'replan' AND {0}.trigger_ref IS NOT NULL)
                  AND COALESCE({0}.trigger_ref, '') NOT LIKE 'evacuation:%'"""
DUE_SQL = f"""
    SELECT p.id::text, (SELECT array_agg(h.id::text) FROM v_active_hazards h WHERE h.id = ANY(p.hazard_ids)) AS active
    FROM plans p
    WHERE {_MOVES_GOODS.format("p")}
      AND ((p.status = 'dispatched' AND jsonb_path_exists(p.escalations, '$[*].quantity_short'))
           OR (p.status = 'failed' AND p.requested_by = $2 AND p.updated_at < now() - make_interval(mins => $4)
               AND COALESCE(p.unreachable_reason, '') NOT LIKE $5 || '%'))
      AND EXISTS (SELECT 1 FROM v_active_hazards h WHERE h.id = ANY(p.hazard_ids))
      AND NOT EXISTS (SELECT 1 FROM plans q WHERE q.hazard_ids && p.hazard_ids AND q.created_at > p.created_at
                                              AND {_MOVES_GOODS.format("q")})
      AND NOT EXISTS (SELECT 1 FROM plans q WHERE q.hazard_ids && p.hazard_ids
                                              AND q.status IN ('pending', 'planning', 'awaiting_approval', 'approved'))
      AND NOT EXISTS (SELECT 1 FROM routes r JOIN plans q ON q.id = r.plan_id
                      WHERE q.hazard_ids && p.hazard_ids AND r.kind = 'supply' AND r.status IN ('planned', 'active'))
      AND (SELECT max(r.completed_at) FROM routes r JOIN plans q ON q.id = r.plan_id WHERE q.hazard_ids && p.hazard_ids)
          > now() - make_interval(mins => $1)
      AND (SELECT count(*) FROM plans w WHERE w.requested_by = $2 AND w.status <> 'failed' AND w.hazard_ids && p.hazard_ids) < $3
    ORDER BY p.created_at"""


async def left(pool: asyncpg.Pool, hazard_ids: list, most: int) -> int:
    """How many more waves these alerts can have: none once they have ended."""
    row = await pool.fetchrow(
        """SELECT EXISTS (SELECT 1 FROM v_active_hazards WHERE id = ANY($1::uuid[])) AS active,
                  (SELECT count(*) FROM plans WHERE requested_by = $2 AND status <> 'failed' AND hazard_ids && $1::uuid[]) AS sent""",
        hazard_ids, plans.NEXT_WAVE)  # fmt: skip
    return max(0, most - row["sent"]) if row["active"] else 0


async def send_due(deps: "Deps") -> int:
    s = deps.settings
    sent = 0
    for row in await deps.pool.fetch(DUE_SQL, s.next_wave_window_minutes, plans.NEXT_WAVE, s.next_wave_max,
                                     s.next_wave_retry_minutes, plans.NOTHING_LEFT):  # fmt: skip
        # No parent: a parent marks a reroute, and this is new goods for the alert, not a replacement.
        _, created = await plans.request_plan(deps.pool, trigger_kind="replan", trigger_ref=None, hazard_ids=row["active"],
                                              requested_by=plans.NEXT_WAVE, idempotency_key=f"wave:{row['id']}")  # fmt: skip
        sent += created
    return sent


async def run(deps: "Deps") -> None:
    while True:
        try:
            if sent := await send_due(deps):
                log.info("next_waves_requested", count=sent)
        except Exception:  # a failed round must not end the waves
            log.exception("next_wave_round_failed")
        await asyncio.sleep(POLL_S)
