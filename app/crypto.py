"""Hashing of the Apple Health webhook secret.

The secret only has to be *verified*, so the database stores ``sha256:<hex>``
and the plaintext is shown to the user exactly once. Values without the prefix
are legacy plaintext from before migration 014 and are still accepted.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Optional

HASH_PREFIX = "sha256:"


def hash_secret(value: str) -> str:
    return HASH_PREFIX + hashlib.sha256(value.encode()).hexdigest()


def verify_secret(provided: Optional[str], stored: Optional[str]) -> bool:
    """Constant-time check of a provided secret against a hashed/legacy value."""
    if not provided or not stored:
        return False
    if stored.startswith(HASH_PREFIX):
        return hmac.compare_digest(hash_secret(provided), stored)
    return hmac.compare_digest(provided, stored)
