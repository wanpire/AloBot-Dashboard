"""AloBot's order and invoice codes, so the dashboard can search by the code a
customer reads off their receipt. The alphabet is AloBot's
(`app/services/order_codes.py`); a test pins it against the linked source."""

from __future__ import annotations

from sqids import Sqids

ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz"
_sqids = Sqids(alphabet=ALPHABET, min_length=6)


def encode_id(value: int) -> str:
    return _sqids.encode([value])


def decode_id(code: str) -> int | None:
    if not code or any(ch not in ALPHABET for ch in code):
        return None
    decoded = _sqids.decode(code)
    if len(decoded) != 1:
        return None
    return decoded[0]
