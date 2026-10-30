"""Test doubles (tests only; runtime code never imports this)."""

from __future__ import annotations

import json


class ScriptedBackend:
    """Returns queued replies in order; records what it was asked."""

    model = "scripted-test-model"

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls: list[tuple[str, list[tuple[str, str]]]] = []

    async def complete(self, system: str, turns: list[tuple[str, str]]) -> str:
        self.calls.append((system, list(turns)))
        return self.replies.pop(0)


class SchemaAwareBackend:
    """Answers by schema (detected from the system prompt) so a whole graph run can be scripted.

    `translate` maps the English text to a fake 'translation' that keeps the numbers."""

    model = "schema-aware-test-model"

    def __init__(self, malformed_first: bool = False):
        self.malformed_first = malformed_first
        self.calls: list[str] = []

    async def complete(self, system: str, turns: list[tuple[str, str]]) -> str:
        last = turns[-1][1]
        if self.malformed_first and len(turns) == 1 and "Sentinel" in system:
            self.calls.append("sentinel-malformed")
            return '{"priority": "urgent!!", "rationale": "short"}'
        if "Sentinel" in system:
            self.calls.append("sentinel")
            return json.dumps({"priority": "high", "planning_horizon_hours": 24,
                               "rationale": "Severe flood drill near populated settlements; plan one day of water and food.",
                               "key_risks": ["drill hazard"], "recommend_human_review": True})
        if "duty officer" in system:
            self.calls.append("brief")
            return json.dumps({"summary": "Deliver water and rice from the registered supplier to relief points.",
                               "risks": ["unverified supplier"], "approval_notes": "Check supplier verification."})
        if "Translate" in system:
            self.calls.append("translate")
            return json.dumps({"text": "[te] " + turns[0][1]})
        raise AssertionError(f"unexpected prompt: {system[:80]} / {last[:80]}")
