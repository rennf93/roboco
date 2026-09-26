"""System settings service — runtime-editable, panel-tunable config.

Reads/writes the ``system_settings`` key-value table. Values are stored as text
and parsed by typed accessors. Code defaults in ``roboco.config`` are the
fallback used when a key has no row yet. Only keys in ``KNOWN_SETTINGS`` are
writable, each with a validator, so the panel can't persist junk.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from sqlalchemy import select

from roboco.db.tables import AgentRole, SystemSettingTable
from roboco.foundation.policy.maintenance_pause import (
    PauseScope,
    validate_pause_payload,
)
from roboco.services.base import BaseService
from roboco.services.repositories.query_helpers import get_agent_by_role

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


def _validate_pilot_mode(value: str) -> None:
    """``decisions.pilot.{slug}`` stores a tri-state: off | shadow | on."""
    if value.strip().lower() not in ("off", "shadow", "on"):
        raise SettingValidationError("value must be 'off', 'shadow', or 'on'")


def _validate_maintenance_pause(value: str) -> None:
    """``maintenance_pause.{scope}`` stores a JSON payload (who/when/why/
    expiry), not a bare bool; shape enforced by the pure foundation
    validator so the two never drift."""
    try:
        payload = json.loads(value)
    except (json.JSONDecodeError, TypeError) as exc:
        raise SettingValidationError("value must be valid JSON") from exc
    try:
        validate_pause_payload(payload)
    except ValueError as exc:
        raise SettingValidationError(str(exc)) from exc


_CEO_NAME_MAX_LEN = 60


def _validate_ceo_name(value: str) -> None:
    if not value.strip():
        raise SettingValidationError("ceo_name must not be empty")
    if len(value.strip()) > _CEO_NAME_MAX_LEN:
        raise SettingValidationError(
            f"ceo_name must be at most {_CEO_NAME_MAX_LEN} characters"
        )


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
    (
        "possibilities_matrix_enabled",
        "Possibilities matrix (work-already-done fast path)",
    ),
    ("task_budgets_enabled", "Task/project cost budgets"),
    ("devops_enabled", "DevOps agent (floating infra role)"),
    ("decisions_enabled", "Decisions service (typed System One verdicts)"),
    ("decisions_tier_laya_enabled", "Decisions: self-hosted sidecar tier"),
    ("decisions_tier_openrouter_enabled", "Decisions: OpenRouter fallback tier"),
    ("rag_auto_update_enabled", "RAG auto-update"),
    ("transcript_prune_enabled", "Transcript pruning"),
    ("gateway_health_enabled", "Gateway-health recovery"),
    ("ci_watch_enabled", "Multi-repo CI-watch"),
    ("dep_update_enabled", "Dependency-update bot"),
    ("env_sync_enabled", "Environment-branch sync (cascade prod→dev)"),
    ("docs_sync_enabled", "Docs-divergence sync (release -> docs-update task)"),
    ("release_manager_enabled", "Gated release manager"),
    ("org_memory_enabled", "Organizational memory loop"),
    ("sandbox_db_enabled", "Sandboxed per-agent test DB/Redis"),
    (
        "token_factory_sandboxes_enabled",
        "Token Factory Sandboxes (dev/QA test runs in Nebius microVMs)",
    ),
    ("routing_strict", "Strict model routing (fail-closed on a disabled provider)"),
    ("x_engine_enabled", "X (Twitter) engine"),
    ("x_replies_enabled", "X mention replies (needs a paid X API tier)"),
    ("x_feature_spotlight_enabled", "X feature-spotlight marketing"),
    ("video_engine_enabled", "Video generation engine"),
    ("video_on_release", "Video on release"),
    ("video_on_spotlight", "Video on feature spotlight"),
    ("roadmap_engine_enabled", "Board roadmap engine"),
    ("fable_mode_enabled", "Fable + Ponytail doctrine (+ hooks)"),
    ("obsidian_vault_enabled", "Obsidian vault projection"),
    ("vault_intake_enabled", "Vault intake watcher (notes -> held drafts)"),
    ("vault_report_enabled", "Vault weekly org-report note"),
    ("vault_kb_enabled", "Vault KB ingest (CEO notes -> RAG)"),
    ("telegram_enabled", "Telegram notifications bridge (CEO DMs)"),
    ("telegram_inbound_enabled", "Telegram inbound commands + actionable buttons"),
)
_FEATURE_FLAG_KEYS = tuple(key for key, _ in FEATURE_FLAGS)


def _validate_update_id(value: str) -> None:
    try:
        update_id = int(value)
    except ValueError as exc:
        raise SettingValidationError(
            "telegram_last_update_id must be an integer"
        ) from exc
    if update_id < 0:
        raise SettingValidationError("telegram_last_update_id must be >= 0")


# Writable settings: key -> validator. Keys absent here are rejected on write so
# the panel can only persist values the backend understands.
_VALIDATORS = {
    "transcript_retention_days": _validate_retention_days,
    # The CEO's panel display name (header chip + Settings User Info card).
    # An unset key just means the panel's own hardcoded "Renzo" default
    # renders, same as transcript retention's client-side DEFAULT_RETENTION
    # fallback. When set, `SettingsService.set` also writes through to the
    # CEO agent row's `name` so GET /api/agents (kanban/assignee pickers)
    # reflects the same name instead of the seeded literal.
    "ceo_name": _validate_ceo_name,
    # Telegram inbound's getUpdates offset cursor. Not a feature flag (absent
    # from FEATURE_FLAGS/the panel card) but reuses this same validated KV
    # store instead of a dedicated table, so a restart doesn't replay updates.
    "telegram_last_update_id": _validate_update_id,
    # One-time-nudge marker (XEngine._maybe_nudge_brand_voice). Not a feature
    # flag (absent from FEATURE_FLAGS/the panel card) but reuses this same KV
    # store instead of a dedicated table or a restart-losing in-memory flag.
    "x_brand_voice_nudge_sent": _validate_bool,
    # Board Program per-program enablement (BoardProgramEngine.enabled).
    # Dotted, not in FEATURE_FLAGS (no roboco.config attribute to fall back
    # to) — an unset key falls back to the migrated legacy flag instead
    # (roadmap_engine_enabled / x_engine_enabled+x_feature_spotlight_enabled).
    "board_program.roadmap.enabled": _validate_bool,
    "board_program.x_feature.enabled": _validate_bool,
    "board_program.pest_control.enabled": _validate_bool,
    "board_program.periscope.enabled": _validate_bool,
    "board_program.coroner.enabled": _validate_bool,
    "board_program.sentinel.enabled": _validate_bool,
    "board_program.spackle.enabled": _validate_bool,
    "board_program.scales.enabled": _validate_bool,
    "board_program.mirror.enabled": _validate_bool,
    "board_program.megaphone.enabled": _validate_bool,
    "board_program.librarian.enabled": _validate_bool,
    "board_program.war_room.enabled": _validate_bool,
    "board_program.barfly.enabled": _validate_bool,
    "board_program.dogfood.enabled": _validate_bool,
    "board_program.decisions_audit.enabled": _validate_bool,
    # Decisions service per-pilot mode (roboco.services.decisions). Dotted
    # tri-state rows (off | shadow | on), not FEATURE_FLAGS (no roboco.config
    # bool to fall back to): an unset key means off, which is exactly the
    # pre-Decisions behavior for that pilot. The per-pilot chokepoint
    # (pilots.pilot_mode) resolves these; keep the slug list in sync with
    # the slugs passed to decide_for_pilot across all four pilot modules
    # (roboco/services/decisions/pilots{,_infra,_content,_dispatch,_gateway}.py;
    # note board_rotation_target shares the board_due_early row by design).
    **dict.fromkeys(
        (
            # Tier A (spec 6) + cognition-lane built pilots.
            "decisions.pilot.self_heal",
            "decisions.pilot.parking",
            "decisions.pilot.complexity",
            "decisions.pilot.preflight_diff",
            "decisions.pilot.triage_failure",
            "decisions.pilot.steer_gate",
            "decisions.pilot.transcript_notes",
            "decisions.pilot.tool_spotlight",
            # Tier B second wave (spec 7.1), built default-off by CEO pick;
            # arming awaits shadow data. Row number -> slug map:
            # B1 plan_quality, B2 injection_screen, B3 intake_preroute,
            # B4 x_mention_triage, B5 collision_edge, B6 ci_watch_route,
            # B7 heal_severity, B8 release_worthy, B9 second_review_eligibility,
            # B10 segment_classify, B11 vault_prefilter, B12 notify_dedup,
            # B13 board_due_early, B14 external_pr_triage, B15 stranded_response,
            # B16 coroner_gate, B17 dep_update_risk, B18 secretary_nl,
            # B19 release_readiness, B20 commit_intent, B21 tg_freetext_gate,
            # B22 memory_distill_gate, B23 changelog_highlights,
            # B24 proactive_domain, B25 idle_reaping, B26 decision_note_sufficiency,
            # B29 pre_send_completeness, B30 context_pruning, B31 findings_mapping,
            # B32 budget_wrapup, B33 delta_brief, B34 idle_legitimacy,
            # B35 review_queue_priority, B36 board_evidence_skip, B37 respawn_verdict,
            # B38 submit_now_confidence, B39 branch_staleness, B40 park_cause,
            # B41 lesson_prune, B42 pm_closure_confidence, B43 assembled_coherence,
            # B44 silent_exit.
            "decisions.pilot.plan_quality",
            "decisions.pilot.injection_screen",
            "decisions.pilot.intake_preroute",
            "decisions.pilot.x_mention_triage",
            "decisions.pilot.collision_edge",
            "decisions.pilot.ci_watch_route",
            "decisions.pilot.heal_severity",
            "decisions.pilot.release_worthy",
            "decisions.pilot.second_review_eligibility",
            "decisions.pilot.segment_classify",
            "decisions.pilot.vault_prefilter",
            "decisions.pilot.notify_dedup",
            "decisions.pilot.board_due_early",
            "decisions.pilot.external_pr_triage",
            "decisions.pilot.stranded_response",
            "decisions.pilot.coroner_gate",
            "decisions.pilot.dep_update_risk",
            "decisions.pilot.secretary_nl",
            "decisions.pilot.release_readiness",
            "decisions.pilot.commit_intent",
            "decisions.pilot.tg_freetext_gate",
            "decisions.pilot.memory_distill_gate",
            "decisions.pilot.changelog_highlights",
            "decisions.pilot.proactive_domain",
            "decisions.pilot.idle_reaping",
            "decisions.pilot.decision_note_sufficiency",
            "decisions.pilot.pre_send_completeness",
            "decisions.pilot.context_pruning",
            "decisions.pilot.findings_mapping",
            "decisions.pilot.budget_wrapup",
            "decisions.pilot.delta_brief",
            "decisions.pilot.idle_legitimacy",
            "decisions.pilot.review_queue_priority",
            "decisions.pilot.board_evidence_skip",
            "decisions.pilot.respawn_verdict",
            "decisions.pilot.submit_now_confidence",
            "decisions.pilot.branch_staleness",
            "decisions.pilot.park_cause",
            "decisions.pilot.lesson_prune",
            "decisions.pilot.pm_closure_confidence",
            "decisions.pilot.assembled_coherence",
            "decisions.pilot.silent_exit",
        ),
        _validate_pilot_mode,
    ),
    **dict.fromkeys(_FEATURE_FLAG_KEYS, _validate_bool),
    # Operator maintenance pause: one JSON payload per scope (see
    # roboco.services.maintenance_pause). Not a feature flag: it has no
    # roboco.config attribute, is written by its own CEO-only route, and
    # self-expires rather than persisting across a restart forever.
    **dict.fromkeys(
        (f"maintenance_pause.{s.value}" for s in PauseScope),
        _validate_maintenance_pause,
    ),
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
        if key == "ceo_name":
            await self._sync_ceo_agent_name(value.strip())
        await self.session.flush()

    async def _sync_ceo_agent_name(self, name: str) -> None:
        """Write-through: keep the CEO agent row's `name` in sync with the setting.

        Same-transaction as the setting write so the two never disagree.
        Resolution rides `get_agent_by_role` (earliest-created row wins), the
        shared duplicate-tolerant lookup every singleton-role consumer uses.
        """
        agent = await get_agent_by_role(self.session, AgentRole.CEO)
        if agent is not None:
            agent.name = name

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
