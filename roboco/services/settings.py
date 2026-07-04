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


def _validate_bool(value: str) -> None:
    if value.strip().lower() not in ("true", "false"):
        raise SettingValidationError("value must be 'true' or 'false'")


# Panel-tunable feature flags (master switches). The stored value overrides the
# config/env default at startup via ``apply_persisted_feature_flags`` — i.e. a
# toggle takes effect on the next restart, replacing hand-editing env. Each maps
# to a ``roboco.config.Settings`` bool attribute of the same name.
FEATURE_FLAGS: tuple[tuple[str, str], ...] = (
    ("external_pr_enabled", "External-PR review"),
    ("internal_pr_enabled", "Internal-PR safety reviewer"),
    ("research_enabled", "Web research (Board + PM)"),
    ("strategy_engine_enabled", "Strategy engine"),
    ("self_heal_enabled", "Self-healing (detect + notify)"),
    ("self_heal_originate_enabled", "Self-healing — open fix tasks"),
    ("provisioning_enabled", "Pitch auto-provisioning"),
    ("toolchain_match_enabled", "Agent runtime toolchain matching"),
    ("conventions_enabled", "Architectural conventions standard"),
    ("rag_auto_update_enabled", "RAG auto-update"),
    ("transcript_prune_enabled", "Transcript pruning"),
    ("gateway_health_enabled", "Gateway-health recovery"),
    ("ci_watch_enabled", "Multi-repo CI-watch"),
    ("dep_update_enabled", "Dependency-update bot"),
    ("release_manager_enabled", "Gated release manager"),
    ("org_memory_enabled", "Organizational memory loop"),
    ("sandbox_db_enabled", "Sandboxed per-agent test DB/Redis"),
    ("routing_strict", "Strict model routing (fail-closed on a disabled provider)"),
    ("x_engine_enabled", "X (Twitter) engine"),
    ("x_replies_enabled", "X mention replies (needs a paid X API tier)"),
    ("x_feature_spotlight_enabled", "X feature-spotlight marketing"),
    ("roadmap_engine_enabled", "Board roadmap engine"),
    ("fable_mode_enabled", "Fable-mode doctrine + hooks"),
)
_FEATURE_FLAG_KEYS = tuple(key for key, _ in FEATURE_FLAGS)


# Writable settings: key -> validator. Keys absent here are rejected on write so
# the panel can only persist values the backend understands.
_VALIDATORS = {
    "transcript_retention_days": _validate_retention_days,
    **dict.fromkeys(_FEATURE_FLAG_KEYS, _validate_bool),
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

    async def get_bool(self, key: str, default: bool) -> bool:
        """Return ``key`` parsed as a bool ('true'/'false'), or ``default``."""
        raw = await self.get(key)
        if raw is None:
            return default
        return raw.strip().lower() == "true"

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


async def feature_flag_effective_values(session: AsyncSession) -> dict[str, bool]:
    """Effective value of each panel-tunable flag: stored override, else env default.

    Backs the Settings panel's feature-flag card so it shows what's actually in
    force (the env/config default unless the panel has persisted an override).
    """
    from roboco.config import settings as _settings

    service = get_settings_service(session)
    return {
        key: await service.get_bool(key, bool(getattr(_settings, key, False)))
        for key in _FEATURE_FLAG_KEYS
    }


async def apply_persisted_feature_flags(session: AsyncSession) -> list[str]:
    """Overlay panel-persisted feature-flag overrides onto the live config.

    Called once at startup, after the DB is ready: for each known flag with a
    stored value, set the matching attribute on the ``roboco.config.settings``
    singleton so the rest of the app reads the panel's choice. No per-consumer
    re-routing — a toggle simply takes effect on the next restart. Returns the
    keys that were overridden.
    """
    from roboco.config import settings as _settings

    service = get_settings_service(session)
    applied: list[str] = []
    for key in _FEATURE_FLAG_KEYS:
        raw = await service.get(key)
        if raw is None:
            continue
        setattr(_settings, key, raw.strip().lower() == "true")
        applied.append(key)
    return applied
