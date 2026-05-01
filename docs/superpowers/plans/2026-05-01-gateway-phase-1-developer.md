# Agent Gateway — Phase 1: Developer Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Prerequisite:** Phase 0 (`docs/superpowers/plans/2026-05-01-gateway-phase-0-foundations.md`) is merged. Tag `phase-0-foundations-complete` exists. Gateway services and SDK shim manifest loading are in place behind `ROBOCO_GATEWAY_ENABLED=false`.

**Goal:** Cut developers over to the new gateway. Implement dev intent verbs (`give_me_work`, `i_will_work_on`, `i_have_committed`, `i_am_done`, `i_am_blocked`, `i_am_idle`) + dev-applicable content tools (`commit`, `note`, `say`, `dm`, `evidence`). Ship `roboco-flow` and `roboco-do` MCP servers. Rewrite the developer role prompt. Delete old developer-only MCP tools. Set `ROBOCO_GATEWAY_ENABLED=true` for developer agents only. Run smoke test end-to-end through the new dev workflow.

**Architecture:** New thin MCP servers (`roboco/mcp/flow_server.py`, `roboco/mcp/do_server.py`) call into orchestrator endpoints `/api/v2/flow/*` and `/api/v2/do/*`. Endpoints delegate to `Choreographer` methods (filling in the Phase 1 stubs from Phase 0). Cross-role coordination during transition (dev on new flow, QA on old flow) happens via the shared `tasks` table — both code paths read/write the same rows. No protocol bridges needed.

**Tech Stack:** FastAPI, FastMCP, asyncpg/SQLAlchemy, pytest, structlog. Reuses every service from Phase 0's reuse map (§4 of spec).

---

## File Structure

**Create**:
- `roboco/api/routes/v2/__init__.py`
- `roboco/api/routes/v2/flow_dev.py` — dev intent-verb endpoints
- `roboco/api/routes/v2/do.py` — content-tool endpoints
- `roboco/api/schemas/v2/__init__.py`
- `roboco/api/schemas/v2/flow.py` — request/response models
- `roboco/api/schemas/v2/do.py`
- `roboco/mcp/flow_server.py` — `roboco-flow` MCP server (dev verbs registered)
- `roboco/mcp/do_server.py` — `roboco-do` MCP server (content tools)
- `tests/unit/gateway/test_choreographer_dev.py` — full dev-verb test suite
- `tests/integration/v2/__init__.py`
- `tests/integration/v2/test_flow_dev.py` — end-to-end against test DB
- `tests/integration/v2/test_do.py`

**Modify**:
- `roboco/services/gateway/choreographer.py` — fill in Phase 1 verb bodies
- `roboco/api/__init__.py` / `roboco/api/app.py` — mount `/api/v2/*` routers
- `roboco/mcp/task_server.py` — REMOVE developer-only tools (`roboco_task_claim`, `roboco_task_unclaim`, `roboco_task_plan`, `roboco_task_start`, `roboco_task_progress`, `roboco_task_pause`, `roboco_task_block`, `roboco_task_unblock` for devs, `roboco_task_submit_verification`, `roboco_task_submit_qa`)
- `roboco/mcp/journal_server.py` — REMOVE old journal tools (`roboco_journal_entry`, `roboco_journal_decision`, `roboco_journal_learning`, `roboco_journal_struggle`, `roboco_journal_reflect`, `roboco_journal_search`, `roboco_journal_recent`) — superseded by `note()` in `roboco-do`
- `roboco/mcp/message_server.py` — REMOVE `roboco_message_send`, `roboco_channel_history` for devs (replaced by `say()`); keep server file because non-dev roles still use it until their phase
- `roboco/mcp/git/` — REMOVE `roboco_git_create_pr`, `roboco_git_pr_merge` for direct agent use (gateway uses internally). Keep read-only git tools.
- `roboco/mcp/notify_server.py` — REMOVE `roboco_notify_list`, `roboco_notify_ack` (replaced by inline context_briefing)
- `roboco/mcp/a2a_server.py` — REMOVE `roboco_agent_request`, `roboco_a2a_check` (replaced by `dm()`)
- `agents/prompts/roles/developer.md` — slim rewrite (~15 lines)
- `agents/prompts/base.md` — minor trim (no more state machine table; gateway-aware)
- `roboco/runtime/orchestrator.py` — when spawning developers, write the spawn manifest JSON file before container start; set `ROBOCO_GATEWAY_ENABLED=true` and `ROBOCO_TOOL_MANIFEST_PATH` in env for dev containers
- `docker/agent-base.Dockerfile` and `docker/agent-dev-be.Dockerfile` — mount point for `/app/tool-manifest.json`
- `docker-compose.yml` — pass through gateway flag for dev agents

---

## Task 1: Choreographer — `give_me_work`

**Files:**
- Modify: `roboco/services/gateway/choreographer.py`
- Test: `tests/unit/gateway/test_choreographer_dev.py`

- [ ] **Step 1.1: Write the failing test**

```python
# tests/unit/gateway/test_choreographer_dev.py
"""Tests for the developer choreographer methods."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from roboco.services.gateway.choreographer import Choreographer
from roboco.services.gateway.envelope import Envelope


@pytest.fixture
def make_choreographer():
    def _make(**overrides):
        return Choreographer(
            task=overrides.get("task", AsyncMock()),
            work_session=overrides.get("work_session", AsyncMock()),
            git=overrides.get("git", AsyncMock()),
            a2a=overrides.get("a2a", AsyncMock()),
            journal=overrides.get("journal", AsyncMock()),
            audit=overrides.get("audit", AsyncMock()),
            evidence_repo=overrides.get("evidence_repo", AsyncMock()),
        )
    return _make


@pytest.mark.asyncio
async def test_give_me_work_returns_assigned_task(make_choreographer):
    agent_id = uuid4()
    task_obj = MagicMock(id=uuid4(), status="pending", title="t1")
    task_svc = AsyncMock()
    task_svc.list_assigned_for_agent.return_value = [task_obj]
    c = make_choreographer(task=task_svc)

    env = await c.give_me_work(agent_id)
    body = env.as_dict()
    assert body["status"] == "pending"
    assert body["task_id"] == str(task_obj.id)
    assert "claim" in body["next"].lower() or "i_will_work_on" in body["next"]


@pytest.mark.asyncio
async def test_give_me_work_returns_idle_when_no_work(make_choreographer):
    agent_id = uuid4()
    task_svc = AsyncMock()
    task_svc.list_assigned_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.list_available_for_team.return_value = []
    c = make_choreographer(task=task_svc)

    env = await c.give_me_work(agent_id)
    body = env.as_dict()
    assert body["status"] == "idle"
    assert "i_am_idle" in body["next"]
```

- [ ] **Step 1.2: Run test — expect FAIL (NotImplementedError)**

Run: `uv run pytest tests/unit/gateway/test_choreographer_dev.py::test_give_me_work_returns_assigned_task -v`

- [ ] **Step 1.3: Update Choreographer constructor and implement `give_me_work`**

In `roboco/services/gateway/choreographer.py`:

