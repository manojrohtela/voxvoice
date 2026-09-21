"""Password hashing for console users (M12.2, security model §3).

scrypt from the standard library (`hashlib.scrypt`, OpenSSL-backed): a memory-hard KDF that
needs no new dependency. Parameters travel with every hash, so they can be raised later
without invalidating stored passwords.

    scrypt$<n>$<r>$<p>$<salt b64>$<key b64>
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_N, _R, _P = 2**14, 8, 1
_SALT_BYTES = 16
_KEY_BYTES = 64
_MAXMEM = 64 * 1024 * 1024

MIN_PASSWORD_LENGTH = 12
#: An upper bound so a request cannot make the server hash megabytes of input.
MAX_PASSWORD_LENGTH = 1024


def _derive(password: str, salt: bytes, n: int, r: int, p: int, length: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=_MAXMEM, dklen=length
    )


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    key = _derive(password, salt, _N, _R, _P, _KEY_BYTES)
    encode = base64.urlsafe_b64encode
    return f"scrypt${_N}${_R}${_P}${encode(salt).decode()}${encode(key).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time verification. A malformed stored hash verifies as False, never raises."""
    try:
        scheme, n, r, p, salt_b64, key_b64 = encoded.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_b64)
        expected = base64.urlsafe_b64decode(key_b64)
        actual = _derive(password, salt, int(n), int(r), int(p), len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


#: Verified against when an email is unknown, so an unknown email and a wrong password take
#: the same time and the same code path (no account enumeration by timing).
DUMMY_HASH = hash_password(secrets.token_urlsafe(24))
