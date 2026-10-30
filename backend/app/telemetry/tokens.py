"""Opaque device/supplier tokens. Only SHA-256 hashes are stored."""

import hashlib
import secrets


def new_token() -> tuple[str, str]:
    """(token to hand out once, hash to store)."""
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