```python
# Add to imports
from roboco.services.gateway.evidence_builder import build_context_briefing
from roboco.services.gateway.envelope import Envelope


# Update __init__ to accept additional services
class Choreographer:
    def __init__(
        self,
        *,
        task,
        work_session,
        git,
        a2a,
        journal,
        audit,
        evidence_repo,  # repository wrapper for unread A2A, mentions, etc.
    ) -> None:
        self.task = task
        self.work_session = work_session
        self.git = git
        self.a2a = a2a
        self.journal = journal
        self.audit = audit
        self.evidence_repo = evidence_repo

    async def give_me_work(self, agent_id):
        # Priority order: assigned > paused > available
        assigned = await self.task.list_assigned_for_agent(agent_id)
        if assigned:
            t = assigned[0]
            briefing = await self._briefing_for(agent_id, t.id)
            return Envelope.ok(
                status=str(t.status),
                task_id=str(t.id),
                next=f"call i_will_work_on(task_id='{t.id}', plan='<plan>') to start",
                evidence=None,
                context_briefing=briefing,
            )
        paused = await self.task.list_paused_for_agent(agent_id)
        if paused:
            t = paused[0]
            briefing = await self._briefing_for(agent_id, t.id)
            return Envelope.ok(
                status=str(t.status),
                task_id=str(t.id),
                next=f"call i_will_work_on(task_id='{t.id}') to resume",
                evidence=None,
                context_briefing=briefing,
            )
        # No work
        briefing = await self._briefing_for(agent_id, None)
        return Envelope.ok(
            status="idle",
            task_id=None,
            next="call i_am_idle() — no work available",
            context_briefing=briefing,
        )

    async def _briefing_for(self, agent_id, task_id):
        unread_a2a = await self.evidence_repo.list_unread_a2a(agent_id)
        unread_mentions = await self.evidence_repo.list_unread_mentions(agent_id)
        pending_notif = await self.evidence_repo.list_pending_notifications(agent_id)
        gaps = []
        if task_id is not None:
            gaps = await self.evidence_repo.task_metadata_gaps(task_id)
        team_recent = await self.evidence_repo.recent_team_activity(agent_id)
        blockers = await self.evidence_repo.blockers_in_lane(agent_id)
        return build_context_briefing(
            unread_a2a=unread_a2a,
            unread_mentions=unread_mentions,
            pending_notifications=pending_notif,
            task_metadata_gaps=gaps,
            recent_team_activity=team_recent,
            blockers_in_my_lane=blockers,
        )
```

- [ ] **Step 1.4: Run tests — expect PASS**

Run: `uv run pytest tests/unit/gateway/test_choreographer_dev.py::test_give_me_work_returns_assigned_task tests/unit/gateway/test_choreographer_dev.py::test_give_me_work_returns_idle_when_no_work -v`
Expected: 2 pass.

- [ ] **Step 1.5: Commit**

```bash
git add roboco/services/gateway/choreographer.py tests/unit/gateway/test_choreographer_dev.py
git commit -m "feat(gateway): implement Choreographer.give_me_work with priority order and context briefing"
```

---

## Task 2: Choreographer — `i_will_work_on` (handles pending, claimed, needs_revision)

**Files:**
- Modify: `roboco/services/gateway/choreographer.py`
- Test: `tests/unit/gateway/test_choreographer_dev.py`

- [ ] **Step 2.1: Write the failing tests**

Append to `test_choreographer_dev.py`:

```python
@pytest.mark.asyncio
async def test_i_will_work_on_pending_with_plan(make_choreographer):
    agent_id = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="pending", plan=None, assigned_to=None)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.claim.return_value = t
    task_svc.set_plan.return_value = t
    task_svc.start.return_value = MagicMock(id=task_id, status="in_progress", plan={"text": "do x"})
    c = make_choreographer(task=task_svc)

    env = await c.i_will_work_on(agent_id, task_id, plan="do x then y")
    assert env.error is None
    assert env.status == "in_progress"
    task_svc.claim.assert_awaited_once_with(agent_id, task_id)
    task_svc.set_plan.assert_awaited_once()
    task_svc.start.assert_awaited_once_with(agent_id, task_id)


@pytest.mark.asyncio
async def test_i_will_work_on_pending_no_plan_returns_remediate(make_choreographer):
    agent_id = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="pending", plan=None, assigned_to=None,
                  description="task description")
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    c = make_choreographer(task=task_svc)

    env = await c.i_will_work_on(agent_id, task_id, plan=None)
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "plan" in body["missing"]
    assert "i_will_work_on" in body["remediate"]


@pytest.mark.asyncio
async def test_i_will_work_on_needs_revision_re_starts(make_choreographer):
    agent_id = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="needs_revision", assigned_to=agent_id, plan={"x": 1})
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.start.return_value = MagicMock(id=task_id, status="in_progress")
    c = make_choreographer(task=task_svc)

    env = await c.i_will_work_on(agent_id, task_id)
    assert env.status == "in_progress"
    # Should NOT call claim (already assigned)
    task_svc.claim.assert_not_awaited()
    task_svc.start.assert_awaited_once_with(agent_id, task_id)
```

- [ ] **Step 2.2: Run test — expect FAIL**

Run: `uv run pytest tests/unit/gateway/test_choreographer_dev.py -v -k "i_will_work_on"`

- [ ] **Step 2.3: Implement**

Add to `Choreographer` in `choreographer.py`:

```python
async def i_will_work_on(self, agent_id, task_id, plan=None):
    t = await self.task.get(task_id)
    if t is None:
        return Envelope.not_found(message=f"task {task_id} not found")
    status = str(t.status)

    if status == "needs_revision":
        if t.assigned_to != agent_id:
            t = await self.task.claim(agent_id, task_id)
        t = await self.task.start(agent_id, task_id)
    elif status == "pending":
        if t.assigned_to is None or t.assigned_to != agent_id:
            t = await self.task.claim(agent_id, task_id)
        if not t.plan and not plan:
            return Envelope.tracing_gap(
                missing=["plan"],
                remediate=(
                    f"call i_will_work_on(task_id='{task_id}', plan='<one-paragraph "
                    f"plan describing what you will do>')"
                ),
                context_briefing=await self._briefing_for(agent_id, task_id),
            )
        if plan:
            t = await self.task.set_plan(task_id, plan)
        t = await self.task.start(agent_id, task_id)
    elif status == "claimed" and t.assigned_to == agent_id:
        t = await self.task.start(agent_id, task_id)
    else:
        return Envelope.invalid_state(
            message=f"task {task_id} is in {status}; cannot start work",
            remediate="call give_me_work() to find an actionable task",
            context_briefing=await self._briefing_for(agent_id, task_id),
        )

    return Envelope.ok(
        status=str(t.status),
        task_id=str(task_id),
        next="edit + commit; call i_have_committed when ready, or i_am_done when finished",
        context_briefing=await self._briefing_for(agent_id, task_id),
    )
```

- [ ] **Step 2.4: Run tests — expect PASS**

Run: `uv run pytest tests/unit/gateway/test_choreographer_dev.py -v -k "i_will_work_on"`

- [ ] **Step 2.5: Commit**

```bash
git add roboco/services/gateway/choreographer.py tests/unit/gateway/test_choreographer_dev.py
git commit -m "feat(gateway): implement i_will_work_on handling pending, claimed, and needs_revision recovery"
```

---

## Task 3: Choreographer — `i_have_committed`

**Files:**
- Modify: `roboco/services/gateway/choreographer.py`
- Test: append to `tests/unit/gateway/test_choreographer_dev.py`

- [ ] **Step 3.1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_i_have_committed_records_progress(make_choreographer):
    agent_id = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="in_progress", assigned_to=agent_id, plan={"x": 1})
    task_svc = AsyncMock()
    task_svc.get_active_task_for_agent.return_value = t
    task_svc.add_progress.return_value = t
    c = make_choreographer(task=task_svc)

    env = await c.i_have_committed(agent_id, "feat(api): add /healthz endpoint")
    assert env.error is None
    task_svc.add_progress.assert_awaited_once()
```

- [ ] **Step 3.2: Run test — expect FAIL**

- [ ] **Step 3.3: Implement**

```python
async def i_have_committed(self, agent_id, message):
    t = await self.task.get_active_task_for_agent(agent_id)
    if t is None:
        return Envelope.invalid_state(
            message="no active task for this agent",
            remediate="call give_me_work() then i_will_work_on(task_id, plan)",
            context_briefing=await self._briefing_for(agent_id, None),
        )
    if not t.plan:
        return Envelope.tracing_gap(
            missing=["plan"],
            remediate=f"plan must be set first; call i_will_work_on(task_id='{t.id}', plan='...')",
            context_briefing=await self._briefing_for(agent_id, t.id),
        )
    await self.task.add_progress(t.id, agent_id, message)
    return Envelope.ok(
        status=str(t.status),
        task_id=str(t.id),
        next="continue working, or i_am_done when finished",
        context_briefing=await self._briefing_for(agent_id, t.id),
    )
