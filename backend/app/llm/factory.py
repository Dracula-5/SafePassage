"""Build the configured LLM client, or None when no provider key is set (agents then use their
labelled rule-based fallbacks)."""

from __future__ import annotations

import re

import httpx

from app.core.logging import get_logger
from app.core.settings_brain import BrainSettings

from .client import GeminiBackend, LLMClient

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
    ranked = rank_gemini_models(await backend.list_models())
    if settings.llm_model:
        ranked = [settings.llm_model] + [m for m in ranked if m != settings.llm_model]
    # Order by what answers now; keep the rest as fallbacks for later outages.
    healthy = [m for m in ranked[:6] if await backend.probe(m)]
    backend.models = healthy + [m for m in ranked if m not in healthy]
    if not backend.models:
        raise RuntimeError("no Gemini flash model available for this key")
    log.info("llm_models_ranked", healthy=healthy, fallback=backend.models[len(healthy):][:5])
    return LLMClient(backend)
