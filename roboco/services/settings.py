"""System settings service — runtime-editable, panel-tunable config.

Reads/writes the ``system_settings`` key-value table. Values are stored as text
and parsed by typed accessors. Code defaults in ``roboco.config`` are the
fallback used when a key has no row yet. Only keys in ``KNOWN_SETTINGS`` are
writable, each with a validator, so the panel can't persist junk.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from roboco.db.tables import SystemSettingTable
from roboco.services.base import BaseService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class SettingValidationError(ValueError):
    """Raised when a setting key is unknown or its value is invalid."""


def _validate_retention_days(value: str) -> None:
    try:
        days = int(value)
    except ValueError as exc:
        raise SettingValidationError(
            "transcript_retention_days must be an integer"
        ) from exc
    if days < 1:
        raise SettingValidationError("transcript_retention_days must be >= 1")


# Writable settings: key -> validator. Keys absent here are rejected on write so
# the panel can only persist values the backend understands.
_VALIDATORS = {
    "transcript_retention_days": _validate_retention_days,
}


def validate_setting(key: str, value: str) -> None:
    """Raise SettingValidationError if ``key`` is not writable or ``value`` invalid."""
    validator = _VALIDATORS.get(key)
    if validator is None:
        raise SettingValidationError(f"Unknown or read-only setting: {key}")
    validator(value)


class SettingsService(BaseService):
    """CRUD for the ``system_settings`` key-value store."""

    async def get(self, key: str) -> str | None:
        """Return the stored value for ``key``, or None if unset."""
        result = await self.session.execute(
            select(SystemSettingTable.value).where(SystemSettingTable.key == key)
        )
        return result.scalar_one_or_none()

    async def get_int(self, key: str, default: int) -> int:
        """Return ``key`` parsed as int, or ``default`` if unset/unparseable."""
        raw = await self.get(key)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    async def set(self, key: str, value: str) -> None:
        """Validate then upsert ``key`` = ``value``. Caller commits."""
        validate_setting(key, value)
        existing = await self.session.get(SystemSettingTable, key)
        if existing is None:
            self.session.add(SystemSettingTable(key=key, value=value))
        else:
            existing.value = value
        await self.session.flush()

    async def all(self) -> dict[str, str]:
        """Return every stored setting as a ``{key: value}`` map."""
        result = await self.session.execute(select(SystemSettingTable))
        return {row.key: row.value for row in result.scalars().all()}


def get_settings_service(session: AsyncSession) -> SettingsService:
    """Construct a SettingsService bound to ``session``."""
    return SettingsService(session)
