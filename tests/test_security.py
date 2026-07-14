"""Password hashing and JWT unit tests."""

import pytest

from app.core.security import (
    create_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.domain.exceptions import InvalidTokenError


def test_password_hash_and_verify():
    hashed = hash_password("s3cret-password")
    assert hashed != "s3cret-password"
    assert verify_password("s3cret-password", hashed)
    assert not verify_password("wrong", hashed)


def test_token_roundtrip():
    token = create_token("user-1", "admin", "access")
    payload = decode_token(token)
    assert payload["sub"] == "user-1"
    assert payload["role"] == "admin"


def test_wrong_token_type_rejected():
    refresh = create_token("user-1", "admin", "refresh")
    with pytest.raises(InvalidTokenError):
        decode_token(refresh, expected_type="access")


def test_garbage_token_rejected():
    with pytest.raises(InvalidTokenError):
        decode_token("not.a.jwt")
