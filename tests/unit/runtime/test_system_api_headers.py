"""The orchestrator's internal API calls must carry an authorized identity.

Regression guard for the wedge where dispatcher ``httpx`` clients were built
without an agent identity, so the orchestrator's self-PATCHes (auto-block /
auto-resume / auto-recover / SLA annotation) were rejected ``401 Missing
X-Agent-ID`` and silently no-op'd — leaving paused/blocked parents stuck and
their dependents stranded. The fix gives every API-facing dispatcher client the
system identity; these tests lock that the identity is both *present* and
*authorized* for task writes (otherwise the self-call would 403 instead of act).
"""

import secrets

import pytest
from roboco.agents_config import verify_agent_token
from roboco.foundation import identity as _foundation
from roboco.models import AgentRole
from roboco.models.permissions import TASK_PERMISSIONS, TaskAction
from roboco.runtime.orchestrator import _SYSTEM_API_HEADERS, _system_api_headers


def test_system_api_headers_match_the_system_identity() -> None:
    system = _foundation.AGENTS["system"]
    assert _SYSTEM_API_HEADERS["X-Agent-ID"] == str(system.uuid)
    assert _SYSTEM_API_HEADERS["X-Agent-Role"] == "system"


def test_system_identity_is_authorized_for_task_writes() -> None:
    # admin_set_status — the audited override path the orchestrator's
    # auto-recover / auto-resume drive — is gated behind TaskAction.ASSIGN.
    # The identity the orchestrator sends must hold it.
    assert TaskAction.ASSIGN in TASK_PERMISSIONS[AgentRole.SYSTEM]


def test_system_api_headers_carry_signed_token(monkeypatch: pytest.MonkeyPatch) -> None:
    # F038/F039: the orchestrator's self-API calls must carry a signed
    # X-Agent-Token for the system identity, or arming
    # ROBOCO_AGENT_AUTH_REQUIRED=true 401s every silent recovery op
    # (auto-block / auto-resume / auto-recover / SLA annotation) and wedges
    # paused/blocked parents — the self-PATCH 401 fix is incomplete without it.
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", secrets.token_hex(32))

    headers = _system_api_headers()
    token = headers["X-Agent-Token"]
    assert token and token != "UNSIGNED"
    assert verify_agent_token(token, headers["X-Agent-ID"], "system", "")


def test_system_api_headers_unsigned_when_secret_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Dev fallback: with no secret set, the token is the UNSIGNED sentinel and
    # auth is not required, so the self-call still succeeds. The header is
    # present either way so a future arm-when-secret-set doesn't silently break.
    monkeypatch.delenv("ROBOCO_AGENT_AUTH_SECRET", raising=False)
    headers = _system_api_headers()
    assert headers["X-Agent-Token"] == "UNSIGNED"
