"""Task #159: tracing-gap remediate covers every missing requirement.

Pre-fix:
    `journal:during_work>=1` (and a handful of other tokens) had no entry
    in `_hint_for_missing_key`'s `simple_hints` dict. When the
    requirement was missing, the agent saw the token in `missing[]` but
    the `remediate` string contained NO instruction for how to satisfy
    it. The agent fixed the items with hints, retried, hit the same
    rejection again on the unhinted token, and looped.

Fix:
    Register hints for the previously-unhinted tokens
    (`journal:during_work>=1`, `journal:struggle`, `commits>=1`,
    `pr_open`, `self_verified`). Multi-hint remediate switches to a
    numbered list so the model sees each requirement as a distinct
    instruction instead of a semicolon-blob.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps

_MIN_HINT_LEN = 10


def _build_choreographer() -> Choreographer:
    deps = ChoreographerDeps(
        task=AsyncMock(),
        work_session=AsyncMock(),
        git=AsyncMock(),
        a2a=AsyncMock(),
        journal=AsyncMock(),
        audit=AsyncMock(),
        evidence_repo=AsyncMock(),
    )
    repo = deps.evidence_repo
    for method in (
        "list_unread_a2a",
        "list_unread_mentions",
        "list_pending_notifications",
        "task_metadata_gaps",
        "recent_team_activity",
        "blockers_in_lane",
        "journal_highlights_for_task",
    ):
        getattr(repo, method).return_value = []
    return Choreographer(deps)


# ---------------------------------------------------------------------------
# Hint registration — every previously-unhinted token now has a hint
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token",
    [
        "journal:during_work>=1",
        "journal:struggle",
        "commits>=1",
        "pr_open",
        "self_verified",
        "render_preview",
    ],
)
def test_hint_registered_for_previously_unhinted_token(token: str) -> None:
    """Every requirement token must have a non-empty hint so the agent
    knows how to satisfy it."""
    hint = Choreographer._hint_for_missing_key(token, uuid4())
    assert hint is not None, (
        f"requirement token {token!r} has no hint; agent will see the "
        f"token in `missing[]` but no actionable instruction"
    )
    assert len(hint) > _MIN_HINT_LEN, (
        f"hint for {token!r} is suspiciously short: {hint!r}"
    )


def test_during_work_hint_warns_reflect_doesnt_count() -> None:
    """The during_work hint must explicitly say reflect doesn't satisfy
    it — that's the exact confusion smoke-10's be-dev-1 hit."""
    hint = Choreographer._hint_for_missing_key("journal:during_work>=1", uuid4())
    assert hint is not None
    lower = hint.lower()
    assert "reflect" in lower and (
        "does not count" in lower
        or "doesn't count" in lower
        or "do not count" in lower
        or "not count" in lower
    ), f"during_work hint must warn that scope='reflect' doesn't satisfy this: {hint!r}"


def test_render_preview_hint_mentions_request_render() -> None:
    """The render_preview hint must name the exact next call (request_render)."""
    hint = Choreographer._hint_for_missing_key("render_preview", uuid4())
    assert hint is not None
    assert "request_render" in hint


# ---------------------------------------------------------------------------
# Multi-hint remediate — numbered list, not semicolon-joined
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_missing_remediate_is_plain_string() -> None:
    """One missing item: remediate is the hint itself, no numbering."""
    c = _build_choreographer()
    env = await c._build_tracing_gap(uuid4(), uuid4(), ["journal:reflect"])
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert body["missing"] == ["journal:reflect"]
    assert "Multiple requirements missing" not in (body["remediate"] or "")
    assert "1." not in (body["remediate"] or "")


@pytest.mark.asyncio
async def test_multi_missing_remediate_is_numbered_list() -> None:
    """Multiple missing items: remediate must be a numbered list so the
    agent treats each as a distinct step."""
    c = _build_choreographer()
    env = await c._build_tracing_gap(
        uuid4(),
        uuid4(),
        ["journal:reflect", "journal:during_work>=1", "commits>=1"],
    )
    body = env.as_dict()
    remediate = body["remediate"] or ""
    assert "Multiple requirements missing" in remediate, remediate
    assert "1." in remediate
    assert "2." in remediate
    assert "3." in remediate
    # All three hints must be substring-present (proves each was emitted).
    assert "reflect" in remediate.lower()
    assert "during" in remediate.lower() or "decision" in remediate.lower()
    assert "commit" in remediate.lower()


@pytest.mark.asyncio
async def test_unhinted_token_still_appears_in_remediate() -> None:
    """If a future requirement is added without a hint (defense), the
    agent at least sees the literal token in the remediate — not silently
    dropped."""
    c = _build_choreographer()
    env = await c._build_tracing_gap(
        uuid4(),
        uuid4(),
        ["unknown_future_requirement"],
    )
    body = env.as_dict()
    remediate = body["remediate"] or ""
    assert "unknown_future_requirement" in remediate, (
        f"unhinted token must surface in remediate as fallback. Got: {remediate!r}"
    )


@pytest.mark.asyncio
async def test_acceptance_criteria_grouped_into_one_hint() -> None:
    """Acceptance criteria entries collapse into a single hint regardless
    of how many criteria are missing (so 5 unaddressed criteria don't
    produce 5 separate numbered items)."""
    c = _build_choreographer()
    task_id = uuid4()
    env = await c._build_tracing_gap(
        uuid4(),
        task_id,
        [
            "acceptance_criterion:Branch named correctly",
            "acceptance_criterion:Commit prefix present",
            "acceptance_criterion:PR opened",
        ],
    )
    body = env.as_dict()
    remediate = body["remediate"] or ""
    # All three criterion names should appear in the SAME hint section
    # (collapsed into one hint, not three separate numbered items).
    assert "Branch named correctly" in remediate
    assert "Commit prefix present" in remediate
    assert "PR opened" in remediate
    # Single-hint case → no "Multiple requirements" preamble.
    assert "Multiple requirements missing" not in remediate
