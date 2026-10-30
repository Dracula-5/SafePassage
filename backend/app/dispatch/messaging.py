"""Outbound SMS/WhatsApp via Twilio's REST API, only when credentials are configured.

Without credentials nothing is sent and the notification row records why ("not sent: ...").
Every message has an idempotency key; a key already in `notifications` is never re-sent.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.resilience import CircuitBreaker, SourceUnavailable, resilient_request
from app.core.settings_brain import BrainSettings


@dataclass
class SendResult:
    status: str            # sent | failed
    provider_ref: str | None
    error: str | None


class Messenger:
    def __init__(self, settings: BrainSettings, http: httpx.AsyncClient):
        self.settings = settings
        self.http = http
        self.breaker = CircuitBreaker("twilio", failure_threshold=3, reset_after_s=120)

    @property
    def configured(self) -> bool:
        s = self.settings
        return bool(s.twilio_account_sid and s.twilio_auth_token and s.twilio_from_number)

    async def send(self, channel: str, to: str, body: str) -> SendResult:
        if not self.configured:
            return SendResult("failed", None, "not sent: Twilio credentials not configured")
        s = self.settings
        sender = f"whatsapp:{s.twilio_from_number}" if channel == "whatsapp" else s.twilio_from_number
        recipient = f"whatsapp:{to}" if channel == "whatsapp" else to
        try:
            resp = await resilient_request(
                self.http, "POST", f"https://api.twilio.com/2010-04-01/Accounts/{s.twilio_account_sid}/Messages.json",
                self.breaker, data={"From": sender, "To": recipient, "Body": body[:1500]},
                auth=(s.twilio_account_sid, s.twilio_auth_token), timeout_s=20, retries=1,
            )  # fmt: skip
        except SourceUnavailable as exc:
            return SendResult("failed", None, f"twilio unavailable: {exc.reason}")
        if resp.status_code >= 300:
            return SendResult("failed", None, f"twilio HTTP {resp.status_code}: {resp.text[:200]}")
        return SendResult("sent", resp.json().get("sid"), None)
