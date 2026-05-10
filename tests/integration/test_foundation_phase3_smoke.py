"""Foundation Phase 3 smoke gate — communications + agent_loop canonicalization."""

from __future__ import annotations

import importlib
import inspect

import pytest
from roboco import agents_config
from roboco.agents_config import CHANNEL_ACCESS
from roboco.foundation import identity
from roboco.foundation.policy import communications
from roboco.foundation.policy.agent_loop import (
    DEFAULT_BUDGET,
    VERB_RETRY_LIMITS,
)
from roboco.foundation.policy.communications import (
    ACK_REQUIRED_BY_TYPE,
    NOTIFY_SENDER_ROLES,
    Priority,
)
from roboco.models.base import NotificationType
from roboco.seeds.initial_data import DEFAULT_CHANNELS
from roboco.services.gateway import content_actions
from roboco.services.gateway.envelope import Envelope

_EXPECTED_VERB_RETRY_LIMIT = 3


def test_notification_perms_module_removed():
    """services/enforcement/notification_perms.py is gone."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("roboco.enforcement.notification_perms")


def test_agents_config_notification_permissions_removed():
    """The contradictory NOTIFICATION_PERMISSIONS dict is gone."""
    assert not hasattr(agents_config, "NOTIFICATION_PERMISSIONS")


def test_channel_access_derives_from_foundation():
    """agents_config.CHANNEL_ACCESS keys match foundation.CHANNELS exactly."""
    assert set(CHANNEL_ACCESS.keys()) == set(communications.CHANNELS.keys())


def test_seed_default_channels_derive_from_foundation():
    """seeds.DEFAULT_CHANNELS slugs match foundation.CHANNELS."""
    seed_slugs = {ch["slug"] for ch in DEFAULT_CHANNELS}
    foundation_slugs = set(communications.CHANNELS.keys())
    assert seed_slugs == foundation_slugs


def test_notify_sender_roles_includes_ceo_excludes_auditor():
    """Spec §5.5 contradiction closed."""
    assert identity.Role.CEO in NOTIFY_SENDER_ROLES
    assert identity.Role.AUDITOR not in NOTIFY_SENDER_ROLES


def test_ack_required_table_covers_every_notification_type():
    """Spec §5.5 ACK_REQUIRED_BY_TYPE covers the full enum."""
    for nt in NotificationType:
        assert nt in ACK_REQUIRED_BY_TYPE


def test_a2a_priority_high_reachable():
    """A2A urgency tristate end-to-end (was reduced to boolean pre-Phase-3)."""
    # Confirm the foundation enum has all three values.
    values = {p.value for p in Priority}
    assert "normal" in values
    assert "high" in values
    assert "urgent" in values


def test_loop_action_default_is_halt():
    """Spec §5.7: BudgetPolicy.loop_action default is 'halt' (was 'warn')."""
    assert DEFAULT_BUDGET.loop_action == "halt"


def test_verb_retry_limits_cover_critical_handoff_verbs():
    """Spec §5.7: per-verb circuit breaker has caps for the handoff verbs."""
    for verb in ("i_am_done", "complete", "submit_up", "delegate"):
        assert verb in VERB_RETRY_LIMITS
        assert VERB_RETRY_LIMITS[verb] == _EXPECTED_VERB_RETRY_LIMIT


def test_envelope_circuit_open_kind_distinct_from_tracing_gap():
    """Spec §5.7: circuit_open envelope is its own kind."""
    env_co = Envelope.circuit_open(
        verb="i_am_done", attempts=4, window_seconds=60, remediate="x"
    )
    env_tg = Envelope.tracing_gap(missing=["x"], remediate="y")
    assert env_co.as_dict()["error"] == "circuit_open"
    assert env_tg.as_dict()["error"] == "tracing_gap"
    assert env_co.as_dict()["error"] != env_tg.as_dict()["error"]


def test_auditor_silent_runtime_guard_in_say_dm():
    """Spec §5.5: auditor say/dm refused at runtime (defense in depth)."""
    # The actual guard test lives in tests/unit/gateway/test_auditor_silent_guard.py.
    # Smoke gate verifies the guard exists by checking the source for the
    # specific role-check pattern.
    say_source = inspect.getsource(content_actions.ContentActions.say)
    dm_source = inspect.getsource(content_actions.ContentActions.dm)
    assert "auditor" in say_source.lower(), "say() missing auditor runtime guard"
    assert "auditor" in dm_source.lower(), "dm() missing auditor runtime guard"
