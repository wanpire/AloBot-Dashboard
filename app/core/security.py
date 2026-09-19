"""Password hashing, TOTP, and session tokens - stdlib only, no native
modules, so the image stays one `pip install` and the algorithms are the
ones the RFCs describe."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
_SALT_BYTES = 16
_KEY_LEN = 32


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    key = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=_KEY_LEN)
    return "scrypt$%d$%d$%d$%s$%s" % (
        SCRYPT_N,
        SCRYPT_R,
        SCRYPT_P,
        base64.b64encode(salt).decode(),
        base64.b64encode(key).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, key_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(key_b64)
        key = hashlib.scrypt(password.encode(), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected))
        return hmac.compare_digest(key, expected)
    except Exception:  # noqa: BLE001 - any malformed hash is simply "no"
        return False


# A dummy hash so a login for an unknown email costs the same time as a
# wrong password for a known one (no user enumeration by timing).
DUMMY_HASH = hash_password(secrets.token_hex(8))


TOTP_STEP_SECONDS = 30


def totp_code(secret: bytes, at: int, digits: int = 6, step: int = TOTP_STEP_SECONDS) -> str:
    counter = int(at) // step
    mac = hmac.new(secret, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF) % (10**digits)
    return str(code).zfill(digits)


def totp_verify(
    secret: bytes,
    code: str,
    at: int,
    last_step: int | None = None,
    window: int = 1,
    digits: int = 6,
) -> int | bool:
    """The step that matched, or False. A step at or before `last_step` never
    matches again: every code is single-use even inside the window."""
    code = code.strip().replace(" ", "")
    base = int(at) // TOTP_STEP_SECONDS
    for offset in range(-window, window + 1):
        step = base + offset
        if step < 0 or (last_step is not None and step <= last_step):
            continue
        expected = totp_code(secret, step * TOTP_STEP_SECONDS, digits=digits)
        if hmac.compare_digest(expected, code):
            return step
    return False


def new_totp_secret() -> bytes:
    return secrets.token_bytes(20)


def new_session_token() -> str:
    return secrets.token_hex(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