```

- [ ] **Step 3.4: Run tests — expect PASS**

- [ ] **Step 3.5: Commit**

```bash
git add roboco/services/gateway/choreographer.py tests/unit/gateway/test_choreographer_dev.py
git commit -m "feat(gateway): implement i_have_committed with plan-required precondition"
```

---

## Task 4: Choreographer — `i_am_done` (smart catch-up)

**Files:**
- Modify: `roboco/services/gateway/choreographer.py`
- Test: append to `tests/unit/gateway/test_choreographer_dev.py`

- [ ] **Step 4.1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_i_am_done_full_catch_up(make_choreographer):
    agent_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id, status="in_progress", assigned_to=agent_id,
        plan={"x": 1}, branch_name="feature/backend/abc--def",
        work_session_id=uuid4(), self_verified=False, pr_number=None, pr_url=None,
        team="backend",
        progress_updates=[{"message": "did x"}],
        acceptance_criteria=["AC1"],
        acceptance_criteria_status=[{"criterion": "AC1", "referencing_artifact_id": "c1"}],
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.submit_verification.return_value = MagicMock(**{**t.__dict__, "self_verified": True, "status": "verifying"})
    task_svc.submit_qa.return_value = MagicMock(**{**t.__dict__, "status": "awaiting_qa", "pr_number": 8, "pr_url": "https://x/pr/8"})
    task_svc.qa_agent_for_team.return_value = MagicMock(id=uuid4(), skills=[{"id": "code_review"}])

    ws_svc = AsyncMock()
    ws_svc.has_unpushed_commits.return_value = True

    git_svc = AsyncMock()
    git_svc.create_pr.return_value = {"pr_number": 8, "pr_url": "https://x/pr/8"}

    a2a_svc = AsyncMock()

    journal_svc = AsyncMock()
    journal_svc.has_reflect_for_task.return_value = True

    c = make_choreographer(task=task_svc, work_session=ws_svc, git=git_svc,
                            a2a=a2a_svc, journal=journal_svc)

    env = await c.i_am_done(agent_id, task_id, "all done")
    assert env.error is None
    assert env.status == "awaiting_qa"
    git_svc.push.assert_awaited_once_with("feature/backend/abc--def")
    git_svc.create_pr.assert_awaited_once()
    a2a_svc.send.assert_awaited_once()
    body = env.as_dict()
    assert body["evidence"]["pr_url"] == "https://x/pr/8"


@pytest.mark.asyncio
async def test_i_am_done_blocks_when_acceptance_criteria_unaddressed(make_choreographer):
    agent_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id, status="in_progress", assigned_to=agent_id,
        plan={"x": 1}, branch_name="feature/backend/abc",
        work_session_id=uuid4(), self_verified=False,
        progress_updates=[{"message": "p"}],
        acceptance_criteria=["AC1", "AC2"],
        acceptance_criteria_status=[{"criterion": "AC1", "referencing_artifact_id": "c1"}],
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    journal_svc = AsyncMock()
    journal_svc.has_reflect_for_task.return_value = True
    c = make_choreographer(task=task_svc, journal=journal_svc)

    env = await c.i_am_done(agent_id, task_id, "done")
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert any("AC2" in m for m in body["missing"])
```

- [ ] **Step 4.2: Run test — expect FAIL**

- [ ] **Step 4.3: Implement**

```python
from roboco.services.gateway.tracing_gate import Requirement, check_requirements
from roboco.services.gateway.evidence_builder import build_evidence_for_task
from roboco.services.gateway.merge_chain import parent_branch_for


async def i_am_done(self, agent_id, task_id, notes):
    t = await self.task.get(task_id)
    if t is None:
        return Envelope.not_found(message=f"task {task_id} not found")
    if t.assigned_to != agent_id:
        return Envelope.not_authorized(
            message="not assigned to you",
            remediate="claim it via i_will_work_on(task_id) first",
            context_briefing=await self._briefing_for(agent_id, task_id),
        )

    has_reflect = await self.journal.has_reflect_for_task(agent_id, task_id)
    gate = check_requirements(
        t,
        [
            Requirement.PROGRESS_AT_LEAST_ONE,
            Requirement.JOURNAL_REFLECT,
            Requirement.ACCEPTANCE_CRITERIA_ADDRESSED,
        ],
        journal_reflect_present=has_reflect,
    )
    if not gate.passed:
        from roboco.services.gateway.remediation import (
            hint_for_missing_progress,
            hint_for_missing_reflect,
            hint_for_unaddressed_acceptance_criteria,
        )
        hints: list[str] = []
        for m in gate.missing:
            if m == "progress>=1":
                hints.append(hint_for_missing_progress())
            elif m == "journal:reflect":
                hints.append(hint_for_missing_reflect(task_id=str(task_id)))
            elif m.startswith("acceptance_criterion:"):
                pass  # collected below
        unaddressed_criteria = [m.split(":", 1)[1] for m in gate.missing if m.startswith("acceptance_criterion:")]
        if unaddressed_criteria:
            hints.append(hint_for_unaddressed_acceptance_criteria(
                criteria=unaddressed_criteria, task_id=str(task_id)
            ))
        return Envelope.tracing_gap(
            missing=gate.missing,
            remediate=" ; ".join(hints),
            context_briefing=await self._briefing_for(agent_id, task_id),
        )

    # Smart catch-up: verification, push, PR, submit_qa
    if not t.self_verified:
        t = await self.task.submit_verification(agent_id, task_id, notes)

    has_unpushed = await self.work_session.has_unpushed_commits(t.work_session_id)
    if has_unpushed:
        await self.git.push(t.branch_name)

    if t.pr_number is None:
        parent = parent_branch_for(t.branch_name)
        await self.git.create_pr(t.branch_name, parent=parent, is_root_pr=False)
        t = await self.task.get(task_id)  # refresh

    t = await self.task.submit_qa(agent_id, task_id, notes)

    # Auto-A2A to QA agent
    qa_agent = await self.task.qa_agent_for_team(t.team)
    if qa_agent is not None:
        skill = self._resolve_skill(qa_agent, ["code_review", "qa_review"])
        await self.a2a.send(
            from_agent=agent_id,
            to_agent=qa_agent.id,
            skill=skill,
            task_id=task_id,
            body=f"Ready for review. PR: {t.pr_url}",
        )

    journal_highlights = await self.evidence_repo.journal_highlights_for_task(task_id)
    files_changed = await self.work_session.files_changed(t.work_session_id) if t.work_session_id else []
    evidence = build_evidence_for_task(
        t,
        journal_highlights=journal_highlights,
        files_changed=files_changed,
    )
    return Envelope.ok(
        status=str(t.status),
        task_id=str(task_id),
        next="idle until QA responds",
        evidence=evidence.as_dict(),
        context_briefing=await self._briefing_for(agent_id, task_id),
    )


def _resolve_skill(self, target_agent, preference: list[str]) -> str:
    """Pick the first skill in `preference` that target_agent actually has.

    Falls back to first item if no match (caller may receive SKILL_NOT_FOUND
    from the underlying API, which is acceptable as a last-resort signal).
    """
    have = {s.get("id") if isinstance(s, dict) else s for s in (target_agent.skills or [])}
    for skill in preference:
        if skill in have:
            return skill
    return preference[0]
```

- [ ] **Step 4.4: Run tests — expect PASS**

- [ ] **Step 4.5: Commit**

```bash
git add roboco/services/gateway/choreographer.py tests/unit/gateway/test_choreographer_dev.py
git commit -m "feat(gateway): implement i_am_done with smart catch-up and skill resolution"
```

---

## Task 5: Choreographer — `i_am_blocked` and `i_am_idle`

**Files:**
- Modify: `roboco/services/gateway/choreographer.py`
- Test: append to `tests/unit/gateway/test_choreographer_dev.py`

- [ ] **Step 5.1: Write tests**

