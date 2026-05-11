"""Verify agents_config.* tables are derived from foundation, not duplicated.

The migration replaces the hand-maintained constants in agents_config with
expressions that compute from foundation.AGENTS. After this task:
- AGENT_ROLE_MAP[slug] == foundation.role_for_slug(slug).value (string-typed)
- AGENT_TEAM_MAP[slug] == foundation.team_for_slug(slug).value
- CELL_MEMBERS[team_value] == sorted(foundation.slugs_for_team(...))
"""

from __future__ import annotations

from roboco.agents_config import AGENT_ROLE_MAP, AGENT_TEAM_MAP, CELL_MEMBERS
from roboco.foundation import identity


def test_agent_role_map_matches_foundation() -> None:
    for slug, role_str in AGENT_ROLE_MAP.items():
        assert role_str == identity.role_for_slug(slug).value, (
            f"role drift for {slug!r}: agents_config has {role_str!r}, "
            f"foundation has {identity.role_for_slug(slug).value!r}"
        )
    # Every slug in foundation (except system sentinel) appears in AGENT_ROLE_MAP.
    foundation_slugs = set(identity.AGENTS) - {"system"}
    config_slugs = set(AGENT_ROLE_MAP)
    assert foundation_slugs <= config_slugs, (
        f"slugs in foundation but not agents_config: {foundation_slugs - config_slugs}"
    )


def test_agent_team_map_matches_foundation() -> None:
    for slug, team_str in AGENT_TEAM_MAP.items():
        assert team_str == identity.team_for_slug(slug).value, (
            f"team drift for {slug!r}: agents_config={team_str!r}, "
            f"foundation={identity.team_for_slug(slug).value!r}"
        )


def test_cell_members_matches_foundation() -> None:
    """CELL_MEMBERS keys are team-strings; values are sorted slug lists."""
    for team in (identity.Team.BACKEND, identity.Team.FRONTEND, identity.Team.UX_UI):
        config_members = CELL_MEMBERS.get(team.value, [])
        foundation_members = sorted(identity.slugs_for_team(team))
        assert sorted(config_members) == foundation_members, (
            f"cell members drift for {team.value!r}: "
            f"config={sorted(config_members)}, foundation={foundation_members}"
        )


def test_head_marketing_team_is_board_in_agents_config() -> None:
    """Resolves the head-marketing.team drift via the foundation derivation."""
    assert AGENT_TEAM_MAP["head-marketing"] == "board"
