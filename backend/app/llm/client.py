"""Provider-agnostic structured generation with Pydantic validation and repair-and-retry.

`generate(system, prompt, schema)` asks the model for JSON matching `schema`. If the reply is
not valid JSON or fails validation, the exact validation error is sent back with a request to
correct it, up to `max_attempts` times. Callers get the validated object plus an audit record
(attempts, repaired, model, latency) for agent_events.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Generic, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.core.logging import get_logger
from app.core.resilience import CircuitBreaker, SourceUnavailable, resilient_request

if TYPE_CHECKING:
    from .tracing import LangfuseTracer

log = get_logger(__name__)
T = TypeVar("T", bound=BaseModel)


class LLMOutputError(Exception):
    """The model never produced output that validates against the schema."""

    def __init__(self, message: str, attempts: list["Attempt"]):
        super().__init__(message)
        self.attempts = attempts


@dataclass
class Attempt:
    ok: bool
    error: str | None
    latency_ms: int


@dataclass
class LLMResult(Generic[T]):
    value: T
    model: str
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def repaired(self) -> bool:
        return len(self.attempts) > 1

    def audit(self) -> dict:
        return {"model": self.model, "attempts": len(self.attempts), "repaired": self.repaired,
                "errors": [a.error for a in self.attempts if a.error], "latency_ms": sum(a.latency_ms for a in self.attempts)}


class ChatBackend(Protocol):
    """One raw completion: (system, conversation as [(role, text)]) -> text."""

    model: str

    async def complete(self, system: str, turns: list[tuple[str, str]]) -> str: ...


def extract_json(text: str) -> str:
    """Tolerate ```json fences and leading prose; return the outermost JSON object text."""
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise ValueError("no JSON object in reply")
    return text[start : end + 1]


class LLMClient:
    def __init__(self, backend: ChatBackend, max_attempts: int = 3, tracer: "LangfuseTracer | None" = None):
        self.backend = backend
        self.max_attempts = max_attempts
        self.tracer = tracer

    async def _trace(self, schema: type[BaseModel], system: str, prompt: str, reply: str | None, attempts: list[Attempt],
                     error: str | None) -> None:
        if self.tracer:
            await self.tracer.record(name=schema.__name__, model=self.backend.model, system=system, prompt=prompt,
                                     output=reply, latency_ms=sum(a.latency_ms for a in attempts),
                                     attempts=len(attempts), error=error)  # fmt: skip

    async def generate(self, system: str, prompt: str, schema: type[T]) -> LLMResult[T]:
        schema_text = json.dumps(schema.model_json_schema(), separators=(",", ":"))
        instructions = (f"{system}\n\nReply with ONE JSON object only (no prose, no markdown) that validates "
                        f"against this JSON Schema:\n{schema_text}")  # fmt: skip
        turns: list[tuple[str, str]] = [("user", prompt)]
        attempts: list[Attempt] = []
        reply: str | None = None
        for _ in range(self.max_attempts):
            started = time.monotonic()
            reply = await self.backend.complete(instructions, turns)
            latency = int((time.monotonic() - started) * 1000)
            try:
                value = schema.model_validate_json(extract_json(reply))
            except (ValidationError, ValueError) as exc:
                error = str(exc)[:1500]
                attempts.append(Attempt(ok=False, error=error, latency_ms=latency))
                log.warning("llm_output_invalid", model=self.backend.model, schema=schema.__name__, error=error[:300])
                turns += [("model", reply), ("user", f"That reply was invalid: {error}\nReturn the corrected JSON object only.")]
                continue
            attempts.append(Attempt(ok=True, error=None, latency_ms=latency))
            await self._trace(schema, system, prompt, reply, attempts, None)
            return LLMResult(value=value, model=self.backend.model, attempts=attempts)
        failure = f"{schema.__name__}: no valid output after {self.max_attempts} attempts"
        await self._trace(schema, system, prompt, reply, attempts, failure)
        raise LLMOutputError(failure, attempts)


class GeminiBackend:
    """Google Gemini via the public REST API (generateContent), over an ordered list of models.

    Gemini models come and go (404 when retired) and are often overloaded (503/429 or slow).
    Each call tries the models in order with its own breaker and time budget; the model that
    answers moves to the front, and `model` names it for the audit trail.
    """

    BASE = "https://generativelanguage.googleapis.com/v1beta"
    SKIP_STATUS = {404, 429, 500, 503}

    def __init__(self, api_key: str, models: list[str], http: httpx.AsyncClient, timeout_s: float = 30.0,
                 temperature: float = 0.2):
        self.api_key = api_key
        self.models = list(models)
        self.http = http
        self.timeout_s = timeout_s
        self.temperature = temperature
        self.breakers = {m: CircuitBreaker(f"gemini:{m}", failure_threshold=2, reset_after_s=300) for m in self.models}
        self.breaker = CircuitBreaker("gemini")  # used only for list_models

    @property
    def model(self) -> str:
        return self.models[0] if self.models else ""

    async def _call(self, model: str, body: dict) -> str:
        breaker = self.breakers.setdefault(model, CircuitBreaker(f"gemini:{model}", failure_threshold=2, reset_after_s=300))
        resp = await resilient_request(
            self.http, "POST", f"{self.BASE}/models/{model}:generateContent", breaker,
            timeout_s=self.timeout_s, retries=0, json=body, headers={"x-goog-api-key": self.api_key},
        )  # fmt: skip
        if resp.status_code in self.SKIP_STATUS:
            breaker.record_failure()
            raise SourceUnavailable(f"gemini:{model}", f"HTTP {resp.status_code}")
        if resp.status_code != 200:
            raise SourceUnavailable(f"gemini:{model}", f"HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        try:
            return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError) as exc:
            reason = (data.get("promptFeedback") or {}).get("blockReason") or data.get("candidates", [{}])[0].get("finishReason")
            raise SourceUnavailable(f"gemini:{model}", f"no text in reply ({reason})") from exc

    async def complete(self, system: str, turns: list[tuple[str, str]]) -> str:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": role, "parts": [{"text": text}]} for role, text in turns],
            "generationConfig": {"temperature": self.temperature, "responseMimeType": "application/json"},
        }
        errors = []
        for model in list(self.models):
            try:
                text = await self._call(model, body)
            except SourceUnavailable as exc:
                errors.append(f"{model}: {exc.reason}")
                log.warning("gemini_model_unavailable", model=model, reason=exc.reason)
                continue
            if self.models[0] != model:  # prefer what works
                self.models.remove(model)
                self.models.insert(0, model)
            return text
        raise SourceUnavailable("gemini", "; ".join(errors) or "no models configured")

    async def probe(self, model: str) -> bool:
        """Tiny request: does this model answer right now?"""
        try:
            await self._call(model, {"contents": [{"role": "user", "parts": [{"text": 'Reply with JSON {"ok": true}'}]}],
                                     "generationConfig": {"responseMimeType": "application/json"}})  # fmt: skip
            return True
        except SourceUnavailable:
            return False

    async def list_models(self) -> list[str]:
        resp = await resilient_request(self.http, "GET", f"{self.BASE}/models", self.breaker, timeout_s=15,
                                       headers={"x-goog-api-key": self.api_key})  # fmt: skip
        if resp.status_code != 200:
            raise SourceUnavailable("gemini", f"HTTP {resp.status_code}: {resp.text[:200]}")
        return [m["name"].removeprefix("models/") for m in resp.json().get("models", [])
                if "generateContent" in m.get("supportedGenerationMethods", [])]
