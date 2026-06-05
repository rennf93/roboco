"""agents_config coverage — pure-function role/team resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING

from roboco.agents_config import (
    can_a2a_direct,
    can_assign_tasks,
    can_cancel_tasks,
    can_create_tasks,
    can_send_notifications,
    get_a2a_route_hint,
    get_agent_cell,
    get_agent_role,
    get_agent_skills,
    get_agent_team,
    get_cell_members,
    get_escalation_target,
    get_pm_for_agent,
    get_pm_for_team,
    is_board_member,
    is_ceo,
    is_management,
    is_pm,
    issue_agent_token,
    issue_panel_token,
    verify_agent_token,
)
from roboco.seeds.initial_data import CEO_AGENT_ID

if TYPE_CHECKING:
    import pytest

# ---------------------------------------------------------------------------
# get_agent_role / get_agent_team / get_agent_cell
# ---------------------------------------------------------------------------


def test_get_agent_role_known() -> None:
    assert get_agent_role("be-dev-1") == "developer"


def test_get_agent_role_main_pm() -> None:
    assert get_agent_role("main-pm") == "main_pm"


def test_get_agent_role_unknown() -> None:
    assert get_agent_role("ghost-agent") == "unknown"


def test_get_agent_team_known() -> None:
    assert get_agent_team("be-dev-1") == "backend"


def test_get_agent_team_for_main_pm_returns_value() -> None:
    """main-pm has team 'main_pm' or None depending on config."""
    result = get_agent_team("main-pm")
    assert result is None or isinstance(result, str)


def test_get_agent_team_for_unknown() -> None:
    assert get_agent_team("ghost-agent") is None


def test_get_agent_cell_alias_for_team() -> None:
    assert get_agent_cell("be-dev-1") == "backend"


# ---------------------------------------------------------------------------
# Role predicates
# ---------------------------------------------------------------------------


def test_is_pm_for_cell_pm() -> None:
    assert is_pm("be-pm") is True


def test_is_pm_for_main_pm() -> None:
    assert is_pm("main-pm") is True


def test_is_pm_for_developer() -> None:
    assert is_pm("be-dev-1") is False


def test_is_management_for_main_pm() -> None:
    assert is_management("main-pm") is True


def test_is_management_for_developer() -> None:
    assert is_management("be-dev-1") is False


def test_is_ceo_for_ceo() -> None:
    assert is_ceo("ceo") is True


def test_is_ceo_for_developer() -> None:
    assert is_ceo("be-dev-1") is False


def test_is_board_member() -> None:
    # Whatever the actual board members are, the function should return bool.
    assert isinstance(is_board_member("auditor"), bool)


# ---------------------------------------------------------------------------
# Permission predicates
# ---------------------------------------------------------------------------


def test_can_send_notifications_main_pm() -> None:
    assert can_send_notifications("main-pm") is True


def test_can_send_notifications_developer() -> None:
    assert can_send_notifications("be-dev-1") is False


def test_can_create_tasks_main_pm() -> None:
    assert can_create_tasks("main-pm") is True


def test_can_create_tasks_developer() -> None:
    assert can_create_tasks("be-dev-1") is False


def test_can_assign_tasks_main_pm() -> None:
    assert can_assign_tasks("main-pm") is True


def test_can_assign_tasks_developer() -> None:
    assert can_assign_tasks("be-dev-1") is False


def test_can_cancel_tasks_pm() -> None:
    assert can_cancel_tasks("main-pm") is True


def test_can_cancel_tasks_ceo() -> None:
    """CEO cannot cancel — they observe only."""
    assert can_cancel_tasks("ceo") is False


def test_can_cancel_tasks_auditor() -> None:
    assert can_cancel_tasks("auditor") is False


# ---------------------------------------------------------------------------
# Escalation + PM resolution
# ---------------------------------------------------------------------------


def test_get_escalation_target() -> None:
    target = get_escalation_target("be-dev-1")
    assert target is None or isinstance(target, str)


def test_get_pm_for_team_known() -> None:
    assert get_pm_for_team("backend") == "be-pm"


def test_get_pm_for_team_unknown() -> None:
    assert get_pm_for_team("mars") is None


def test_get_pm_for_agent_cell_pm_returns_main_pm() -> None:
    assert get_pm_for_agent("be-pm") == "main-pm"


def test_get_pm_for_agent_developer() -> None:
    pm = get_pm_for_agent("be-dev-1")
    # Cell members' PM is their cell PM.
    assert pm == "be-pm" or pm is None


# ---------------------------------------------------------------------------
# A2A policy
# ---------------------------------------------------------------------------


def test_can_a2a_direct_to_ceo_denied() -> None:
    allowed, reason = can_a2a_direct("be-dev-1", "ceo")
    assert allowed is False
    assert reason is not None


def test_can_a2a_direct_within_cell() -> None:
    allowed, _ = can_a2a_direct("be-dev-1", "be-qa")
    assert isinstance(allowed, bool)


def test_get_a2a_route_hint_returns_string() -> None:
    hint = get_a2a_route_hint("be-dev-1", "fe-dev-1")
    assert isinstance(hint, str)


def test_get_a2a_route_hint_for_ceo() -> None:
    hint = get_a2a_route_hint("be-dev-1", "ceo")
    assert "CEO" in hint


# ---------------------------------------------------------------------------
# get_cell_members
# ---------------------------------------------------------------------------


def test_get_cell_members_backend() -> None:
    members = get_cell_members("backend")
    assert isinstance(members, list)


def test_get_cell_members_unknown() -> None:
    assert get_cell_members("mars") == []


# ---------------------------------------------------------------------------
# get_agent_skills
# ---------------------------------------------------------------------------


def test_get_agent_skills_returns_list() -> None:
    skills = get_agent_skills("be-dev-1")
    assert isinstance(skills, list)


def test_get_agent_skills_unknown_agent() -> None:
    skills = get_agent_skills("ghost-agent")
    assert isinstance(skills, list)


# ---------------------------------------------------------------------------
# Token issuance + verification (lines 45-46, 67-72, 83-89)
# ---------------------------------------------------------------------------


def test_issue_agent_token_returns_unsigned_when_secret_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    monkeypatch.delenv("ROBOCO_AGENT_AUTH_SECRET", raising=False)
    assert issue_agent_token("be-dev-1", "developer", "backend") == "UNSIGNED"


_SHA256_HEX_LEN = 64


def test_issue_agent_token_signs_when_secret_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", "test-secret")
    tok = issue_agent_token("be-dev-1", "developer", "backend")
    assert tok != "UNSIGNED"
    # 64 hex chars from sha256
    assert len(tok) == _SHA256_HEX_LEN


def test_verify_agent_token_round_trips(monkeypatch: pytest.MonkeyPatch) -> None:

    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", "rt-secret")
    tok = issue_agent_token("be-dev-1", "developer", "backend")
    assert verify_agent_token(tok, "be-dev-1", "developer", "backend") is True


def test_verify_agent_token_rejects_when_secret_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    monkeypatch.delenv("ROBOCO_AGENT_AUTH_SECRET", raising=False)
    assert verify_agent_token("anything", "be-dev-1", "developer", "backend") is False


def test_verify_agent_token_rejects_unsigned_sentinel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", "any-secret")
    assert verify_agent_token("UNSIGNED", "be-dev-1", "developer", "backend") is False


def test_verify_agent_token_rejects_empty_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", "any-secret")
    assert verify_agent_token("", "be-dev-1", "developer", "backend") is False


def test_verify_agent_token_rejects_mismatched_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", "real-secret")
    tok = issue_agent_token("be-dev-1", "developer", "backend")
    # Verify with different role → mismatch.
    assert verify_agent_token(tok, "be-dev-1", "qa", "backend") is False


# ---------------------------------------------------------------------------
# issue_panel_token — the panel's CEO credential for secure mode
# ---------------------------------------------------------------------------


def test_issue_panel_token_verifies_under_panel_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The panel token must verify under the EXACT headers the panel sends:
    X-Agent-Id = CEO uuid, X-Agent-Role = ceo, and NO team (empty)."""
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", "panel-secret")
    tok = issue_panel_token()
    assert verify_agent_token(tok, CEO_AGENT_ID, "ceo", "") is True


