"""Foundation Phase 3 smoke gate — communications + agent_loop canonicalization."""

from __future__ import annotations

import importlib

import pytest
from roboco import agents_config
from roboco.foundation import identity
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
from roboco.services.gateway.envelope import Envelope

_EXPECTED_VERB_RETRY_LIMIT = 3


def test_notification_perms_module_removed() -> None:
    """services/enforcement/notification_perms.py is gone."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("roboco.enforcement.notification_perms")


def test_agents_config_notification_permissions_removed() -> None:
    """The contradictory NOTIFICATION_PERMISSIONS dict is gone."""
    assert not hasattr(agents_config, "NOTIFICATION_PERMISSIONS")


def test_notify_sender_roles_includes_ceo_excludes_auditor() -> None:
    """Spec §5.5 contradiction closed."""
    assert identity.Role.CEO in NOTIFY_SENDER_ROLES
    assert identity.Role.AUDITOR not in NOTIFY_SENDER_ROLES


def test_ack_required_table_covers_every_notification_type() -> None:
    """Spec §5.5 ACK_REQUIRED_BY_TYPE covers the full enum."""
    for nt in NotificationType:
        assert nt in ACK_REQUIRED_BY_TYPE


def test_a2a_priority_high_reachable() -> None:
    """A2A urgency tristate end-to-end (was reduced to boolean pre-Phase-3)."""
    # Confirm the foundation enum has all three values.
    values = {p.value for p in Priority}
    assert "normal" in values
    assert "high" in values
    assert "urgent" in values


def test_loop_action_default_is_halt() -> None:
    """Spec §5.7: BudgetPolicy.loop_action default is 'halt' (was 'warn')."""
    assert DEFAULT_BUDGET.loop_action == "halt"


def test_verb_retry_limits_cover_critical_handoff_verbs() -> None:
    """Spec §5.7: per-verb circuit breaker has caps for the handoff verbs."""
    for verb in ("i_am_done", "complete", "submit_up", "delegate"):
        assert verb in VERB_RETRY_LIMITS
        assert VERB_RETRY_LIMITS[verb] == _EXPECTED_VERB_RETRY_LIMIT


def test_envelope_circuit_open_kind_distinct_from_tracing_gap() -> None:
    """Spec §5.7: circuit_open envelope is its own kind."""
    env_co = Envelope.circuit_open(
        verb="i_am_done", attempts=4, window_seconds=60, remediate="x"
    )
    env_tg = Envelope.tracing_gap(missing=["x"], remediate="y")
    assert env_co.as_dict()["error"] == "circuit_open"
    assert env_tg.as_dict()["error"] == "tracing_gap"
    assert env_co.as_dict()["error"] != env_tg.as_dict()["error"]
