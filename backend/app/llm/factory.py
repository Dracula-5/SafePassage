"""Build the configured LLM client, or None when no provider key is set (agents then use their
labelled rule-based fallbacks)."""

from __future__ import annotations

import asyncio
import re

import httpx

from app.core.logging import get_logger
from app.core.settings_brain import BrainSettings

from app.core.resilience import SourceUnavailable

from .client import GeminiBackend, LLMClient
from .tracing import LangfuseTracer

log = get_logger(__name__)
EXCLUDE = ("lite", "image", "tts", "audio", "live", "embedding", "thinking", "exp", "preview", "learnlm", "gemma")


def _version(name: str) -> float:
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", name)
    return float(m.group(1)) if m else 0.0


def rank_gemini_models(available: list[str]) -> list[str]:
    """Flash models for per-plan reasoning, best first: stable before preview, newest first, lite last."""
    flash = [m for m in available if "flash" in m and not any(x in m for x in EXCLUDE)]
    lite = [m for m in available if "flash-lite" in m and not any(x in m for x in EXCLUDE if x != "lite")]
    preview = [m for m in available if "flash" in m and "preview" in m
               and not any(x in m for x in EXCLUDE if x not in ("preview",))]  # fmt: skip
    ordered = sorted(flash, key=lambda m: (-_version(m), len(m))) + sorted(preview, key=lambda m: -_version(m)) \
        + sorted(lite, key=lambda m: -_version(m))
    return list(dict.fromkeys(ordered))


def pick_gemini_model(available: list[str]) -> str | None:
    ranked = rank_gemini_models(available)
    return ranked[0] if ranked else None


async def build_llm(settings: BrainSettings, http: httpx.AsyncClient) -> LLMClient | None:
    if settings.llm_provider != "gemini":
        raise ValueError(f"LLM provider {settings.llm_provider!r} is not implemented")
    if not settings.gemini_api_key:
        log.warning("llm_not_configured", provider="gemini")
        return None
    backend = GeminiBackend(settings.gemini_api_key, [], http)
    try:
        ranked = rank_gemini_models(await backend.list_models())
    except SourceUnavailable as exc:
        # A Gemini outage at startup must not stop the worker: plans use the labelled fallbacks.
        log.warning("llm_unavailable_at_startup", reason=exc.reason)
        if not settings.llm_model:
            return None
        ranked = []
    if settings.llm_model:
        ranked = [settings.llm_model] + [m for m in ranked if m != settings.llm_model]
    # Order by what answers now; keep the rest as fallbacks for later outages.
    candidates = ranked[:6]
    backend.timeout_s = 15  # probes only; real calls get the full budget below
    answers = await asyncio.gather(*(backend.probe(m) for m in candidates))
    backend.timeout_s = 30
    healthy = [m for m, ok in zip(candidates, answers, strict=True) if ok]
    backend.models = healthy + [m for m in ranked if m not in healthy]
    if not backend.models:
        log.warning("llm_no_flash_model", provider="gemini")
        return None
    log.info("llm_models_ranked", healthy=healthy, fallback=backend.models[len(healthy):][:5])
    tracer = None
    if settings.langfuse_public_key and settings.langfuse_secret_key:
        tracer = LangfuseTracer(settings.langfuse_public_key, settings.langfuse_secret_key, settings.langfuse_host, http)
    return LLMClient(backend, tracer=tracer)
