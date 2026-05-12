"""Foundation Phase 1 smoke gate.

End-to-end: a delegate call that would have produced a skeleton task
pre-Phase-1 must now return Envelope.incomplete_input with a populated
field_hints map. No silent fallback; no ``["completed and reviewed by
assignee"]`` ever lands in the DB.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import (
    Choreographer,
    ChoreographerDeps,
    DelegateInputs,
)

# Lower bound for a useful field hint. The canonical
# ``_HINT_ACCEPTANCE_CRITERIA`` text is ~200 chars; anything under this
# would mean the hint string was truncated or replaced with a stub.
_MIN_FIELD_HINT_LEN = 30


def _make_deps(**overrides: Any) -> ChoreographerDeps:
    """Build a ChoreographerDeps with AsyncMock services + empty evidence repo.

    Mirrors ``tests/unit/gateway/test_delegate_incomplete_input.py``: every
    evidence_repo lookup the briefing assembler reaches for must return
    ``[]`` so the briefing build does not raise on a coroutine result.
    """
    base: dict[str, Any] = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    base.update(overrides)
    repo = base["evidence_repo"]
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
    # C8: default-fresh journal:decision so PM-decision gate passes for
    # callers that don't override the journal mock.
    _ldef = base["journal"].latest_decision_at.return_value
    if type(_ldef).__name__ in ("MagicMock", "AsyncMock"):
        base["journal"].latest_decision_at.return_value = datetime.now(UTC)
    return ChoreographerDeps(**base)


@pytest.mark.asyncio
async def test_skeleton_task_path_returns_incomplete_input() -> None:
    """The 2026-05-10 smoke run produced subtasks with empty
    acceptance_criteria via the gateway. After Phase 1, that exact
    sequence must produce incomplete_input rejections instead.
    """
    pm_id = uuid4()
    parent = MagicMock(
        id=uuid4(),
        project_id=uuid4(),
        status="in_progress",
        assigned_to=pm_id,
        priority=2,
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = parent
    task_svc.agent_for.return_value = MagicMock(
        id=pm_id, role="cell_pm", team="backend", slug="be-pm"
    )
    task_svc.get_subtasks.return_value = []
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    # The exact shape from the failing smoke run: acceptance_criteria
    # missing, optional fields not provided.
    env = await c.delegate(
        pm_id,
        parent.id,
        DelegateInputs(
            title="Branch naming smoke test",
            description=(
                "Verify branch creation follows the feature/team/task convention."
            ),
            assigned_to="be-dev-1",
            team="backend",
            task_type="code",
            nature="technical",
            estimated_complexity="medium",
            acceptance_criteria=None,
        ),
    )
    body = env.as_dict()
    assert body["error"] == "incomplete_input", (
        f"expected incomplete_input, got: {body}"
    )
    assert "acceptance_criteria" in body["missing"]
    # The agent learns from field_hints what to fill:
    assert "acceptance_criteria" in body["field_hints"]
    assert len(body["field_hints"]["acceptance_criteria"]) > _MIN_FIELD_HINT_LEN
    # Skeleton task never reached the DB.
    task_svc.create_subtask.assert_not_awaited()


def test_no_silent_fallback_phrase_in_repo() -> None:
    """Production code must not contain the placeholder string outside of
    documented locations.

    Allowed appearances after Tasks 12 + 18 + 20:
    - ``roboco/foundation/policy/task_completeness.py`` — the DENYLIST
      entry plus the ``_HINT_ACCEPTANCE_CRITERIA`` text that names the
      phrase as a known evasion.
    - ``roboco/api/routes/tasks.py`` — comment in the POST /tasks handler
      explaining why the route runs ``task_completeness.check`` (Task 20).
    - ``roboco/services/task.py`` — docstring on ``create_subtask``
      documenting the Task 18 deletion of the silent fallback.

    Any other appearance is a real Phase 1 gap.
    """
    proc = subprocess.run(
        # ``-I`` skips binary files so stray ``__pycache__/*.pyc`` matches
        # (left over from a previous run) don't contaminate the grep.
        ["grep", "-rnI", "completed and reviewed by assignee", "roboco/"],
        capture_output=True,
        text=True,
        check=False,
    )
    allowed_files = (
        "roboco/foundation/policy/task_completeness.py",
        "roboco/api/routes/tasks.py",
        "roboco/services/task.py",
    )
    suspicious: list[str] = []
    for line in proc.stdout.splitlines():
        if not line:
            continue
        if any(line.startswith(f"{path}:") for path in allowed_files):
            continue
        suspicious.append(line)
    assert suspicious == [], (
        f"silent-fallback phrase still present in production code: {suspicious}"
    )