```python
@pytest.mark.asyncio
async def test_i_am_blocked_escalates_and_journals(make_choreographer):
    agent_id = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="in_progress", assigned_to=agent_id,
                  pre_block_state=None)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.escalate.return_value = MagicMock(**{**t.__dict__, "status": "blocked"})
    journal_svc = AsyncMock()
    c = make_choreographer(task=task_svc, journal=journal_svc)

    env = await c.i_am_blocked(agent_id, task_id, "external API down")
    assert env.status == "blocked"
    journal_svc.write_struggle.assert_awaited_once()
    task_svc.escalate.assert_awaited_once()


@pytest.mark.asyncio
async def test_i_am_idle_with_unread_a2a_soft_blocks(make_choreographer):
    agent_id = uuid4()
    evidence_repo = AsyncMock()
    evidence_repo.list_unread_a2a.return_value = [{"from": "x", "task_id": "t1"}]
    evidence_repo.list_unread_mentions.return_value = []
    evidence_repo.list_pending_notifications.return_value = []
    evidence_repo.task_metadata_gaps.return_value = []
    evidence_repo.recent_team_activity.return_value = []
    evidence_repo.blockers_in_lane.return_value = []
    c = make_choreographer(evidence_repo=evidence_repo)

    env = await c.i_am_idle(agent_id)
    body = env.as_dict()
    # Soft block via context_briefing — verb still returns ok but next is "address A2As"
    assert "a2a" in body["next"].lower() or "address" in body["next"].lower()


@pytest.mark.asyncio
async def test_i_am_idle_clean_returns_idle(make_choreographer):
    agent_id = uuid4()
    evidence_repo = AsyncMock()
    evidence_repo.list_unread_a2a.return_value = []
    evidence_repo.list_unread_mentions.return_value = []
    evidence_repo.list_pending_notifications.return_value = []
    evidence_repo.task_metadata_gaps.return_value = []
    evidence_repo.recent_team_activity.return_value = []
    evidence_repo.blockers_in_lane.return_value = []
    c = make_choreographer(evidence_repo=evidence_repo)

    env = await c.i_am_idle(agent_id)
    assert env.status == "idle"
```

- [ ] **Step 5.2: Run test — expect FAIL**

- [ ] **Step 5.3: Implement**

```python
async def i_am_blocked(self, agent_id, task_id, reason):
    t = await self.task.get(task_id)
    if t is None:
        return Envelope.not_found(message=f"task {task_id} not found")
    await self.journal.write_struggle(agent_id=agent_id, task_id=task_id, content=reason)
    t = await self.task.escalate(agent_id, task_id, reason)
    return Envelope.ok(
        status=str(t.status),
        task_id=str(task_id),
        next="idle — PM will resolve and notify",
        context_briefing=await self._briefing_for(agent_id, task_id),
    )


async def i_am_idle(self, agent_id):
    briefing = await self._briefing_for(agent_id, None)
    if briefing.get("unread_a2a") or briefing.get("unread_mentions"):
        return Envelope.ok(
            status="idle_with_unread",
            task_id=None,
            next="address unread A2A and @mentions in context_briefing before going idle",
            context_briefing=briefing,
        )
    await self.task.mark_agent_idle(agent_id)
    return Envelope.ok(
        status="idle",
        task_id=None,
        next="container will shut down",
        context_briefing=briefing,
    )
```

- [ ] **Step 5.4: Run tests — expect PASS**

- [ ] **Step 5.5: Commit**

```bash
git add roboco/services/gateway/choreographer.py tests/unit/gateway/test_choreographer_dev.py
git commit -m "feat(gateway): implement i_am_blocked (struggle + escalate) and i_am_idle (with unread soft-block)"
```

---

## Task 6: Content tools — `commit`, `note`, `say`, `dm`, `evidence`

**Files:**
- Add a new module: `roboco/services/gateway/content_actions.py`
- Test: `tests/unit/gateway/test_content_actions.py`

- [ ] **Step 6.1: Write tests for `commit` (uses commit_validator)**

```python
# tests/unit/gateway/test_content_actions.py
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from roboco.services.gateway.content_actions import ContentActions
from roboco.services.gateway.envelope import Envelope


@pytest.fixture
def make_content():
    def _make(**overrides):
        return ContentActions(
            task=overrides.get("task", AsyncMock()),
            git=overrides.get("git", AsyncMock()),
            messaging=overrides.get("messaging", AsyncMock()),
            a2a=overrides.get("a2a", AsyncMock()),
            journal=overrides.get("journal", AsyncMock()),
            workspace=overrides.get("workspace", AsyncMock()),
        )
    return _make


@pytest.mark.asyncio
async def test_commit_short_message_rejected(make_content):
    c = make_content()
    env = await c.commit(agent_id=uuid4(), message="wip")
    assert env.error == "invalid_state" or env.error == "tracing_gap"
    assert env.remediate is not None


@pytest.mark.asyncio
async def test_commit_descriptive_succeeds(make_content):
    task_svc = AsyncMock()
    t = MagicMock(id=uuid4(), branch_name="feature/be/abc", work_session_id=uuid4())
    task_svc.get_active_task_for_agent.return_value = t
    git_svc = AsyncMock()
    git_svc.commit.return_value = {"sha": "abc123"}
    c = make_content(task=task_svc, git=git_svc)

    env = await c.commit(
        agent_id=uuid4(),
        message="feat(api): add /healthz endpoint with timeout config",
    )
    assert env.error is None
    git_svc.commit.assert_awaited_once()
    task_svc.add_progress.assert_awaited_once()
```

- [ ] **Step 6.2: Run test — expect FAIL**

- [ ] **Step 6.3: Implement `ContentActions`**

```python
# roboco/services/gateway/content_actions.py
"""Smart-wrapped content tools — commit, note, say, dm, evidence.

Each method:
1. Validates input (e.g., commit_validator for commit messages).
2. Auto-injects task_id when the agent has an active claim and the param is missing.
3. Calls the underlying service.
4. Returns a standardized Envelope.

Pure orchestration; no DB writes outside what the underlying services do.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from roboco.services.gateway.commit_validator import validate_commit_message
from roboco.services.gateway.envelope import Envelope


class ContentActions:
    def __init__(
        self,
        *,
        task,
        git,
        messaging,
        a2a,
        journal,
        workspace,
    ) -> None:
        self.task = task
        self.git = git
        self.messaging = messaging
        self.a2a = a2a
        self.journal = journal
        self.workspace = workspace

    async def commit(self, *, agent_id: UUID, message: str, files: list[str] | None = None) -> Envelope:
        # Strip any pre-existing [task-id] prefix the agent might have included
        subject = _strip_task_prefix(message).strip()
        result = validate_commit_message(subject)
        if not result.ok:
            return Envelope.invalid_state(
                message=result.reason or "commit message invalid",
                remediate=result.remediate or "",
                context_briefing={},
            )
        t = await self.task.get_active_task_for_agent(agent_id)
        if t is None:
            return Envelope.invalid_state(
                message="no active task; cannot commit",
                remediate="call give_me_work() first",
                context_briefing={},
            )
        commit_result = await self.git.commit(
            branch_name=t.branch_name,
            message=subject,
            task_id=t.id,
            files=files,
        )
        sha = commit_result.get("sha", "")
        await self.task.add_progress(t.id, agent_id, f"committed {sha[:8]}: {subject}")
        return Envelope.ok(
            status=str(t.status),
            task_id=str(t.id),
            next="continue, then i_have_committed or i_am_done",
            context_briefing={},
        )

    async def note(
        self, *, agent_id: UUID, text: str, scope: str = "note", task_id: UUID | None = None
    ) -> Envelope:
        valid_scopes = {"note", "decision", "reflect", "learning", "struggle"}
        if scope not in valid_scopes:
            return Envelope.invalid_state(
                message=f"invalid scope {scope!r}",
                remediate=f"scope must be one of: {sorted(valid_scopes)}",
                context_briefing={},
            )
        if task_id is None:
            t = await self.task.get_active_task_for_agent(agent_id)
            task_id = t.id if t is not None else None
        title = text.split("\n", 1)[0][:200]
        await self.journal.write_entry(
            agent_id=agent_id,
            task_id=task_id,
            scope=scope,
            title=title,
            content=text,
        )
        return Envelope.ok(
            status="noted",
            task_id=str(task_id) if task_id else None,
            next="continue",
            context_briefing={},
        )

    async def say(
        self, *, agent_id: UUID, channel: str, text: str, task_id: UUID | None = None
    ) -> Envelope:
        if task_id is None:
            t = await self.task.get_active_task_for_agent(agent_id)
            task_id = t.id if t is not None else None
        await self.messaging.post_to_channel(
            agent_id=agent_id, channel_slug=channel, content=text, task_id=task_id
        )
        return Envelope.ok(
            status="posted",
            task_id=str(task_id) if task_id else None,
            next="continue",
            context_briefing={},
        )

    async def dm(
        self,
        *,
        agent_id: UUID,
        recipient: str,
        text: str,
        task_id: UUID | None = None,
        skill: str | None = None,
    ) -> Envelope:
        if task_id is None:
            t = await self.task.get_active_task_for_agent(agent_id)
            task_id = t.id if t is not None else None
        if task_id is None:
            return Envelope.invalid_state(
                message="dm requires a task_id (no active task and none provided)",
                remediate="provide task_id explicitly or claim a task first",
                context_briefing={},
            )
        await self.a2a.send(
            from_agent=agent_id,
            to_agent_slug=recipient,
            task_id=task_id,
            body=text,
            skill=skill,
        )
        return Envelope.ok(
            status="sent",
            task_id=str(task_id),
            next="continue",
            context_briefing={},
        )

    async def evidence(self, *, agent_id: UUID, task_id: UUID) -> Envelope:
        t = await self.task.get(task_id)
        if t is None:
            return Envelope.not_found(message=f"task {task_id} not found")
        # Optionally fetch the dev branch into the caller's workspace (read-only ref)
        if t.branch_name and t.work_session_id:
            await self.workspace.fetch_branch_for_inspection(
                agent_id=agent_id, branch_name=t.branch_name
            )
        diff = await self.git.diff(
            branch_name=t.branch_name, base="HEAD~1" if t.commits else None
        ) if t.branch_name else None
        from roboco.services.gateway.evidence_builder import build_evidence_for_task
        ev = build_evidence_for_task(
            t,
            journal_highlights=[],
            files_changed=[],
            pr_diff_summary=diff,
        )
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next="continue",
            evidence=ev.as_dict(),
            context_briefing={},
        )


def _strip_task_prefix(msg: str) -> str:
    """Strip any [task-id] prefix the agent supplied; gateway re-adds canonical prefix."""
    import re
    return re.sub(r"^\s*\[[a-zA-Z0-9_-]+\]\s*", "", msg)
```

