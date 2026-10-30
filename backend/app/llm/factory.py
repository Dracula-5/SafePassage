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


def pick_gemini_model(available: list[str]) -> str | None:
    """Newest stable 'flash' model: fast and cheap enough for per-plan reasoning."""
    def version(name: str) -> float:
        m = re.search(r"gemini-(\d+(?:\.\d+)?)", name)
        return float(m.group(1)) if m else 0.0

    candidates = [m for m in available if "flash" in m and not any(x in m for x in EXCLUDE)]
    return max(candidates, key=lambda m: (version(m), -len(m)), default=None)


async def build_llm(settings: BrainSettings, http: httpx.AsyncClient) -> LLMClient | None:
    if settings.llm_provider != "gemini":
        raise ValueError(f"LLM provider {settings.llm_provider!r} is not implemented")
    if not settings.gemini_api_key:
        log.warning("llm_not_configured", provider="gemini")
        return None
    backend = GeminiBackend(settings.gemini_api_key, settings.llm_model or "", http)
    if not settings.llm_model:
        available = await backend.list_models()
        model = pick_gemini_model(available)
        if model is None:
            raise RuntimeError(f"no suitable Gemini flash model among {available}")
        backend.model = model
        log.info("llm_model_selected", model=model, from_available=len(available))
    return LLMClient(backend)
