"""Optional Langfuse tracing of LLM calls, over its public ingestion API.

Best effort by design: a tracing outage must never slow down or fail a relief plan, so every call
is short, and failures are logged and dropped. Disabled unless both keys are set.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx

from app.core.logging import get_logger

log = get_logger(__name__)
TIMEOUT_S = 3.0


class LangfuseTracer:
    def __init__(self, public_key: str, secret_key: str, host: str, http: httpx.AsyncClient):
        self.auth = (public_key, secret_key)
        self.url = f"{host.rstrip('/')}/api/public/ingestion"
        self.http = http

    async def record(self, *, name: str, model: str, system: str, prompt: str, output: str | None, latency_ms: int,
                     attempts: int, error: str | None) -> None:
        """One trace with one generation. Never raises."""
        end = datetime.now(UTC)
        start = end - timedelta(milliseconds=latency_ms)
        trace_id = str(uuid4())
        batch = [
            {"id": str(uuid4()), "type": "trace-create", "timestamp": end.isoformat(),
             "body": {"id": trace_id, "name": name, "timestamp": start.isoformat(), "input": prompt, "output": output}},
            {"id": str(uuid4()), "type": "generation-create", "timestamp": end.isoformat(),
             "body": {"id": str(uuid4()), "traceId": trace_id, "name": name, "model": model,
                      "startTime": start.isoformat(), "endTime": end.isoformat(),
                      "input": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                      "output": output, "metadata": {"attempts": attempts},
                      "level": "ERROR" if error else "DEFAULT", "statusMessage": error}},
        ]  # fmt: skip
        try:
            resp = await self.http.post(self.url, json={"batch": batch}, auth=self.auth, timeout=TIMEOUT_S)
            if resp.status_code >= 300:
                log.warning("langfuse_rejected", status=resp.status_code, body=resp.text[:200])
        except httpx.HTTPError as exc:
            log.warning("langfuse_unreachable", error=str(exc))
