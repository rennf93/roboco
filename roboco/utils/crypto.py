"""
Cryptographic utilities for encrypting sensitive data at rest.

Uses Fernet symmetric encryption with a master key from settings.
"""

from cryptography.fernet import Fernet, InvalidToken

from roboco.config import settings
from roboco.logging import get_logger

logger = get_logger(__name__)


class EncryptionError(Exception):
    """Raised when encryption/decryption operations fail."""

    pass


def _get_fernet() -> Fernet:
    """
    Get Fernet instance with master encryption key.

    Raises:
        EncryptionError: If encryption key is not configured
    """
    if not settings.encryption_key:
        raise EncryptionError(
            "ROBOCO_ENCRYPTION_KEY is not configured. "
            "Generate one with: python -c 'from cryptography.fernet "
            "import Fernet; print(Fernet.generate_key().decode())'"
        )
    try:
        return Fernet(settings.encryption_key.encode())
    except Exception as e:
        raise EncryptionError(f"Invalid encryption key format: {e}") from e


def encrypt_token(token: str) -> str:
    """
    Encrypt a token using Fernet symmetric encryption.

    Args:
        token: The plaintext token to encrypt

    Returns:
        Base64-encoded encrypted token string

    Raises:
        EncryptionError: If encryption fails
    """
    if not token:
        raise EncryptionError("Cannot encrypt empty token")

    try:
        fernet = _get_fernet()
        encrypted = fernet.encrypt(token.encode())
        return encrypted.decode()
    except EncryptionError:
        raise
    except Exception as e:
        logger.error("Token encryption failed", error=str(e))
        raise EncryptionError(f"Failed to encrypt token: {e}") from e


def decrypt_token(encrypted: str) -> str:
    """
    Decrypt a token that was encrypted with encrypt_token().

    Args:
        encrypted: Base64-encoded encrypted token string

    Returns:
        The original plaintext token

    Raises:
        EncryptionError: If decryption fails (wrong key, corrupted data, etc.)
    """
    if not encrypted:
        raise EncryptionError("Cannot decrypt empty value")

    try:
        fernet = _get_fernet()
        decrypted = fernet.decrypt(encrypted.encode())
        return decrypted.decode()
    except InvalidToken as e:
        logger.error(
            "Token decryption failed - encryption key may have changed",
            error="InvalidToken",
        )
        raise EncryptionError(
            "Unable to decrypt token - encryption key may have changed"
        ) from e
    except EncryptionError:
        raise
    except Exception as e:
        logger.error("Token decryption failed", error=str(e))
        raise EncryptionError(f"Failed to decrypt token: {e}") from e


def is_encryption_configured() -> bool:
    """Check if encryption is properly configured."""
    if not settings.encryption_key:
        return False
    try:
        _get_fernet()
        return True
    except EncryptionError:
        return False