- [ ] **Step 6.4: Run tests — expect PASS**

Run: `uv run pytest tests/unit/gateway/test_content_actions.py -v`

- [ ] **Step 6.5: Commit**

```bash
git add roboco/services/gateway/content_actions.py tests/unit/gateway/test_content_actions.py
git commit -m "feat(gateway): add ContentActions for commit, note, say, dm, evidence with auto-inject and validation"
```

---

## Task 7: API v2 — `/api/v2/flow/dev/*` endpoints

**Files:**
- Create: `roboco/api/routes/v2/__init__.py`, `roboco/api/routes/v2/flow_dev.py`
- Create: `roboco/api/schemas/v2/__init__.py`, `roboco/api/schemas/v2/flow.py`
- Modify: `roboco/api/__init__.py` (or `app.py`) to mount the v2 router

- [ ] **Step 7.1: Write the request/response schemas**

```python
# roboco/api/schemas/v2/__init__.py
```

```python
# roboco/api/schemas/v2/flow.py
"""Request schemas for /api/v2/flow/* intent verbs."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class GiveMeWorkRequest(BaseModel):
    pass  # agent_id comes from header


class IWillWorkOnRequest(BaseModel):
    task_id: UUID
    plan: str | None = None


class IHaveCommittedRequest(BaseModel):
    message: str = Field(..., min_length=1)


class IAmDoneRequest(BaseModel):
    task_id: UUID
    notes: str = Field(default="")


class IAmBlockedRequest(BaseModel):
    task_id: UUID
    reason: str = Field(..., min_length=1)


class IAmIdleRequest(BaseModel):
    pass
```

- [ ] **Step 7.2: Write the endpoints**

```python
# roboco/api/routes/v2/__init__.py
```

```python
# roboco/api/routes/v2/flow_dev.py
"""Dev intent-verb HTTP endpoints. Thin handlers; delegate to Choreographer."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header

from roboco.api.deps import get_choreographer
from roboco.api.schemas.v2.flow import (
    GiveMeWorkRequest,
    IAmBlockedRequest,
    IAmDoneRequest,
    IAmIdleRequest,
    IHaveCommittedRequest,
    IWillWorkOnRequest,
)
from roboco.services.gateway.choreographer import Choreographer

router = APIRouter(prefix="/api/v2/flow/dev", tags=["v2-flow-dev"])


@router.post("/give_me_work")
async def give_me_work(
    _: GiveMeWorkRequest,
    x_agent_id: UUID = Header(..., alias="X-Agent-ID"),
    choreographer: Choreographer = Depends(get_choreographer),
):
    env = await choreographer.give_me_work(x_agent_id)
    return env.as_dict()


@router.post("/i_will_work_on")
async def i_will_work_on(
    body: IWillWorkOnRequest,
    x_agent_id: UUID = Header(..., alias="X-Agent-ID"),
    choreographer: Choreographer = Depends(get_choreographer),
):
    env = await choreographer.i_will_work_on(x_agent_id, body.task_id, body.plan)
    return env.as_dict()


@router.post("/i_have_committed")
async def i_have_committed(
    body: IHaveCommittedRequest,
    x_agent_id: UUID = Header(..., alias="X-Agent-ID"),
    choreographer: Choreographer = Depends(get_choreographer),
):
    env = await choreographer.i_have_committed(x_agent_id, body.message)
    return env.as_dict()


@router.post("/i_am_done")
async def i_am_done(
    body: IAmDoneRequest,
    x_agent_id: UUID = Header(..., alias="X-Agent-ID"),
    choreographer: Choreographer = Depends(get_choreographer),
):
    env = await choreographer.i_am_done(x_agent_id, body.task_id, body.notes)
    return env.as_dict()


@router.post("/i_am_blocked")
async def i_am_blocked(
    body: IAmBlockedRequest,
    x_agent_id: UUID = Header(..., alias="X-Agent-ID"),
    choreographer: Choreographer = Depends(get_choreographer),
):
    env = await choreographer.i_am_blocked(x_agent_id, body.task_id, body.reason)
    return env.as_dict()


@router.post("/i_am_idle")
async def i_am_idle(
    _: IAmIdleRequest,
    x_agent_id: UUID = Header(..., alias="X-Agent-ID"),
    choreographer: Choreographer = Depends(get_choreographer),
):
    env = await choreographer.i_am_idle(x_agent_id)
    return env.as_dict()
```

- [ ] **Step 7.3: Add the dependency provider**

In `roboco/api/deps.py` (modify existing or add):

```python
async def get_choreographer(db_session=Depends(get_db_session)) -> Choreographer:
    from roboco.services.task import TaskService
    from roboco.services.work_session import WorkSessionService
    from roboco.services.git import GitService
    from roboco.services.a2a import A2AService
    from roboco.services.journal import JournalService
    from roboco.services.audit import AuditService
    from roboco.services.gateway.evidence_repo import EvidenceRepo

    return Choreographer(
        task=TaskService(db_session),
        work_session=WorkSessionService(db_session),
        git=GitService(db_session),
        a2a=A2AService(db_session),
        journal=JournalService(db_session),
        audit=AuditService(db_session),
        evidence_repo=EvidenceRepo(db_session),
    )
```

(Implementation note: `EvidenceRepo` is a thin wrapper — create it in `roboco/services/gateway/evidence_repo.py` with the list_unread_*, task_metadata_gaps, etc. methods used by `_briefing_for`. Each method composes existing services or queries `journal_entries`, `messages`, `notifications`, `tasks` via the existing repository pattern.)

- [ ] **Step 7.4: Mount the router in the FastAPI app**

