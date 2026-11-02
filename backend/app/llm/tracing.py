"""Optional Langfuse tracing of LLM calls, over its OpenTelemetry endpoint (OTLP as JSON).

Best effort by design: a tracing outage must never slow down or fail a relief plan, so every call
is short, and failures are logged and dropped. Disabled unless both keys are set.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx

from app.core.logging import get_logger

log = get_logger(__name__)
TIMEOUT_S = 3.0
STATUS_OK, STATUS_ERROR = 1, 2


def _attribute(key: str, value: object) -> dict:
    return {"key": key, "value": {"stringValue": value if isinstance(value, str) else json.dumps(value)}}


def _nanoseconds(moment: datetime) -> str:
    return str(int(moment.timestamp() * 1_000_000_000))


class LangfuseTracer:
    def __init__(self, public_key: str, secret_key: str, host: str, http: httpx.AsyncClient):
        self.auth = (public_key, secret_key)
        self.url = f"{host.rstrip('/')}/api/public/otel/v1/traces"
        self.http = http

    async def record(self, *, name: str, model: str, system: str, prompt: str, output: str | None, latency_ms: int,
                     attempts: int, error: str | None) -> None:
        """One span that Langfuse shows as a generation. Never raises."""
        end = datetime.now(UTC)
        start = end - timedelta(milliseconds=latency_ms)
        attributes = [
            _attribute("langfuse.trace.name", name),
            _attribute("langfuse.observation.type", "generation"),
            _attribute("langfuse.observation.model.name", model),
            _attribute("gen_ai.request.model", model),
            _attribute("langfuse.observation.input", [{"role": "system", "content": system}, {"role": "user", "content": prompt}]),
            _attribute("langfuse.observation.output", output or ""),
            _attribute("langfuse.observation.metadata.attempts", str(attempts)),
        ]
        span = {"traceId": uuid4().hex, "spanId": uuid4().hex[:16], "name": name, "kind": 1, "attributes": attributes,
                "startTimeUnixNano": _nanoseconds(start), "endTimeUnixNano": _nanoseconds(end),
                "status": {"code": STATUS_ERROR, "message": error} if error else {"code": STATUS_OK}}  # fmt: skip
        body = {"resourceSpans": [{"resource": {"attributes": [_attribute("service.name", "reliefroute")]},
                                   "scopeSpans": [{"scope": {"name": "reliefroute"}, "spans": [span]}]}]}  # fmt: skip
        try:
            resp = await self.http.post(self.url, json=body, auth=self.auth, timeout=TIMEOUT_S)
            # A 200 can still carry refused spans, so a quiet failure cannot look like success.
            if resp.status_code >= 300 or resp.json().get("partialSuccess", {}).get("rejectedSpans"):
                log.warning("langfuse_rejected", status=resp.status_code, body=resp.text[:200])
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("langfuse_unreachable", error=str(exc))
