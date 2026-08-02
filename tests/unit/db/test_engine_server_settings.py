"""Engine-level Postgres session timeouts (2026-07-29 pool-exhaustion class).

A session parked mid-transaction on non-DB work (a git subprocess, an
asyncio lock queue) holds its row locks and pooled connection until Postgres
kills it; a statement queued on someone else's row lock must give up instead
of camping on a pool slot. ``get_engine`` passes both as asyncpg
``server_settings`` so every app connection carries them; a 0 value drops
the setting entirely (Postgres-default behavior, operator off-switch).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from roboco.db import base as db_base


@pytest.fixture
def _holder_reset(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Isolate _DbHolder so the test never touches the real engine."""
    monkeypatch.setattr(db_base._DbHolder, "engine", None)
    monkeypatch.setattr(db_base._DbHolder, "session_factory", None)
    monkeypatch.setattr(db_base._DbHolder, "loop", None)
    return db_base._DbHolder


def _capture_engine_kwargs(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def _fake_create(_url: str, **kwargs: Any) -> MagicMock:
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(db_base, "create_async_engine", _fake_create)
    return captured


def test_engine_carries_server_side_timeouts(
    monkeypatch: pytest.MonkeyPatch, _holder_reset: Any
) -> None:
    monkeypatch.setattr(
        db_base.settings, "database_idle_in_transaction_timeout_ms", 120_000
    )
    monkeypatch.setattr(db_base.settings, "database_lock_timeout_ms", 30_000)
    captured = _capture_engine_kwargs(monkeypatch)

    db_base.get_engine()

    assert captured["connect_args"]["server_settings"] == {
        "idle_in_transaction_session_timeout": "120000",
        "lock_timeout": "30000",
    }


def test_zero_disables_each_timeout_individually(
    monkeypatch: pytest.MonkeyPatch, _holder_reset: Any
) -> None:
    monkeypatch.setattr(db_base.settings, "database_idle_in_transaction_timeout_ms", 0)
    monkeypatch.setattr(db_base.settings, "database_lock_timeout_ms", 5_000)
    captured = _capture_engine_kwargs(monkeypatch)

    db_base.get_engine()

    assert captured["connect_args"]["server_settings"] == {"lock_timeout": "5000"}


def test_both_zero_sends_no_server_settings(
    monkeypatch: pytest.MonkeyPatch, _holder_reset: Any
) -> None:
    monkeypatch.setattr(db_base.settings, "database_idle_in_transaction_timeout_ms", 0)
    monkeypatch.setattr(db_base.settings, "database_lock_timeout_ms", 0)
    captured = _capture_engine_kwargs(monkeypatch)

    db_base.get_engine()

    assert captured["connect_args"] == {}
