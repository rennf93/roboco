"""Agent-lane Decisions verbs: preflight_diff and triage_failure (spec
6.4/6.5). Covers state composition, the advisory envelope shape, and the
graceful no-verdict path when Decisions is off/unreachable."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import roboco.services.decisions as decisions
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps


def _deps(**overrides) -> ContentActionsDeps:
    task = overrides.get("task", AsyncMock())
    git = overrides.get("git", AsyncMock())
    git.diff_and_files.return_value = ("", [])
    return ContentActionsDeps(
        task=task,
        git=git,
        a2a=AsyncMock(),
        journal=AsyncMock(),
        workspace=AsyncMock(),
        notifications=AsyncMock(),
        notification_delivery=AsyncMock(),
        evidence_repo=AsyncMock(),
        orchestrator=None,
    )


def _task(**kwargs):
    t = MagicMock()
    t.id = uuid4()
    t.branch_name = "feature/backend/ABC12345--DEF67890"
    t.assigned_to = uuid4()
    t.acceptance_criteria = ["CI green", "docs updated"]
    t.status = "in_progress"
    defaults = {"assigned_to": None, "status": "in_progress"}
    for k, v in {**defaults, **kwargs}.items():
        setattr(t, k, v)
    return t


@pytest.mark.asyncio
async def test_preflight_diff_advisory_envelope(monkeypatch):
    agent_id = uuid4()
    t = _task(assigned_to=agent_id)
    deps = _deps(task=AsyncMock())
    deps.task.get.return_value = t
    deps.git.diff_and_files.return_value = (
        "diff --git a/x.py b/x.py\n+code",
        ["x.py"],
    )
    monkeypatch.setattr(
        decisions,
        "preflight_diff",
        AsyncMock(
            return_value={
                "criteria": [
                    {"criterion": "CI green", "addresses": False, "noul": 0.2,
                     "confidence": 0.9},
                    {"criterion": "docs updated", "addresses": True, "noul": 0.9,
                     "confidence": 0.9},
                ],
                "hygiene": {"flagged": True, "noul": 0.9, "confidence": 0.9},
            }
        ),
    )
    ca = ContentActions(deps)
    env = await ca.preflight_diff(agent_id=agent_id, task_id=t.id)
    body = env.as_dict()
    assert body["status"] == "preflight"
    assert body["evidence"]["advisory"] is True
    assert "re-check" in body["next"]
    assert "hygiene" in body["next"]
    # State composition went through the agent's own branch.
    assert deps.git.diff_and_files.await_args.kwargs["branch_name"] == t.branch_name


@pytest.mark.asyncio
async def test_preflight_diff_no_verdict_is_graceful(monkeypatch):
    agent_id = uuid4()
    t = _task(assigned_to=agent_id)
    deps = _deps(task=AsyncMock())
    deps.task.get.return_value = t
    deps.git.diff_and_files.return_value = ("diff ...", ["x.py"])
    monkeypatch.setattr(decisions, "preflight_diff", AsyncMock(return_value=None))
    ca = ContentActions(deps)
    env = await ca.preflight_diff(agent_id=agent_id, task_id=t.id)
    body = env.as_dict()
    assert body["status"] == "no_verdict"
    assert "proceed exactly as usual" in body["next"]


@pytest.mark.asyncio
async def test_preflight_diff_requires_ownership():
    t = _task(assigned_to=uuid4())  # someone else's task
    deps = _deps(task=AsyncMock())
    deps.task.get.return_value = t
    ca = ContentActions(deps)
    env = await ca.preflight_diff(agent_id=uuid4(), task_id=t.id)
    assert env.as_dict()["error"] is not None


@pytest.mark.asyncio
async def test_preflight_diff_empty_diff_is_invalid_state(monkeypatch):
    agent_id = uuid4()
    t = _task(assigned_to=agent_id)
    deps = _deps(task=AsyncMock())
    deps.task.get.return_value = t
    deps.git.diff_and_files.return_value = ("", [])
    ca = ContentActions(deps)
    env = await ca.preflight_diff(agent_id=agent_id, task_id=t.id)
    assert env.as_dict()["error"] == "invalid_state"


@pytest.mark.asyncio
async def test_triage_failure_lane_envelope(monkeypatch):
    t = _task()
    deps = _deps(task=AsyncMock())
    deps.task.get.return_value = t
    deps.git.diff_and_files.return_value = ("diff ...", ["a.py", "b.py"])
    monkeypatch.setattr(
        decisions,
        "triage_failure",
        AsyncMock(return_value=decisions.TriageLane.FLAKY),
    )
    ca = ContentActions(deps)
    env = await ca.triage_failure(
        agent_id=uuid4(), task_id=t.id, test_name="test_x", error_excerpt="boom"
    )
    body = env.as_dict()
    assert body["status"] == "flaky"
    assert body["evidence"]["advisory"] is True
    assert body["evidence"]["changed_files_in_diff"] == ["a.py", "b.py"]
    assert "not a waiver" in body["next"]


@pytest.mark.asyncio
async def test_triage_failure_unknown_lane_tells_agent_to_debug(monkeypatch):
    t = _task()
    deps = _deps(task=AsyncMock())
    deps.task.get.return_value = t
    monkeypatch.setattr(
        decisions,
        "triage_failure",
        AsyncMock(return_value=decisions.TriageLane.UNKNOWN),
    )
    ca = ContentActions(deps)
    env = await ca.triage_failure(
        agent_id=uuid4(), task_id=t.id, test_name="test_x", error_excerpt=""
    )
    body = env.as_dict()
    assert body["status"] == "unknown"
    assert "debug" in body["next"]


@pytest.mark.asyncio
async def test_triage_failure_git_failure_still_triages(monkeypatch):
    """A git leg failure degrades changed_files to [] instead of failing
    the verb: the triage lane is advisory and must not hard-fail."""
    t = _task()
    deps = _deps(task=AsyncMock())
    deps.task.get.return_value = t
    deps.git.diff_and_files.side_effect = RuntimeError("git down")
    captured = {}

    async def fake_triage(session, **kwargs):
        captured.update(kwargs)
        return decisions.TriageLane.ENVIRONMENT

    monkeypatch.setattr(decisions, "triage_failure", fake_triage)
    ca = ContentActions(deps)
    env = await ca.triage_failure(
        agent_id=uuid4(), task_id=t.id, test_name="t", error_excerpt="e"
    )
    assert env.as_dict()["status"] == "environment"
    assert captured["changed_files_in_diff"] == []
