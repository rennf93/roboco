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
from roboco.runtime.orchestrator import (
    _SYSTEM_API_HEADERS,
    _agent_api_headers,
    _system_api_headers,
)


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


def test_agent_api_headers_carry_signed_token_and_team(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The cell-PM auto-submit self-API call acts as a specific PM. A hand-built
    # {X-Agent-ID, X-Agent-Role} dict 401s under ROBOCO_AGENT_AUTH_REQUIRED —
    # same F038/F039 gap as the system self-call. _agent_api_headers must carry
    # a token signed for that PM's (id, role, team) plus the team header.
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", secrets.token_hex(32))
    be_pm = _foundation.AGENTS["be-pm"]
    be_pm_uuid = str(be_pm.uuid)
    role = be_pm.role.value  # "cell_pm"
    team = be_pm.team.value  # "backend"

    headers = _agent_api_headers(be_pm_uuid, role)

    assert headers["X-Agent-ID"] == be_pm_uuid
    assert headers["X-Agent-Role"] == role
    assert headers["X-Agent-Team"] == team
    token = headers["X-Agent-Token"]
    assert token and token != "UNSIGNED"
    assert verify_agent_token(token, be_pm_uuid, role, team)


def test_agent_api_headers_omit_token_when_secret_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Dev mode: with no secret set, issue_agent_token returns the UNSIGNED
    # sentinel, but the dev-mode middleware rejects a presented-but-unverifiable
    # token with 401 "signature mismatch" while accepting a missing token.
    # Sending UNSIGNED would 401 the cell-PM auto-submit self-call in every dev
    # run (the e2e test_auto_submit_cuts_the_pm_turn regression), so the token
    # header is omitted entirely when the secret is unset.
    monkeypatch.delenv("ROBOCO_AGENT_AUTH_SECRET", raising=False)
    be_pm = _foundation.AGENTS["be-pm"]
    headers = _agent_api_headers(str(be_pm.uuid), be_pm.role.value)
    assert "X-Agent-Token" not in headers
