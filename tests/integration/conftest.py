"""Integration-test fixtures.

Supplements the top-level conftest with integration-level concerns:

* A session-scoped autouse fixture that seeds ``settings.encryption_key``
  with a fresh Fernet key so any test that exercises encrypt_token /
  decrypt_token (project tokens, provider tokens, LLM routing) works
  without needing ROBOCO_ENCRYPTION_KEY in the environment.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from cryptography.fernet import Fernet
from roboco.config import settings

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(scope="session", autouse=True)
def configure_test_encryption_key() -> Iterator[None]:
    """Seed a valid Fernet key so encryption works in integration tests.

    Restores the original (empty) value on teardown so it cannot leak into
    any non-integration test that shares the process.
    """
    original = settings.encryption_key
    settings.encryption_key = Fernet.generate_key().decode()
    yield
    settings.encryption_key = original
