"""Phase 1 task 3, the pure half: password hashing, TOTP, session tokens."""

import pytest

from app.core.security import (
    hash_password,
    hash_token,
    new_session_token,
    totp_code,
    totp_verify,
    verify_password,
)


def test_password_hash_is_salted_and_verifies():
    a = hash_password("correct horse")
    b = hash_password("correct horse")
    assert a != b
    assert verify_password("correct horse", a)
    assert verify_password("correct horse", b)
    assert not verify_password("wrong", a)


def test_password_hash_is_scrypt_with_parameters_in_the_string():
    assert hash_password("x").startswith("scrypt$")


def test_verify_never_raises_on_garbage_hash():
    assert verify_password("x", "not-a-hash") is False
    assert verify_password("x", "") is False


# RFC 6238 Appendix B, SHA-1, secret "12345678901234567890", 8 digits.
RFC_VECTORS = [
    (59, "94287082"),
    (1111111109, "07081804"),
    (1111111111, "14050471"),
    (1234567890, "89005924"),
    (2000000000, "69279037"),
    (20000000000, "65353130"),
]
RFC_SECRET = b"12345678901234567890"


@pytest.mark.parametrize(("at", "expected"), RFC_VECTORS)
def test_totp_matches_the_rfc_vectors(at, expected):
    assert totp_code(RFC_SECRET, at, digits=8) == expected
    assert totp_code(RFC_SECRET, at, digits=6) == expected[-6:]


def test_totp_verify_accepts_current_and_adjacent_step_only():
    assert totp_verify(RFC_SECRET, "287082", at=59)
    assert totp_verify(RFC_SECRET, "287082", at=59 + 30)  # one step late
    assert totp_verify(RFC_SECRET, "287082", at=59 - 30)  # one step early
    assert not totp_verify(RFC_SECRET, "287082", at=59 + 61)
    assert not totp_verify(RFC_SECRET, "000000", at=59)


def test_totp_verify_returns_the_step_used_so_a_code_can_be_burned():
    assert totp_verify(RFC_SECRET, "287082", at=59) == 1
    assert totp_verify(RFC_SECRET, "287082", at=59, last_step=1) is False


def test_session_tokens_are_random_and_hashed_for_storage():
    t1, t2 = new_session_token(), new_session_token()
    assert t1 != t2 and len(t1) == 64
    assert hash_token(t1) != t1 and len(hash_token(t1)) == 64
    assert hash_token(t1) == hash_token(t1)