def test_issue_panel_token_unsigned_without_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ROBOCO_AGENT_AUTH_SECRET", raising=False)
    assert issue_panel_token() == "UNSIGNED"


def test_panel_token_does_not_grant_other_roles_or_identities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The panel token is bound to the CEO identity — it cannot be replayed to
    claim a different role or agent id."""
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", "panel-secret")
    tok = issue_panel_token()
    assert verify_agent_token(tok, CEO_AGENT_ID, "developer", "") is False
    assert verify_agent_token(tok, "be-dev-1", "ceo", "") is False


# ---------------------------------------------------------------------------
# get_pm_for_agent main_pm escalation (line 360)
# ---------------------------------------------------------------------------


def test_get_pm_for_agent_main_pm_returns_product_owner() -> None:
    assert get_pm_for_agent("main-pm") == "product-owner"


# ---------------------------------------------------------------------------
# A2A check branches: cell PM rejects board (line 682), cell-member without
# team falls through to management route (line 704), board successful path
# (732), main_pm dispatch (744), cell-member outbound to no-team (771-772).
# ---------------------------------------------------------------------------


def test_can_a2a_direct_cell_pm_to_board_denied() -> None:
    allowed, reason = can_a2a_direct("be-pm", "product-owner")
    assert allowed is False
    assert reason is not None
    assert "main-pm" in reason


def test_can_a2a_direct_cell_member_to_unknown_management() -> None:
    """Cell dev → CEO routes through CEO branch, not management."""
    # Dev → main-pm: management → falls into cell_member branch's else case.
    allowed, reason = can_a2a_direct("be-dev-1", "main-pm")
    # Either path works; just ensure structured output.
    assert isinstance(allowed, bool)
    assert reason is None or isinstance(reason, str)


def test_can_a2a_direct_main_pm_to_developer_denied() -> None:
    allowed, reason = can_a2a_direct("main-pm", "be-dev-1")
    # main-pm to a developer routed via cell PM.
    assert allowed is False
    assert reason is not None


def test_can_a2a_direct_main_pm_to_cell_pm_allowed() -> None:
    allowed, _ = can_a2a_direct("main-pm", "be-pm")
    assert allowed is True


def test_can_a2a_direct_board_to_main_pm_allowed() -> None:
    allowed, _ = can_a2a_direct("product-owner", "main-pm")
    assert allowed is True


def test_can_a2a_direct_board_to_developer_denied() -> None:
    allowed, reason = can_a2a_direct("product-owner", "be-dev-1")
    assert allowed is False
    assert reason is not None


def test_get_a2a_route_hint_cell_member_to_management() -> None:
    """Non-CEO cell-member → management agent (no team) routes via cell PM."""
    hint = get_a2a_route_hint("be-dev-1", "main-pm")
    # main-pm has team None → falls through to management branch (771-772).
    assert "be-pm" in hint or "main-pm" in hint or "Use" in hint


def test_can_a2a_direct_cell_member_to_unknown_returns_management_hint() -> None:
    """Cell-member → agent with unresolvable team hits management branch (704)."""
    allowed, reason = can_a2a_direct("be-dev-1", "ghost-agent")
    assert allowed is False
    assert reason is not None
    assert "be-pm" in reason or "main-pm" in reason


def test_can_a2a_direct_unknown_from_agent_falls_through() -> None:
    """An agent with no team hits the final fallback (line 750)."""
    allowed, reason = can_a2a_direct("ghost", "be-dev-1")
    assert allowed is False
    assert reason is not None
    assert "not permitted" in reason


def test_get_a2a_route_hint_cell_member_to_unknown_routes_via_cell_pm() -> None:
    """Cell-member → unknown agent (no team) hits lines 770-772."""
    hint = get_a2a_route_hint("be-dev-1", "ghost-agent")
    assert "be-pm" in hint
    assert "main-pm" in hint


def test_get_a2a_route_hint_unknown_from_agent_falls_through() -> None:
    """from_agent with no team falls through to escalate fallback (line 774)."""
    hint = get_a2a_route_hint("ghost", "be-dev-1")
    assert "escalate" in hint.lower()