In `roboco/api/__init__.py` or `app.py`:

```python
from roboco.api.routes.v2 import flow_dev

app.include_router(flow_dev.router)
```

- [ ] **Step 7.5: Integration test**

```python
# tests/integration/v2/__init__.py
```

```python
# tests/integration/v2/test_flow_dev.py
"""End-to-end integration tests for /api/v2/flow/dev/* endpoints."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_give_me_work_returns_envelope(
    client: AsyncClient, dev_agent_with_assigned_task
):
    response = await client.post(
        "/api/v2/flow/dev/give_me_work",
        headers={"X-Agent-ID": str(dev_agent_with_assigned_task.id)},
        json={},
    )
    assert response.status_code == 200
    body = response.json()
    assert "status" in body
    assert "task_id" in body
    assert "next" in body
    assert "context_briefing" in body
```

(The fixture `dev_agent_with_assigned_task` should be added to `tests/conftest.py` — pattern: factory-boy creating an Agent + Task pre-assigned to it.)

- [ ] **Step 7.6: Run tests — expect PASS**

Run: `uv run pytest tests/integration/v2/test_flow_dev.py -v`

- [ ] **Step 7.7: Commit**

```bash
git add roboco/api/routes/v2/ roboco/api/schemas/v2/ roboco/api/deps.py roboco/api/__init__.py tests/integration/v2/
git commit -m "feat(api/v2): add /api/v2/flow/dev/* endpoints delegating to Choreographer"
```

---

## Task 8: API v2 — `/api/v2/do/*` endpoints

Same pattern as Task 7 but for content actions.

**Files:**
- Create: `roboco/api/routes/v2/do.py`
- Create: `roboco/api/schemas/v2/do.py`
- Test: `tests/integration/v2/test_do.py`

- [ ] **Step 8.1: Write schemas**

```python
# roboco/api/schemas/v2/do.py
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class CommitRequest(BaseModel):
    message: str = Field(..., min_length=1)
    files: list[str] | None = None


class NoteRequest(BaseModel):
    text: str = Field(..., min_length=1)
    scope: str = "note"
    task_id: UUID | None = None


class SayRequest(BaseModel):
    channel: str
    text: str = Field(..., min_length=1)
    task_id: UUID | None = None


class DmRequest(BaseModel):
    recipient: str  # agent slug
    text: str = Field(..., min_length=1)
    task_id: UUID | None = None
    skill: str | None = None


class EvidenceRequest(BaseModel):
    task_id: UUID
```

- [ ] **Step 8.2: Write endpoints**

```python
# roboco/api/routes/v2/do.py
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header

from roboco.api.deps import get_content_actions
from roboco.api.schemas.v2.do import (
    CommitRequest, DmRequest, EvidenceRequest, NoteRequest, SayRequest,
)
from roboco.services.gateway.content_actions import ContentActions

router = APIRouter(prefix="/api/v2/do", tags=["v2-do"])


@router.post("/commit")
async def do_commit(
    body: CommitRequest,
    x_agent_id: UUID = Header(..., alias="X-Agent-ID"),
    actions: ContentActions = Depends(get_content_actions),
):
    env = await actions.commit(agent_id=x_agent_id, message=body.message, files=body.files)
    return env.as_dict()


@router.post("/note")
async def do_note(
    body: NoteRequest,
    x_agent_id: UUID = Header(..., alias="X-Agent-ID"),
    actions: ContentActions = Depends(get_content_actions),
):
    env = await actions.note(
        agent_id=x_agent_id, text=body.text, scope=body.scope, task_id=body.task_id
    )
    return env.as_dict()


@router.post("/say")
async def do_say(
    body: SayRequest,
    x_agent_id: UUID = Header(..., alias="X-Agent-ID"),
    actions: ContentActions = Depends(get_content_actions),
):
    env = await actions.say(
        agent_id=x_agent_id, channel=body.channel, text=body.text, task_id=body.task_id
    )
    return env.as_dict()


@router.post("/dm")
async def do_dm(
    body: DmRequest,
    x_agent_id: UUID = Header(..., alias="X-Agent-ID"),
    actions: ContentActions = Depends(get_content_actions),
):
    env = await actions.dm(
        agent_id=x_agent_id,
        recipient=body.recipient,
        text=body.text,
        task_id=body.task_id,
        skill=body.skill,
    )
    return env.as_dict()


@router.post("/evidence")
async def do_evidence(
    body: EvidenceRequest,
    x_agent_id: UUID = Header(..., alias="X-Agent-ID"),
    actions: ContentActions = Depends(get_content_actions),
):
    env = await actions.evidence(agent_id=x_agent_id, task_id=body.task_id)
    return env.as_dict()
```

- [ ] **Step 8.3: Add `get_content_actions` to `deps.py`** following the same pattern as `get_choreographer`.

- [ ] **Step 8.4: Mount router** in `roboco/api/__init__.py`:

```python
from roboco.api.routes.v2 import do

app.include_router(do.router)
```

- [ ] **Step 8.5: Integration test**

```python
# tests/integration/v2/test_do.py
@pytest.mark.asyncio
async def test_commit_rejects_short_message(client, dev_agent_in_progress):
    r = await client.post(
        "/api/v2/do/commit",
        headers={"X-Agent-ID": str(dev_agent_in_progress.id)},
        json={"message": "wip"},
    )
    body = r.json()
    assert body["error"] == "invalid_state"


@pytest.mark.asyncio
async def test_note_with_scope_reflect(client, dev_agent_in_progress):
    r = await client.post(
        "/api/v2/do/note",
        headers={"X-Agent-ID": str(dev_agent_in_progress.id)},
        json={"text": "I learned that ... and decided to ...", "scope": "reflect"},
    )
    assert r.status_code == 200
    assert r.json()["error"] is None
```

- [ ] **Step 8.6: Run tests — expect PASS**

Run: `uv run pytest tests/integration/v2/test_do.py -v`

- [ ] **Step 8.7: Commit**

```bash
git add roboco/api/routes/v2/do.py roboco/api/schemas/v2/do.py roboco/api/deps.py roboco/api/__init__.py tests/integration/v2/test_do.py
git commit -m "feat(api/v2): add /api/v2/do/* endpoints for commit, note, say, dm, evidence"
```

---

## Task 9: `roboco-flow` MCP server (dev verbs)

**Files:**
- Create: `roboco/mcp/flow_server.py`

- [ ] **Step 9.1: Read an existing MCP server pattern**

Run: `head -80 roboco/mcp/task_server.py`
Note the FastMCP setup, tool registration decorator, env var reading.

- [ ] **Step 9.2: Implement flow_server.py**

