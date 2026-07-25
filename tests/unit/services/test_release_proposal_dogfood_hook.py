"""The release-proposal publish hook triggers a Dogfood walk cycle
(best-effort, never raises into approve()). Layering: release_proposal calls
only ``BoardProgramEngine.open_program_cycle("dogfood")`` — this test patches
at that seam, not the engine's internals. Mirrors
test_release_proposal_war_room_hook.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.services.release_proposal import ReleaseProposalService


@pytest.mark.asyncio
async def test_draft_dogfood_walk_calls_engine_seam() -> None:
    fake_engine = AsyncMock()
    fake_engine.open_program_cycle = AsyncMock(return_value=None)

    with patch(
        "roboco.services.board_programs.get_board_program_engine",
        return_value=fake_engine,
    ):
        await ReleaseProposalService(MagicMock())._draft_dogfood_walk()

    fake_engine.open_program_cycle.assert_awaited_once_with("dogfood")


@pytest.mark.asyncio
async def test_draft_dogfood_walk_swallows_engine_exception() -> None:
    """An engine exception must never propagate out of the best-effort seam —
    the release already published; a walk-trigger failure can't un-publish
    it."""
    with patch(
        "roboco.services.board_programs.get_board_program_engine",
        side_effect=RuntimeError("dogfood boom"),
    ):
        await ReleaseProposalService(MagicMock())._draft_dogfood_walk()
