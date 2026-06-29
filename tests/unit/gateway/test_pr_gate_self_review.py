"""The in-path PR-review gate must block a reviewer who is the original dev.

``pr_pass`` / ``pr_fail`` carry ``self_review_block=True`` in the lifecycle spec,
but ``_gate_preflight`` never wired the spec ``Context.original_developer_slug``
(and ``actor_slug`` was read off ``agent.slug``, which ``GatewayAgentView`` does
not carry — so it was always ``None`` in production). The block was therefore
structurally dormant: a reviewer who happened to also be the original developer
of the assembled PR could pass their own work. The service-layer
``_validate_not_self_review`` backstop only covers qa/documenter, not
pr_reviewer, so the spec gate is the only defense. This pins that the gate now
fires when the ``original_developer`` marker resolves to the reviewer, and
stays inert (legitimate review proceeds) when it resolves to someone else.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.foundation.policy.content import markers
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps
from roboco.services.gateway.envelope import Envelope


def _make_choreographer() -> Choreographer:
    base: dict[str, Any] = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    return Choreographer(ChoreographerDeps(**base))


def _wire_preflight(
    c: Choreographer,
    *,
    reviewer_id: Any,
    original_dev_id: Any,
    task_id: Any,
) -> MagicMock:
    """Drive the REAL ``_gate_preflight`` past ownership into the spec gate.

    Only the I/O helpers it calls before the spec decision are stubbed; the
    spec gate itself runs unmodified so the self_review block is exercised.
    ``agent_for`` returns a ``MagicMock`` with no explicit ``slug`` (mirroring
    ``GatewayAgentView``, which has no slug field) so the fix's explicit
    ``actor_slug=str(reviewer_agent_id)`` is what makes the comparison work.
    """
    t = MagicMock(
        id=task_id,
        assigned_to=reviewer_id,
        status="awaiting_pr_review",
        task_type="code",
        orchestration_markers={markers.ORIGINAL_DEVELOPER: str(original_dev_id)},
    )
    cc: Any = c
    cc.task.get = AsyncMock(return_value=t)
    # No ``slug`` attribute set — mirrors GatewayAgentView (getattr -> None).
    cc.task.agent_for = AsyncMock(return_value=MagicMock(role="pr_reviewer"))
    cc._briefing_for = AsyncMock(return_value={})
    cc._guard_free_text = AsyncMock(return_value=None)
    # Pass-through so the spec-gate Envelope reaches the assertion unchanged.
    cc._emit_rejection = AsyncMock(side_effect=lambda env, **_kw: env)
    return t


@pytest.mark.asyncio
async def test_pr_pass_blocks_reviewer_who_is_original_developer() -> None:
    """The reviewer IS the original developer (marker == reviewer) — the
    self_review block must fire and ``_gate_preflight`` must return a
    ``not_authorized`` Envelope, not the pass-through tuple."""
    reviewer_id = uuid4()
    task_id = uuid4()
    c = _make_choreographer()
    _wire_preflight(
        c, reviewer_id=reviewer_id, original_dev_id=reviewer_id, task_id=task_id
    )

    pre = await c._gate_preflight(
        reviewer_id, task_id, "pr_pass", notes="looks fine", issues=()
    )

    assert isinstance(pre, Envelope)
    assert pre.error == "not_authorized"
    assert pre.message is not None and "self-review" in pre.message


@pytest.mark.asyncio
async def test_pr_fail_blocks_reviewer_who_is_original_developer() -> None:
    """Same self_review defense on ``pr_fail`` — a reviewer must not fail their
    own assembled PR either (could rubber-stamp or sabotage their own work)."""
    reviewer_id = uuid4()
    task_id = uuid4()
    c = _make_choreographer()
    _wire_preflight(
        c, reviewer_id=reviewer_id, original_dev_id=reviewer_id, task_id=task_id
    )

    pre = await c._gate_preflight(
        reviewer_id,
        task_id,
        "pr_fail",
        notes="Issues:\n- x",
        issues=("x",),
    )

    assert isinstance(pre, Envelope)
    assert pre.error == "not_authorized"
    assert pre.message is not None and "self-review" in pre.message


@pytest.mark.asyncio
async def test_legitimate_review_proceeds_when_reviewer_is_not_original_dev() -> None:
    """Regression guard: a reviewer who is NOT the original developer must pass
    through the spec gate (preflight returns the tuple, not a rejection) — the
    block fires only on the self-review edge, never on a normal review."""
    reviewer_id = uuid4()
    other_dev_id = uuid4()
    task_id = uuid4()
    c = _make_choreographer()
    _wire_preflight(
        c, reviewer_id=reviewer_id, original_dev_id=other_dev_id, task_id=task_id
    )

    pre = await c._gate_preflight(
        reviewer_id, task_id, "pr_pass", notes="looks fine", issues=()
    )

    assert not isinstance(pre, Envelope)


@pytest.mark.asyncio
async def test_review_proceeds_when_no_original_developer_marker() -> None:
    """Assembled coordination tasks never set the ``original_developer`` marker
    (only dev-leaf tasks set it at QA/doc claim), so in production the marker is
    absent and the block is dormant by design — a normal review proceeds. This
    pins that wiring the gate does not accidentally fire when there is no
    marker to compare against."""
    reviewer_id = uuid4()
    task_id = uuid4()
    c = _make_choreographer()
    t = _wire_preflight(
        c, reviewer_id=reviewer_id, original_dev_id=reviewer_id, task_id=task_id
    )
    # Wipe the marker — assembled coordination root, no original dev recorded.
    t.orchestration_markers = None

    pre = await c._gate_preflight(
        reviewer_id, task_id, "pr_pass", notes="looks fine", issues=()
    )

    assert not isinstance(pre, Envelope)
