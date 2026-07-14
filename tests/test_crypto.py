"""Envelope encryption unit tests."""

import pytest

from app.core.crypto import SecretCipher, fingerprint, hint
from app.domain.exceptions import DecryptionError

PLAINTEXT = "nvapi-abcdef0123456789abcdef"


def test_encrypt_decrypt_roundtrip():
    cipher = SecretCipher("master-secret")
    token = cipher.encrypt(PLAINTEXT)
    assert PLAINTEXT not in token
    assert token.startswith("enc$v1$")
    assert cipher.decrypt(token) == PLAINTEXT


def test_nonce_uniqueness():
    cipher = SecretCipher("master-secret")
    assert cipher.encrypt(PLAINTEXT) != cipher.encrypt(PLAINTEXT)


def test_wrong_master_key_fails():
    token = SecretCipher("master-a").encrypt(PLAINTEXT)
    with pytest.raises(DecryptionError):
        SecretCipher("master-b").decrypt(token)


def test_tampered_ciphertext_fails():
    cipher = SecretCipher("master-secret")
    token = cipher.encrypt(PLAINTEXT)
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    with pytest.raises(DecryptionError):
        cipher.decrypt(tampered)


def test_malformed_token_fails():
    with pytest.raises(DecryptionError):
        SecretCipher("master-secret").decrypt("not-a-token")


def test_fingerprint_deterministic():
    assert fingerprint(PLAINTEXT) == fingerprint(PLAINTEXT)
    assert fingerprint(PLAINTEXT) != fingerprint(PLAINTEXT + "x")


def test_hint_never_reveals_key():
    assert hint(PLAINTEXT) == f"…{PLAINTEXT[-4:]}"
    assert hint("short") == "…"
