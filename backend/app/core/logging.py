"""Structured JSON logging via structlog."""

import logging
import re
import sys

import structlog

SECRETS = (
    (re.compile(r"(?i)\b([\w-]*(?:password|passwd|secret|token|api[_-]?key|map_key)[\w-]*['\"]?\s*[=:]\s*['\"]?)[^\s'\",&]+"), r"\1***"),
    (re.compile(r"://([^:/@\s]*):[^@/\s]+@"), r"://\1:***@"),  # a password inside a connection URL
    (re.compile(r"/api/area/csv/[^/\s]+/"), "/api/area/csv/***/"),  # FIRMS puts its key in the path
)


def scrub(text: str) -> str:
    """Errors end up in logs and in source_health.last_error, which a public endpoint returns."""
    for pattern, replacement in SECRETS:
        text = pattern.sub(replacement, text)
    return text


def _scrub_event(_logger: object, _method: str, event: dict) -> dict:
    return {k: scrub(v) if isinstance(v, str) else v for k, v in event.items()}


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())
    logging.getLogger("httpx").setLevel(logging.WARNING)  # at INFO it prints every URL, keys included
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _scrub_event,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
