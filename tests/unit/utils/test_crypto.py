"""utils.crypto coverage — Fernet encrypt/decrypt round-trip + error paths."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from roboco.config import settings
from roboco.utils.crypto import (
    EncryptionError,
    _get_fernet,
    decrypt_token,
    encrypt_token,
    is_encryption_configured,
)


@pytest.fixture(autouse=True)
def _configured_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Supply a valid Fernet key for the tests that exercise real crypto.

    The round-trip tests need a configured key; without this they depend on
    ``ROBOCO_ENCRYPTION_KEY`` being set in the environment — it is not in agent
    gate containers, which is exactly why they failed there. The patch-based
    tests below replace ``settings`` wholesale inside their ``with`` blocks, so
    this autouse default never interferes with them.
    """
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())


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


# ---------------------------------------------------------------------------
# Fail paths — cover the guards in _get_fernet, encrypt_token, decrypt_token.
# ---------------------------------------------------------------------------


def test_get_fernet_raises_when_key_unset() -> None:
    """_get_fernet must fail-closed when settings.encryption_key is empty."""
    with (
        patch("roboco.utils.crypto.settings") as mock_settings,
        pytest.raises(EncryptionError, match="ROBOCO_ENCRYPTION_KEY"),
    ):
        mock_settings.encryption_key = ""
        _get_fernet()


def test_get_fernet_raises_on_invalid_key_format() -> None:
    """Bad key format → InvalidKey is wrapped in EncryptionError."""
    with (
        patch("roboco.utils.crypto.settings") as mock_settings,
        pytest.raises(EncryptionError, match="Invalid encryption key format"),
    ):
        mock_settings.encryption_key = "not-a-valid-fernet-key"
        _get_fernet()


class _FailingFernet:
    """Fernet stand-in whose encrypt/decrypt always raise RuntimeError."""

    def encrypt(self, _data: bytes) -> bytes:
        raise RuntimeError("boom")

    def decrypt(self, _data: bytes) -> bytes:
        raise RuntimeError("boom")


def test_encrypt_token_wraps_unexpected_error() -> None:
    """Mock Fernet.encrypt to raise — encrypt_token must wrap as EncryptionError."""
    with (
        patch("roboco.utils.crypto._get_fernet", return_value=_FailingFernet()),
        pytest.raises(EncryptionError, match="Failed to encrypt"),
    ):
        encrypt_token("anything")


def test_decrypt_token_wraps_unexpected_error() -> None:
    """Non-InvalidToken errors get wrapped as EncryptionError."""
    with (
        patch("roboco.utils.crypto._get_fernet", return_value=_FailingFernet()),
        pytest.raises(EncryptionError, match="Failed to decrypt"),
    ):
        decrypt_token("any-encrypted-value")


def test_encrypt_token_reraises_encryption_error_unchanged() -> None:
    """EncryptionError from _get_fernet bubbles up without re-wrapping."""
    err = EncryptionError("inner")
    with (
        patch("roboco.utils.crypto._get_fernet", side_effect=err),
        pytest.raises(EncryptionError, match="inner"),
    ):
        encrypt_token("anything")


def test_decrypt_token_reraises_encryption_error_unchanged() -> None:
    """EncryptionError from _get_fernet bubbles up unchanged from decrypt."""
    err = EncryptionError("inner")
    with (
        patch("roboco.utils.crypto._get_fernet", side_effect=err),
        pytest.raises(EncryptionError, match="inner"),
    ):
        decrypt_token("any-encrypted-value")


def test_is_encryption_configured_returns_false_on_empty_key() -> None:
    with patch("roboco.utils.crypto.settings") as mock_settings:
        mock_settings.encryption_key = ""
        assert is_encryption_configured() is False


def test_is_encryption_configured_returns_false_on_bad_key() -> None:
    with patch("roboco.utils.crypto.settings") as mock_settings:
        mock_settings.encryption_key = "not-a-valid-fernet-key"
        assert is_encryption_configured() is False
