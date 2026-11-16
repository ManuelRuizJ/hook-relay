"""API key generation and hashing.

Keys look like ``hr_<43 url-safe chars>``: 256 bits from ``secrets``. The
``hr_`` prefix makes a leaked key recognisable (secret scanners, logs).
"""

import hashlib
import secrets

KEY_PREFIX = "hr_"
# "hr_" plus 8 characters: enough to tell keys apart, useless to an attacker.
DISPLAY_PREFIX_LENGTH = len(KEY_PREFIX) + 8


def generate_api_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_api_key(key: str) -> str:
    """SHA-256 hex digest; the only form of the key that is stored."""
    return hashlib.sha256(key.encode()).hexdigest()


def display_prefix(key: str) -> str:
    return key[:DISPLAY_PREFIX_LENGTH]
