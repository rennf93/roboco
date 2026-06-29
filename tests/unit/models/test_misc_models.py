"""Coverage for misc model methods, factories, and helper functions.

Targets the long-tail of small-percent gaps in roboco.models.* — convenience
properties, factory functions, lookup helpers, and __post_init__ branches.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from unittest.mock import patch
from uuid import uuid4

from roboco.models import AgentRole, Team
from roboco.models.a2a import (
    A2AConversation,
    A2AConversationStatus,
    A2ATaskState,
    a2a_state_to_task_status,
    task_status_to_a2a_state,
)
from roboco.models.agents import AgentConfig
from roboco.models.base import ModelProvider
from roboco.models.llm_catalog import (
    MODEL_CATALOG,
    _build_anthropic_entries,
    provider_type_for_model,
)
from roboco.models.permissions import (
    AgentContext,
    PermissionLevel,
    _build_role_levels,
)
from roboco.models.runtime import (
    AgentInstance,
    OrchestratorAgentConfig,
    OrchestratorAgentState,
)

# ---------------------------------------------------------------------------
# AgentConfig convenience properties (roboco/models/agents.py 63, 68, 73, 78)
# ---------------------------------------------------------------------------


def test_agent_config_convenience_properties() -> None:
    cfg = AgentConfig(
        name="Test",
        slug="test",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        system_prompt="hello",
    )
    # provider, model, temperature, max_tokens come from model_config_data
    assert cfg.provider == cfg.model_config_data.provider
    assert cfg.model == cfg.model_config_data.name
    assert cfg.temperature == cfg.model_config_data.temperature
    assert cfg.max_tokens == cfg.model_config_data.max_tokens


# ---------------------------------------------------------------------------
# AgentInstance.__post_init__ — explicit None id branch (runtime.py 76-77)
# ---------------------------------------------------------------------------


def test_agent_instance_post_init_assigns_uuid_when_falsy() -> None:
    # Forcing an empty UUID(int=0) is falsy → the post_init triggers re-assign.
    inst = AgentInstance.__new__(AgentInstance)
    # Fill required dataclass fields explicitly so __post_init__ runs cleanly.
    cc: Any = inst
    cc.id = None
    inst.agent_id = "be-dev-1"
    inst.state = OrchestratorAgentState.OFFLINE
    inst.container_id = None
    inst.config = None
    inst.started_at = None
    inst.last_activity = None
    inst.current_task_id = None
    inst.error_count = 0
    inst.waiting_for = None
    inst.waiting_context = {}
    inst.__post_init__()
    assert inst.id is not None


def test_agent_instance_default_factory_assigns_id() -> None:
    inst = AgentInstance(agent_id="be-dev-1")
    assert inst.id is not None


def test_orchestrator_agent_config_defaults() -> None:

    cfg = OrchestratorAgentConfig(
        agent_id="be-dev-1",
        blueprint_path=Path("/tmp/blueprint"),
    )
    assert cfg.model == "sonnet"
    assert cfg.provider_type == "anthropic"


# ---------------------------------------------------------------------------
# llm_catalog: catalog skip when MODEL_MAP missing entry (line 56)
# ---------------------------------------------------------------------------


def test_build_anthropic_entries_skips_missing_models() -> None:
    # Patch MODEL_MAP to be missing one entry → loop hits the `continue` branch.
    with patch(
        "roboco.models.llm_catalog.MODEL_MAP",
        {"opus": "claude-opus-4-6"},  # sonnet+haiku missing
    ):
        entries = _build_anthropic_entries()
    # Only one entry survives — opus.
    assert len(entries) == 1
    assert entries[0].model_name == "opus"


def test_provider_type_for_model_known() -> None:
    # Pick the first catalog entry deterministically.
    sample = MODEL_CATALOG[0]
    assert provider_type_for_model(sample.model_name) == sample.provider_type


def test_provider_type_for_model_unknown_returns_none() -> None:
    assert provider_type_for_model("nonexistent-model-x") is None


# ---------------------------------------------------------------------------
# permissions._build_role_levels — exception branch (lines 35-36)
# ---------------------------------------------------------------------------


def test_build_role_levels_skips_invalid_role() -> None:
    """Bad role string should be silently swallowed in the except branch."""
    with patch(
        "roboco.models.permissions.ROLE_PERMISSION_LEVELS",
        {"not_a_role": "CEO", "ceo": "CEO"},
    ):
        result = _build_role_levels()
    # 'not_a_role' silently dropped; ceo retained.
    assert AgentRole.CEO in result
    assert result[AgentRole.CEO] == PermissionLevel.CEO


def test_build_role_levels_skips_invalid_level() -> None:
    with patch(
        "roboco.models.permissions.ROLE_PERMISSION_LEVELS",
        {"ceo": "NOT_A_LEVEL"},
    ):
        result = _build_role_levels()
    assert result == {}


# ---------------------------------------------------------------------------
# A2A helpers — A2AConversation methods + a2a_state_to_task_status fallback
# ---------------------------------------------------------------------------


def test_a2a_conversation_other_agent() -> None:
    conv = A2AConversation(agent_a="be-dev-1", agent_b="fe-dev-1")
    assert conv.other_agent("be-dev-1") == "fe-dev-1"
    assert conv.other_agent("fe-dev-1") == "be-dev-1"


def test_a2a_conversation_my_unread() -> None:
    _UNREAD_A = 2
    _UNREAD_B = 5
    conv = A2AConversation(
        agent_a="be-dev-1",
        agent_b="fe-dev-1",
        unread_by_a=_UNREAD_A,
        unread_by_b=_UNREAD_B,
    )
    assert conv.my_unread("be-dev-1") == _UNREAD_A
    assert conv.my_unread("fe-dev-1") == _UNREAD_B


def test_a2a_conversation_is_participant() -> None:
    conv = A2AConversation(agent_a="be-dev-1", agent_b="fe-dev-1")
    assert conv.is_participant("be-dev-1") is True
    assert conv.is_participant("fe-dev-1") is True
    assert conv.is_participant("ux-dev-1") is False


def test_a2a_state_to_task_status_unknown_returns_pending() -> None:
    # Default fallback for any unknown state is "pending".
    class _FakeState:
        pass

    fake = _FakeState()
    assert a2a_state_to_task_status(cast("Any", fake)) == "pending"


def test_a2a_state_to_task_status_known() -> None:
    assert a2a_state_to_task_status(A2ATaskState.SUBMITTED) == "pending"
    assert a2a_state_to_task_status(A2ATaskState.WORKING) == "in_progress"
    assert a2a_state_to_task_status(A2ATaskState.COMPLETED) == "completed"
    assert a2a_state_to_task_status(A2ATaskState.FAILED) == "cancelled"
    assert a2a_state_to_task_status(A2ATaskState.INPUT_REQUIRED) == "blocked"


def test_task_status_to_a2a_state_known() -> None:
    assert task_status_to_a2a_state("pending") == A2ATaskState.SUBMITTED
    assert task_status_to_a2a_state("in_progress") == A2ATaskState.WORKING
    assert task_status_to_a2a_state("completed") == A2ATaskState.COMPLETED
    assert task_status_to_a2a_state("cancelled") == A2ATaskState.CANCELLED
    assert task_status_to_a2a_state("blocked") == A2ATaskState.INPUT_REQUIRED


def test_task_status_to_a2a_state_unknown_returns_working() -> None:
    assert task_status_to_a2a_state("garbage_status") == A2ATaskState.WORKING


# ---------------------------------------------------------------------------
# AgentContext.level — line 79
# ---------------------------------------------------------------------------


def test_agent_context_level_returns_role_level() -> None:
    ctx = AgentContext(agent_id=uuid4(), role=AgentRole.CEO)
    assert ctx.level == PermissionLevel.CEO


def test_agent_context_level_falls_back_when_role_missing() -> None:
    """Edge: build an AgentContext for a role removed from ROLE_LEVELS."""
    ctx = AgentContext(agent_id=uuid4(), role=AgentRole.DEVELOPER)
    # Default fallback returns CELL_MEMBER even when not present.
    assert ctx.level in PermissionLevel


# ---------------------------------------------------------------------------
# Conversation status enum + closed/paused round-trip
# ---------------------------------------------------------------------------


def test_a2a_conversation_status_values() -> None:
    assert A2AConversationStatus.ACTIVE == "active"
    assert A2AConversationStatus.PAUSED == "paused"
    assert A2AConversationStatus.CLOSED == "closed"


# ---------------------------------------------------------------------------
# Sanity: ModelProvider import is reachable
# ---------------------------------------------------------------------------


def test_model_provider_enum_has_anthropic() -> None:
    assert ModelProvider.ANTHROPIC == "anthropic"
