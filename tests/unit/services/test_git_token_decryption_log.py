"""Log a Fernet decryption failure with the project slug before returning None.

On a key rotation the stored PAT can't be decrypted; ``_token_for_project`` must
not mask that as a silent 'no token' — log the project slug so the cause is
diagnosable (best-effort skip behavior preserved).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.services.git import GitService
from roboco.utils.crypto import EncryptionError
from structlog.testing import capture_logs

_PROJECT_SLUG = "acme-backend"


def _svc() -> GitService:
    return GitService(MagicMock())


def _patch_project_service_raising(exc: Exception) -> Any:
    fake_service = MagicMock()
    fake_service.get_decrypted_token_by_slug = AsyncMock(side_effect=exc)
    return patch("roboco.services.git.get_project_service", return_value=fake_service)


@pytest.mark.asyncio
async def test_decryption_failure_logged_with_project_slug() -> None:
    """An EncryptionError (key rotation) is logged with the project slug so the
    operator can tell WHICH project is wedged — not silently masked as 'no
    token'."""
    svc = _svc()
    with (
        _patch_project_service_raising(
            EncryptionError("Unable to decrypt token - encryption key may have changed")
        ),
        capture_logs() as logs,
    ):
        result = await svc._token_for_project(_PROJECT_SLUG)

    # Best-effort behavior preserved: returns None (callers skip remote ops).
    assert result is None
    # ... but the failure is loud + project-scoped, not silent.
    error_logs = [e for e in logs if e["log_level"] == "error"]
    assert error_logs, "decryption failure must be logged at error level"
    log = error_logs[0]
    assert "decrypt" in log["event"].lower() or "token" in log["event"].lower()
    assert log.get("project_slug") == _PROJECT_SLUG or _PROJECT_SLUG in str(log)


@pytest.mark.asyncio
async def test_missing_token_returns_none_silently() -> None:
    """A project that genuinely has no token (None, no exception) returns None
    with NO error log — the fix must not over-log the legitimate no-token
    case (regression guard)."""
    svc = _svc()
    fake_service = MagicMock()
    fake_service.get_decrypted_token_by_slug = AsyncMock(return_value=None)
    with (
        patch("roboco.services.git.get_project_service", return_value=fake_service),
        capture_logs() as logs,
    ):
        result = await svc._token_for_project(_PROJECT_SLUG)

    assert result is None
    assert not [e for e in logs if e["log_level"] == "error"]