```python
# roboco/mcp/flow_server.py
"""roboco-flow MCP server — exposes intent verbs to agents.

Tools registered depend on the agent's role (read from manifest at startup).
Each tool forwards to /api/v2/flow/<role>/<verb> on the orchestrator.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

ORCHESTRATOR_URL = os.environ.get("ROBOCO_ORCHESTRATOR_URL", "http://roboco-orchestrator:8000")
AGENT_ID = os.environ["ROBOCO_AGENT_ID"]
AGENT_ROLE = os.environ["ROBOCO_AGENT_ROLE"]
TOOL_MANIFEST_PATH = Path(os.environ.get("ROBOCO_TOOL_MANIFEST_PATH", "/app/tool-manifest.json"))


def _load_manifest() -> dict:
    return json.loads(TOOL_MANIFEST_PATH.read_text())


_HEADERS = {"X-Agent-ID": AGENT_ID, "X-Agent-Role": AGENT_ROLE}

mcp = FastMCP("roboco-flow")


def _post(path: str, body: dict) -> dict:
    with httpx.Client(timeout=30) as c:
        r = c.post(f"{ORCHESTRATOR_URL}{path}", headers=_HEADERS, json=body)
        r.raise_for_status()
        return r.json()


def _role_path(verb: str) -> str:
    return f"/api/v2/flow/{AGENT_ROLE}/{verb}"


# ---------- Dev verbs ----------

@mcp.tool()
def give_me_work() -> dict:
    """Get your current task or report idle. Returns task + context_briefing."""
    return _post(_role_path("give_me_work"), {})


@mcp.tool()
def i_will_work_on(task_id: str, plan: str | None = None) -> dict:
    """Claim/start/recover a task. Works for pending, claimed, needs_revision."""
    return _post(_role_path("i_will_work_on"), {"task_id": task_id, "plan": plan})


@mcp.tool()
def i_have_committed(message: str) -> dict:
    """Record that you made a commit. Auto-creates progress entry."""
    return _post(_role_path("i_have_committed"), {"message": message})


@mcp.tool()
def i_am_done(task_id: str, notes: str = "") -> dict:
    """Submit work for QA. Runs verify/push/PR/submit-qa as needed."""
    return _post(_role_path("i_am_done"), {"task_id": task_id, "notes": notes})


@mcp.tool()
def i_am_blocked(task_id: str, reason: str) -> dict:
    """Escalate to PM. Logs a struggle journal entry."""
    return _post(_role_path("i_am_blocked"), {"task_id": task_id, "reason": reason})


@mcp.tool()
def i_am_idle() -> dict:
    """Report no more work. Soft-blocks if you have unread A2A/mentions."""
    return _post(_role_path("i_am_idle"), {})


def _register_role_specific_tools() -> None:
    """Conditionally register tools based on the agent's role manifest.

    Phase 1 only registers dev verbs unconditionally if role is developer.
    Other roles are registered in Phases 2-4 by extending this function.
    """
    manifest = _load_manifest()
    role = manifest["role"]
    flow_tools = set(manifest["flow_tools"])

    # Validate that we registered exactly the role's allowed tools.
    # If the role asks for a tool we haven't implemented yet, log a warning.
    implemented = {
        "give_me_work", "i_will_work_on", "i_have_committed",
        "i_am_done", "i_am_blocked", "i_am_idle",
    }
    missing = flow_tools - implemented
    if missing:
        import structlog
        log = structlog.get_logger()
        log.warning(
            "flow_server: role manifest references unimplemented verbs",
            role=role, missing=sorted(missing),
        )


if __name__ == "__main__":
    _register_role_specific_tools()
    mcp.run()
```

- [ ] **Step 9.3: Add to docker config**

The flow server needs to start as part of agent containers. In the agent Dockerfiles (`docker/agent-base.Dockerfile` and `docker/agent-dev-be.Dockerfile`), add a CMD/entrypoint addition that starts both the SDK shim AND the flow server. Or, more cleanly, the SDK shim can launch the flow server as a subprocess on startup.

Implementation choice: Update `roboco/agent_sdk/server.py` lifespan/startup to spawn the flow MCP server. (Existing code launches the SDK on port 9000; add another subprocess for the MCP server.)

- [ ] **Step 9.4: Smoke test the server starts**

In a dev shell, set the env vars and run:

```bash
ROBOCO_AGENT_ID=00000000-0000-0000-0001-000000000001 \
ROBOCO_AGENT_ROLE=developer \
ROBOCO_TOOL_MANIFEST_PATH=/tmp/manifest.json \
ROBOCO_ORCHESTRATOR_URL=http://localhost:8000 \
uv run python -m roboco.mcp.flow_server
```

(First create `/tmp/manifest.json` with a valid manifest.)
Expected: server starts, prints log lines, listens on stdio.

- [ ] **Step 9.5: Commit**

```bash
git add roboco/mcp/flow_server.py
git commit -m "feat(mcp): add roboco-flow MCP server for intent verbs (Phase 1: dev verbs implemented)"
```

---

## Task 10: `roboco-do` MCP server (content tools)

**Files:**
- Create: `roboco/mcp/do_server.py`

- [ ] **Step 10.1: Implement using same pattern as flow_server**

```python
# roboco/mcp/do_server.py
"""roboco-do MCP server — smart-wrapped content tools."""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

ORCHESTRATOR_URL = os.environ.get("ROBOCO_ORCHESTRATOR_URL", "http://roboco-orchestrator:8000")
AGENT_ID = os.environ["ROBOCO_AGENT_ID"]
AGENT_ROLE = os.environ["ROBOCO_AGENT_ROLE"]
TOOL_MANIFEST_PATH = Path(os.environ.get("ROBOCO_TOOL_MANIFEST_PATH", "/app/tool-manifest.json"))

_HEADERS = {"X-Agent-ID": AGENT_ID, "X-Agent-Role": AGENT_ROLE}
mcp = FastMCP("roboco-do")


def _post(path: str, body: dict) -> dict:
    with httpx.Client(timeout=30) as c:
        r = c.post(f"{ORCHESTRATOR_URL}{path}", headers=_HEADERS, json=body)
        r.raise_for_status()
        return r.json()


@mcp.tool()
def commit(message: str, files: list[str] | None = None) -> dict:
    """Make a git commit. [task-id] prefix auto-applied. Validates message."""
    return _post("/api/v2/do/commit", {"message": message, "files": files})


@mcp.tool()
def note(text: str, scope: str = "note", task_id: str | None = None) -> dict:
    """Write a journal entry. scope ∈ note|decision|reflect|learning|struggle."""
    return _post("/api/v2/do/note", {"text": text, "scope": scope, "task_id": task_id})


@mcp.tool()
def say(channel: str, text: str, task_id: str | None = None) -> dict:
    """Post to a channel. task_id auto-injected if you have an active task."""
    return _post("/api/v2/do/say", {"channel": channel, "text": text, "task_id": task_id})


@mcp.tool()
def dm(recipient: str, text: str, task_id: str | None = None, skill: str | None = None) -> dict:
    """A2A message. Auto-creates conversation; auto-resolves skill if needed."""
    return _post(
        "/api/v2/do/dm",
        {"recipient": recipient, "text": text, "task_id": task_id, "skill": skill},
    )


@mcp.tool()
def evidence(task_id: str) -> dict:
    """Inspect a task's PR diff, commits, files. Fetches dev branch into your workspace."""
    return _post("/api/v2/do/evidence", {"task_id": task_id})


if __name__ == "__main__":
    mcp.run()
```

- [ ] **Step 10.2: Smoke test**

Same pattern as Task 9. Run with env vars set.

- [ ] **Step 10.3: Commit**

```bash
git add roboco/mcp/do_server.py
git commit -m "feat(mcp): add roboco-do MCP server for smart-wrapped content tools"
```

---

## Task 11: Integrate spawn manifest write at orchestrator-side spawn

**Files:**
- Modify: `roboco/runtime/orchestrator.py`

- [ ] **Step 11.1: Locate the spawn function**

Run: `grep -n "spawn\|docker\|create_container\|run_container" roboco/runtime/orchestrator.py | head -10`

- [ ] **Step 11.2: Before launching the dev container, write its manifest**

```python
from pathlib import Path
from roboco.runtime.spawn_manifest import build_for_role, write_manifest


async def _prepare_manifest_for_spawn(agent, model: str) -> Path:
    """Build + write the agent's tool manifest to a host-side path that the
    container will mount at /app/tool-manifest.json."""
    workspace = WorkspaceService.path_for(agent)
    manifest = build_for_role(
        agent_id=agent.id,
        role=agent.role,
        team=agent.team,
        workspace_path=workspace,
        agent_model=model,
    )
    host_path = Path(f"/var/lib/roboco/manifests/{agent.id}.json")
    write_manifest(manifest, host_path)
    return host_path
```

In the spawn-container function, add a host-mount mapping for the manifest:

```python
container_config["mounts"].append({
    "Type": "bind",
    "Source": str(host_manifest_path),
    "Target": "/app/tool-manifest.json",
    "ReadOnly": True,
})
container_config["env"]["ROBOCO_GATEWAY_ENABLED"] = "true" if agent.role == "developer" else "false"
container_config["env"]["ROBOCO_TOOL_MANIFEST_PATH"] = "/app/tool-manifest.json"
```

(Phase 1: only developers get `ROBOCO_GATEWAY_ENABLED=true`. Phases 2-4 expand to other roles.)

- [ ] **Step 11.3: Smoke test (the script that the user runs from their NAS box)**

