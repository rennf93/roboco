"""Seed and orchestrator agent maps must derive from foundation."""

from __future__ import annotations

from roboco.foundation import identity
from roboco.runtime import orchestrator
from roboco.seeds.initial_data import AGENT_UUIDS, DEFAULT_AGENTS


def test_seed_agent_uuids_derive_from_foundation() -> None:
    """seeds.initial_data.AGENT_UUIDS is derived; not hand-maintained."""
    expected = {slug: str(row.uuid) for slug, row in identity.AGENTS.items()}
    assert expected == AGENT_UUIDS, (
        f"AGENT_UUIDS drift from foundation: {set(expected) ^ set(AGENT_UUIDS)}"
    )


def test_default_agents_derive_role_team_from_foundation() -> None:
    """DEFAULT_AGENTS rows must use the foundation-declared role+team."""
    by_slug = {row["slug"]: row for row in DEFAULT_AGENTS}
    for slug, row in identity.AGENTS.items():
        if slug == "system":
            # system may or may not be in DEFAULT_AGENTS depending on bootstrap
            continue
        assert slug in by_slug, f"{slug} missing from DEFAULT_AGENTS"
        assert by_slug[slug]["role"] == row.role.value, (
            f"role drift for {slug}: seed={by_slug[slug]['role']}, "
            f"foundation={row.role.value}"
        )
        assert by_slug[slug]["team"] == row.team.value, (
            f"team drift for {slug}: seed={by_slug[slug]['team']}, "
            f"foundation={row.team.value}"
        )


def test_head_marketing_seeded_as_board() -> None:
    """Resolves the head-marketing drift in the seed file."""
    head = next(r for r in DEFAULT_AGENTS if r["slug"] == "head-marketing")
    assert head["team"] == "board"


def test_orchestrator_team_resolution_uses_foundation() -> None:
    """orchestrator's team resolution agrees with foundation for every slug."""
    if hasattr(orchestrator, "_AGENT_TEAM_MAP"):
        for slug, team_str in orchestrator._AGENT_TEAM_MAP.items():
            assert team_str == identity.team_for_slug(slug).value, (
                f"team drift for {slug}: orchestrator={team_str}, "
                f"foundation={identity.team_for_slug(slug).value}"
            )

    # Resolution by behavior:
    assert identity.team_for_slug("be-dev-1") == identity.Team.BACKEND
    assert identity.team_for_slug("head-marketing") == identity.Team.BOARD
