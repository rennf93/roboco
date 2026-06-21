"""The PR reviewer's pr_reviewer_notes section is obligated on its verbs.

pr_pass / pr_fail (in-path gate) and post_pr_review (inbound external PR) each
require a substantive review note. The note is the verb's own argument (not yet
persisted), so it is checked through a SimpleNamespace shim against the
``pr_reviewer_notes`` field — these cover both the short-circuit (too short) and
the pass-through (long enough) at the tracing-gate helpers.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_choreographer(*, has_learning: bool = True) -> Choreographer:
    base: dict[str, Any] = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    base["journal"].has_learning_for_task.return_value = has_learning
    return Choreographer(ChoreographerDeps(**base))


_LONG = "Reviewed the assembled diff end to end; the seam contract holds."
_SHORT = "looks ok"


@pytest.mark.asyncio
async def test_gate_tracing_blocks_on_short_notes() -> None:
    c = _make_choreographer()
    env = await c._gate_tracing(
        uuid4(), uuid4(), MagicMock(), "pr_reviewer", "pr_pass", notes=_SHORT
    )
    assert env is not None
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "pr_reviewer_notes>=min" in body["missing"]


@pytest.mark.asyncio
async def test_gate_tracing_passes_on_substantive_notes() -> None:
    c = _make_choreographer()
    env = await c._gate_tracing(
        uuid4(), uuid4(), MagicMock(), "pr_reviewer", "pr_fail", notes=_LONG
    )
    assert env is None


@pytest.mark.asyncio
async def test_post_pr_review_tracing_blocks_on_short_body() -> None:
    c = _make_choreographer()
    env = await c._pr_review_tracing_gate(
        uuid4(), uuid4(), MagicMock(), "pr_reviewer", body=_SHORT
    )
    assert env is not None
    assert "pr_reviewer_notes>=min" in env.as_dict()["missing"]


@pytest.mark.asyncio
async def test_post_pr_review_tracing_passes_on_substantive_body() -> None:
    c = _make_choreographer()
    env = await c._pr_review_tracing_gate(
        uuid4(), uuid4(), MagicMock(), "pr_reviewer", body=_LONG
    )
    assert env is None


@pytest.mark.asyncio
async def test_missing_learning_still_blocks_even_with_long_notes() -> None:
    """The journal:learning requirement remains independent of the note."""
    c = _make_choreographer(has_learning=False)
    env = await c._gate_tracing(
        uuid4(), uuid4(), MagicMock(), "pr_reviewer", "pr_pass", notes=_LONG
    )
    assert env is not None
    assert "journal:learning" in env.as_dict()["missing"]