Bring up the dev stack with one developer agent. Verify:

```bash
docker exec roboco-agent-be-dev-1 cat /app/tool-manifest.json
# Should print the manifest JSON
docker exec roboco-agent-be-dev-1 env | grep ROBOCO_GATEWAY_ENABLED
# Should print: ROBOCO_GATEWAY_ENABLED=true
```

- [ ] **Step 11.4: Commit**

```bash
git add roboco/runtime/orchestrator.py
git commit -m "feat(runtime): mount per-agent tool-manifest.json on developer-container spawn; gateway flag enabled for devs only"
```

---

## Task 12: Slim developer role prompt

**Files:**
- Modify: `agents/prompts/roles/developer.md`

- [ ] **Step 12.1: Replace with the slim version**

Open `agents/prompts/roles/developer.md` and replace the entire content with:

```markdown
# Developer

You implement features, fix bugs, and write code.

## Who you are
- Team: {team}    Workspace: /data/workspaces/{project}/{team}/{your-slug}/
- You commit + push. You don't merge. PMs merge. CEO approves master.

## Your verbs (already loaded — no ToolSearch needed)
- `give_me_work()` — returns a task or `idle`
- `i_will_work_on(task_id, plan=None)` — claims/starts/recovers any state of yours
- `commit(message)` — auto-prefixed [task-id]; auto-progress entry
- `note(text, scope?)` — journal. scope ∈ note|decision|reflect|learning|struggle
- `i_have_committed(message)` — quick alias
- `i_am_blocked(reason)` — escalates and idles you
- `i_am_done(notes)` — runs verify/push/PR/submit-qa. Gateway tells you what's missing.
- `evidence(task_id)` — fetches PR diff if you need to inspect something
- `i_am_idle()` — done for now (soft-blocks if you have unread A2A/mentions)
- `say(channel, text)` — channel message; task_id auto-injected
- `dm(recipient, text, skill?)` — A2A; conversation auto-created

## Ground rules
- Edit/Write/Bash limited to your workspace.
- Tracing is enforced server-side. `i_am_done` requires: progress entry + journal:reflect + every acceptance criterion addressed (commit/note referencing it).
- Verb errors include a `remediate` field — follow it. Don't bypass.
- If unsure, call `give_me_work` and read the response.
```

- [ ] **Step 12.2: Update `agents/prompts/base.md` for gateway awareness**

Append a small section explaining the new error envelope and `remediate` field; remove the old ToolSearch-on-spawn instruction. Keep it brief — most workflow detail is gone.

- [ ] **Step 12.3: Commit**

```bash
git add agents/prompts/roles/developer.md agents/prompts/base.md
git commit -m "docs(prompts): rewrite developer role prompt for gateway-only verbs (~15 lines vs 49)"
```

---

## Task 13: Remove dev-only old MCP tools

**Files:**
- Modify: `roboco/mcp/task_server.py` — remove dev-only tools
- Modify: `roboco/mcp/journal_server.py` — remove old journal tools (used by dev only initially)
- Modify: `roboco/mcp/git/` — remove `roboco_git_create_pr`, `roboco_git_pr_merge`
- Modify: `roboco/mcp/notify_server.py` — remove `roboco_notify_list`, `roboco_notify_ack` (handled by inline briefing)
- Modify: `roboco/mcp/a2a_server.py` — remove `roboco_agent_request`, `roboco_a2a_check`
- Modify: `roboco/mcp/message_server.py` — remove `roboco_message_send` (dev-facing) — but only for dev; non-dev roles still use this until later phases. Implement via removing the registration and adjusting the per-role tool list.

Strategy: rather than removing the *implementations*, remove the **registrations** so dev manifests don't include them. Non-dev roles still see them via their manifests (until Phases 2-4 retire those roles). This is the cleanest path during phased rollout.

- [ ] **Step 13.1: Find where MCP tools are registered**

Run: `grep -n "@mcp.tool\|@server.tool\|mcp.tool()" roboco/mcp/task_server.py | head -30`

- [ ] **Step 13.2: Update Phase 0's `role_config.py` if needed**

The role config already lists what each role gets. Phase 1's dev manifest does not include `roboco_task_claim`, etc. So the existing MCP servers can stay — they just don't get loaded into dev containers because the SDK shim only registers tools the manifest explicitly lists.

The actual deletion of the *server code* happens in Phase 4. Phase 1's job is to ensure the dev manifest only includes the new gateway tools.

- [ ] **Step 13.3: Verify dev manifest does not include old tools**

Spawn a dev container, check:

```bash
docker exec roboco-agent-be-dev-1 cat /app/tool-manifest.json | python -c "import json,sys; m=json.load(sys.stdin); print('flow:',m['flow_tools']); print('do:',m['do_tools']); assert 'roboco_task_claim' not in m['flow_tools']+m['do_tools']; assert 'roboco_journal_entry' not in m['flow_tools']+m['do_tools']; print('clean')"
```

Expected: prints "clean".

- [ ] **Step 13.4: Commit**

```bash
git commit --allow-empty -m "chore(mcp): confirm dev manifest excludes legacy task/journal/notify/a2a tools (Phase 1 cutover; servers retired in Phase 4)"
```

---

## Task 14: Smoke test — full developer workflow

- [ ] **Step 14.1: Reset state on the deployment box**

Run: `ssh renzof@renzof-nas.local 'cd /volume1/roboco/ && bash scripts/reset_runtime_state.sh'`

- [ ] **Step 14.2: Bring up infra with developer-only gateway flag**

Verify `docker-compose.yml` passes `ROBOCO_GATEWAY_ENABLED=true` only to developer agents (or via per-agent env in the orchestrator-side spawn).

- [ ] **Step 14.3: Create a smoke-test task (dev-only path)**

Use the existing CEO workflow to create a task that exercises only the dev → QA handoff (the QA still uses old tools in Phase 1; that's fine for cross-role bridging).

Expected end-to-end:
1. Dev spawned with manifest mounted, no ToolSearch in briefing
2. Dev calls `give_me_work` → response includes the task
3. Dev calls `i_will_work_on(task_id, plan=...)` → status=in_progress
4. Dev calls `commit("feat(api): ...")` (descriptive subject)
5. Dev calls `i_am_done(task_id, "did the thing")`
6. Gateway runs catch-up: push → create PR → submit_verification → submit_qa
7. QA agent (still old workflow) reviews the PR
8. Task transitions through awaiting_qa → awaiting_documentation → ...

- [ ] **Step 14.4: Verify no Phase-1 regressions**

- No `ToolSearch` failures
- No "tool not available" for any tool the dev manifest lists
- `i_am_done` validation rejects when acceptance criteria unaddressed (test with a deliberately malformed call)
- Skill resolution: dev's auto-A2A to QA picks `code_review` (not `qa_review`) because of the alignment from Phase 0
- No NULL agent_id in audit_log entries written during this run

- [ ] **Step 14.5: Tag the phase complete**

```bash
git tag phase-1-developer-complete
git push origin phase-1-developer-complete
```

---

## Self-Review

1. **Spec coverage:** Phase 1 from §11 — all developer verbs implemented (Tasks 1-5), content tools (Task 6), API endpoints (Tasks 7-8), MCP servers (Tasks 9-10), spawn-time wiring (Task 11), prompt rewrite (Task 12), legacy tool removal from manifest (Task 13), smoke test (Task 14). ✓
2. **Placeholder scan:** All TDD steps include test code, implementation, run-and-expect. No "TBD". A few "Implementation note" callouts in spawn integration — the engineer fills the framework-specific details by reading the existing code. ✓
3. **Type consistency:** `Choreographer.__init__` signature finalized in Task 1; later tasks all reference the same DI shape. `ContentActions` has its own constructor. `Envelope.as_dict` shape matches what FastAPI handlers return. ✓
4. **Spec alignment:** All 6 dev intent verbs + 5 do tools delivered. Smart catch-up (`i_am_done`) implemented per §7.1 of spec. Tracing gates (§6.1) enforced. Skill resolution (§5.2) implemented. ✓
