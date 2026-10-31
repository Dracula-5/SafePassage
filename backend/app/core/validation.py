"""Text types for anything typed by a person on the internet.

That text reaches Postgres, PDFs, LLM prompts and the approver's screen. A NUL byte makes Postgres
raise, and bidi overrides can make a name read differently from what it is. Zero-width joiners are
left alone: Telugu and Hindi need them.
"""

from __future__ import annotations

import unicodedata
from typing import Annotated

from pydantic import AfterValidator

BIDI_CONTROLS = frozenset("‪‫‬‭‮⁦⁧⁨⁩")


def _no_controls(value: str, allowed: str = "") -> str:
    for char in value:
        if char in BIDI_CONTROLS or (unicodedata.category(char) == "Cc" and char not in allowed):
            raise ValueError("control characters are not allowed")
    return value


def _text(value: str) -> str:
    return _no_controls(value, allowed="\n\t")


def _name(value: str) -> str:
    _no_controls(value)
    if "<" in value or ">" in value:
        raise ValueError("< and > are not allowed in a name")
    if not value.strip():
        raise ValueError("must not be blank")
    return value.strip()


Text = Annotated[str, AfterValidator(_text)]  # free text: reasons, notes
Name = Annotated[str, AfterValidator(_name)]  # one line that labels something: people, vehicles, sites
