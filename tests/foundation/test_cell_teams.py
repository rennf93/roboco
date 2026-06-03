from __future__ import annotations

from roboco.foundation.identity import CELL_TEAMS, Team


def test_cell_teams_is_exactly_the_three_cells() -> None:
    assert frozenset({Team.BACKEND, Team.FRONTEND, Team.UX_UI}) == CELL_TEAMS


def test_cell_teams_excludes_non_cells() -> None:
    for non_cell in (Team.BOARD, Team.MAIN_PM, Team.MARKETING, Team.SYSTEM):
        assert non_cell not in CELL_TEAMS


def test_cell_team_values_match_db_strings() -> None:
    assert {t.value for t in CELL_TEAMS} == {"backend", "frontend", "ux_ui"}
