"""Tests for SettingsService + setting validation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from roboco.db.tables import AgentRole, AgentStatus, AgentTable
from roboco.services.settings import (
    SettingValidationError,
    get_settings_service,
    validate_setting,
)


def _ceo_agent(name: str = "Renzo", created_at: datetime | None = None) -> AgentTable:
    """A minimal CEO-role agent row for write-through tests.

    `created_at` defaults to an ancient timestamp so this row is the
    earliest-created CEO regardless of rows other suite tests may have
    committed into the shared DB — the write-through targets the oldest.
    """
    return AgentTable(
        id=uuid4(),
        name=name,
        slug=f"ceo-{uuid4().hex[:6]}",
        role=AgentRole.CEO,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
        created_at=created_at or datetime(2000, 1, 1, tzinfo=UTC),
    )


def test_validate_setting_rejects_unknown_key() -> None:
    with pytest.raises(SettingValidationError):
        validate_setting("not_a_real_setting", "x")


def test_validate_retention_requires_positive_int() -> None:
    validate_setting("transcript_retention_days", "7")  # ok, no raise
    with pytest.raises(SettingValidationError):
        validate_setting("transcript_retention_days", "0")
    with pytest.raises(SettingValidationError):
        validate_setting("transcript_retention_days", "abc")


def test_validate_ceo_name_requires_nonempty_bounded_string() -> None:
    validate_setting("ceo_name", "Alice")  # ok, no raise
    with pytest.raises(SettingValidationError):
        validate_setting("ceo_name", "   ")
    with pytest.raises(SettingValidationError):
        validate_setting("ceo_name", "x" * 61)


_DEFAULT_RETENTION = 14
_NEW_RETENTION = 30


@pytest.mark.asyncio
async def test_get_int_returns_default_when_unset(db_session: Any) -> None:
    svc = get_settings_service(db_session)
    assert (
        await svc.get_int("transcript_retention_days", _DEFAULT_RETENTION)
        == _DEFAULT_RETENTION
    )


@pytest.mark.asyncio
async def test_set_then_get_roundtrips(db_session: Any) -> None:
    svc = get_settings_service(db_session)
    await svc.set("transcript_retention_days", str(_NEW_RETENTION))
    assert (
        await svc.get_int("transcript_retention_days", _DEFAULT_RETENTION)
        == _NEW_RETENTION
    )
    assert (await svc.all())["transcript_retention_days"] == str(_NEW_RETENTION)


@pytest.mark.asyncio
async def test_set_rejects_invalid_value(db_session: Any) -> None:
    svc = get_settings_service(db_session)
    with pytest.raises(SettingValidationError):
        await svc.set("transcript_retention_days", "-5")


@pytest.mark.asyncio
async def test_ceo_name_set_then_get_roundtrips(db_session: Any) -> None:
    svc = get_settings_service(db_session)
    assert await svc.get("ceo_name") is None  # unset, panel supplies fallback
    await svc.set("ceo_name", "Alice")
    assert await svc.get("ceo_name") == "Alice"


@pytest.mark.asyncio
async def test_ceo_name_set_rejects_blank_value(db_session: Any) -> None:
    svc = get_settings_service(db_session)
    with pytest.raises(SettingValidationError):
        await svc.set("ceo_name", "   ")


@pytest.mark.asyncio
async def test_ceo_name_set_writes_through_to_ceo_agent_row(db_session: Any) -> None:
    agent = _ceo_agent()
    db_session.add(agent)
    await db_session.flush()

    svc = get_settings_service(db_session)
    await svc.set("ceo_name", "Alice")

    await db_session.refresh(agent)
    assert agent.name == "Alice"


@pytest.mark.asyncio
async def test_ceo_name_set_strips_whitespace_on_agent_row(db_session: Any) -> None:
    agent = _ceo_agent()
    db_session.add(agent)
    await db_session.flush()

    svc = get_settings_service(db_session)
    await svc.set("ceo_name", "  Bob  ")

    await db_session.refresh(agent)
    assert agent.name == "Bob"


@pytest.mark.asyncio
async def test_ceo_name_set_with_no_ceo_agent_does_not_raise(
    db_session: Any,
) -> None:
    svc = get_settings_service(db_session)
    await svc.set("ceo_name", "Alice")  # no CEO row seeded — no-op, no crash
    assert await svc.get("ceo_name") == "Alice"


@pytest.mark.asyncio
async def test_ceo_name_set_tolerates_duplicate_ceo_rows(db_session: Any) -> None:
    """A second role=CEO row must not crash the write-through (no bare one_or_none)."""
    older = _ceo_agent("Renzo", created_at=datetime(2000, 1, 1, tzinfo=UTC))
    db_session.add(older)
    await db_session.flush()
    newer = _ceo_agent("Renzo", created_at=datetime(2000, 1, 2, tzinfo=UTC))
    db_session.add(newer)
    await db_session.flush()

    svc = get_settings_service(db_session)
    await svc.set("ceo_name", "Alice")  # must not raise MultipleResultsFound

    await db_session.refresh(older)
    await db_session.refresh(newer)
    assert older.name == "Alice"  # earliest-created wins
    assert newer.name == "Renzo"  # the duplicate is left untouched
