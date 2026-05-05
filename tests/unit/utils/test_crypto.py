"""utils.crypto coverage — Fernet encrypt/decrypt round-trip."""

from __future__ import annotations

import pytest
from roboco.utils.crypto import (
    EncryptionError,
    decrypt_token,
    encrypt_token,
    is_encryption_configured,
)


def test_encrypt_decrypt_round_trip() -> None:
    plaintext = "sk-secret-test-token-12345"
    encrypted = encrypt_token(plaintext)
    assert encrypted != plaintext
    decrypted = decrypt_token(encrypted)
    assert decrypted == plaintext


def test_encrypt_empty_string_raises() -> None:
    with pytest.raises(EncryptionError, match="empty"):
        encrypt_token("")


def test_decrypt_empty_string_raises() -> None:
    with pytest.raises(EncryptionError, match="empty"):
        decrypt_token("")


def test_decrypt_invalid_token_raises() -> None:
    with pytest.raises(EncryptionError):
        decrypt_token("not-a-valid-token-at-all")


def test_is_encryption_configured() -> None:
    """Encryption must be configured for tests to run."""
    assert is_encryption_configured() is True


def test_encrypt_long_string() -> None:
    plaintext = "a" * 1000
    encrypted = encrypt_token(plaintext)
    decrypted = decrypt_token(encrypted)
    assert decrypted == plaintext


def test_encrypt_unicode() -> None:
    plaintext = "héllo wörld 中文 🚀"
    encrypted = encrypt_token(plaintext)
    decrypted = decrypt_token(encrypted)
    assert decrypted == plaintext
