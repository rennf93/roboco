"""DO verbs accept the 8-char task-id prefixes the cards/prompts display.

evidence/task_time got short-id resolution in 6fbf40fb after be-qa burned
a run on evidence 422s; the review pass found six more agent-facing verbs
still UUID-typed (progress, preflight_diff, triage_failure, pr_update,
curate_vault, nothing_to_propose) - the same wedge shape one verb away
from recurring. All resolve through ContentActions._resolve_task_ref.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.content_actions import ContentActions
from tests.unit.gateway.test_content_actions import _make_deps


def _resolver_task_svc(task_id: Any, matches: list[Any] | None = None) -> AsyncMock:
    """Task service whose session.execute returns the resolver's lookup rows."""
    task_svc = AsyncMock()
    execute_result = MagicMock()
    if matches is None:
        execute_result.all.return_value = [(task_id,)]
    else:
        execute_result.all.return_value = matches
    task_svc.session = MagicMock()
    task_svc.session.execute = AsyncMock(return_value=execute_result)
    task_svc.session.commit = AsyncMock()
    return task_svc


@pytest.mark.asyncio
async def test_progress_accepts_short_task_id_prefix() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = _resolver_task_svc(task_id)
    task_svc.get.return_value = MagicMock(
        id=task_id,
        status="in_progress",
        active_claimant_id=agent_id,
        assigned_to=agent_id,
        plan=[],
        branch_name="feature/x",
    )
    ca = ContentActions(_make_deps(task=task_svc))

    env = await ca.progress(
        agent_id=agent_id,
        task_id=str(task_id)[:8],
        message=" Implemented the resolver wiring for progress updates.",
    )

    assert env.as_dict()["error"] is None
    task_svc.get.assert_awaited_with(task_id)


@pytest.mark.asyncio
async def test_preflight_diff_accepts_short_task_id_prefix() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = _resolver_task_svc(task_id)
    task_svc.get.return_value = MagicMock(
        id=task_id, assigned_to=None, status="in_progress"
    )
    git_svc = AsyncMock()
    git_svc.diff_and_files.return_value = ("diff --git a/x b/x", ["x"])
    ca = ContentActions(_make_deps(task=task_svc, git=git_svc))

    env = await ca.preflight_diff(agent_id=agent_id, task_id=str(task_id)[:8])

    # The verb body may reject on its own preconditions with this minimal
    # mock; the resolver wiring is proven by get() receiving the FULL uuid
    # and by the absence of resolver-shaped errors (not_found/incomplete).
    body = env.as_dict()
    assert body["error"] not in ("not_found", "incomplete_input")
    task_svc.get.assert_awaited_with(task_id)


@pytest.mark.asyncio
async def test_triage_failure_accepts_short_task_id_prefix() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = _resolver_task_svc(task_id)
    task = MagicMock(id=task_id, assigned_to=agent_id, status="in_progress")
    task.branch_name = None
    task_svc.get.return_value = task
    git_svc = AsyncMock()
    git_svc.diff_and_files.return_value = ("diff", ["x"])
    ca = ContentActions(_make_deps(task=task_svc, git=git_svc))

    env = await ca.triage_failure(
        agent_id=agent_id,
        task_id=str(task_id)[:8],
        test_name="test_resolver",
        error_excerpt="AssertionError: prefix not accepted",
    )

    body = env.as_dict()
    # The lane is advisory: any clean verdict shape proves the ref resolved
    # (a 422/not_found would carry an error).
    assert body["error"] in (None, "ok")


@pytest.mark.asyncio
async def test_pr_update_ambiguous_prefix_reports_incomplete() -> None:
    task_svc = _resolver_task_svc(uuid4(), matches=[(uuid4(),), (uuid4(),)])
    ca = ContentActions(_make_deps(task=task_svc))

    env = await ca.pr_update(
        agent_id=uuid4(), task_id="abcd1234", title="New PR title here"
    )

    body = env.as_dict()
    assert body["error"] == "incomplete_input"
    # The ambiguity detail rides the field hint (message stays generic).
    assert "abcd1234" in str(body.get("field_hints", "")) + str(body.get("message", ""))


@pytest.mark.asyncio
async def test_full_uuid_still_passes_through_resolver() -> None:
    """The fast path: a full UUID skips the lookup entirely (no session hit)."""
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = _resolver_task_svc(task_id)
    task_svc.get.return_value = MagicMock(
        id=task_id, assigned_to=None, status="in_progress"
    )
    git_svc = AsyncMock()
    git_svc.diff_and_files.return_value = ("diff", ["x"])
    ca = ContentActions(_make_deps(task=task_svc, git=git_svc))

    env = await ca.preflight_diff(agent_id=agent_id, task_id=str(task_id))

    body = env.as_dict()
    assert body["error"] not in ("not_found", "incomplete_input")
    task_svc.get.assert_awaited_with(task_id)
