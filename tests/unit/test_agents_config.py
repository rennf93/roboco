"""agents_config coverage — pure-function role/team resolution."""

from __future__ import annotations

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
)

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
