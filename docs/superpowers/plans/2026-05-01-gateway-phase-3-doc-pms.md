# Agent Gateway — Phase 3: Documenter + PMs Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Prerequisite:** Phase 2 (`docs/superpowers/plans/2026-05-01-gateway-phase-2-qa.md`) merged. Tag `phase-2-qa-complete` exists. Devs and QAs run via gateway.

**Goal:** Cut Documenters, Cell PMs, and Main PM over to the gateway. Implement Doc verbs (`claim_doc_task`, `i_documented`); Cell PM verbs (`triage`, `unblock`, `complete` with auto-merge, `escalate_up`); Main PM verbs (`triage_all`, `complete` opening master PR + escalating to CEO, `escalate_up`). Wire `merge_chain` into `GitService.pr_merge`. Implement state restoration on unblock. Slim role prompts for Doc/Cell PM/Main PM. Verify the full **pending → completed** flow with CEO approval as the only manual step.

**Architecture:** This phase delivers the workflow's killer feature — the auto-merge chain (#22) and state-restoration unblock (#23). PR/merge ownership: Cell PM merges leaf PRs into the parent task branch; Main PM opens the master PR and escalates; CEO approves via UI. The gateway's `merge_chain.parent_branch_for(task)` (Phase 0) computes targets; `GitService.pr_merge` (Phase 0) executes; `Choreographer.complete` orchestrates them in one transactional call.

**Tech Stack:** Same as prior phases.

---

## File Structure

**Create**:
- `roboco/api/routes/v2/flow_doc.py`
- `roboco/api/routes/v2/flow_cell_pm.py`
- `roboco/api/routes/v2/flow_main_pm.py`
- `tests/unit/gateway/test_choreographer_doc.py`
- `tests/unit/gateway/test_choreographer_cell_pm.py`
- `tests/unit/gateway/test_choreographer_main_pm.py`
- `tests/integration/v2/test_flow_doc.py`
- `tests/integration/v2/test_flow_cell_pm.py`
- `tests/integration/v2/test_flow_main_pm.py`
- `tests/integration/v2/test_full_pending_to_completed.py` — end-to-end across all roles

**Modify**:
- `roboco/services/gateway/choreographer.py` — implement Phase 3 verb bodies
- `roboco/services/gateway/merge_chain.py` — add `MergeChainExecutor` class that composes `GitService.pr_merge` + `TaskService.complete` atomically
- `roboco/services/gateway/unblock_restore.py` — NEW: state restoration on unblock helper
- `roboco/services/git.py` — confirm `pr_merge(pr_number, target_branch)` exists; add if missing (kills #22 from the API side)
- `roboco/services/task.py` — add `unblock_with_restore`, `pre_block_snapshot`, `cell_pm_complete`, `main_pm_complete`, `documenter_for_team`, `escalate_up_to_role`, `triage_for_team`, `triage_all`
- `roboco/api/schemas/v2/flow.py` — add Doc/Cell PM/Main PM schemas
- `roboco/api/__init__.py` — mount new routers
- `roboco/mcp/flow_server.py` — register Doc + PM verbs
- `agents/prompts/roles/documenter.md` — slim
- `agents/prompts/roles/cell_pm.md` — slim
- `agents/prompts/roles/main_pm.md` — slim
- `roboco/runtime/orchestrator.py` — extend `GATEWAY_ENABLED_ROLES` to include `documenter`, `cell_pm`, `main_pm`

---

## Task 1: Choreographer — `claim_doc_task` and `i_documented`

**Files:**
- Modify: `roboco/services/gateway/choreographer.py`
- Test: `tests/unit/gateway/test_choreographer_doc.py`

- [ ] **Step 1.1: Write failing tests**

```python
# tests/unit/gateway/test_choreographer_doc.py
"""Tests for Documenter choreographer methods."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from roboco.services.gateway.choreographer import Choreographer


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
async def test_claim_doc_task_returns_evidence(make_choreographer):
    doc_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id, status="awaiting_documentation", assigned_to=None,
        pr_number=8, pr_url="https://github.com/x/y/pull/8",
        commits=[{"sha": "abc", "message": "feat: x"}],
        team="backend", branch_name="feature/backend/abc--def",
        documents=[], dev_notes="", acceptance_criteria=[],
        acceptance_criteria_status=[],
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.doc_claim.return_value = MagicMock(**{**t.__dict__, "assigned_to": doc_id})
    work_svc = AsyncMock()
    work_svc.files_changed.return_value = ["README.md"]
    git_svc = AsyncMock()
    git_svc.diff.return_value = "+++ diff"
    evidence_repo = AsyncMock()
    for attr in ("journal_highlights_for_task", "list_unread_a2a", "list_unread_mentions",
                 "list_pending_notifications", "task_metadata_gaps",
                 "recent_team_activity", "blockers_in_lane"):
        getattr(evidence_repo, attr).return_value = []
    c = make_choreographer(task=task_svc, work_session=work_svc, git=git_svc, evidence_repo=evidence_repo)

    env = await c.claim_doc_task(doc_id, task_id)
    body = env.as_dict()
    assert body["error"] is None
    assert body["evidence"]["pr_url"] == "https://github.com/x/y/pull/8"


@pytest.mark.asyncio
async def test_claim_doc_task_blocks_wrong_state(make_choreographer):
    doc_id = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="in_progress")
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    c = make_choreographer(task=task_svc)

    env = await c.claim_doc_task(doc_id, task_id)
    body = env.as_dict()
    assert body["error"] == "invalid_state"


@pytest.mark.asyncio
async def test_i_documented_requires_min_notes_and_files(make_choreographer):
    doc_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id, status="claimed", assigned_to=doc_id,
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    c = make_choreographer(task=task_svc)

    env = await c.i_documented(doc_id, task_id, notes="short", files=[])
    body = env.as_dict()
    assert body["error"] == "tracing_gap" or body["error"] == "invalid_state"


@pytest.mark.asyncio
async def test_i_documented_succeeds_and_transitions(make_choreographer):
    doc_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id, status="claimed", assigned_to=doc_id,
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.docs_complete.return_value = MagicMock(**{**t.__dict__, "status": "awaiting_pm_review"})
    c = make_choreographer(task=task_svc)

    notes = "Wrote backend/guides/feature-x.md with usage examples and config notes."
    files = ["backend/guides/feature-x.md"]
    env = await c.i_documented(doc_id, task_id, notes=notes, files=files)
    assert env.error is None
    assert env.status == "awaiting_pm_review"
    task_svc.docs_complete.assert_awaited_once()
```

- [ ] **Step 1.2: Run tests — expect FAIL**

- [ ] **Step 1.3: Implement**

```python
async def claim_doc_task(self, doc_agent_id, task_id):
    t = await self.task.get(task_id)
    if t is None:
        return Envelope.not_found(message=f"task {task_id} not found")
    if str(t.status) != "awaiting_documentation":
        return Envelope.invalid_state(
            message=f"task {task_id} is in {t.status}, expected awaiting_documentation",
            remediate="call give_me_work() to find an actionable doc task",
            context_briefing=await self._briefing_for(doc_agent_id, task_id),
        )
    t = await self.task.doc_claim(doc_agent_id, task_id)
    files_changed = await self.work_session.files_changed(t.work_session_id) if t.work_session_id else []
    diff = await self.git.diff(branch_name=t.branch_name) if t.branch_name else ""
    journal_highlights = await self.evidence_repo.journal_highlights_for_task(task_id)
    from roboco.services.gateway.evidence_builder import build_evidence_for_task
    ev = build_evidence_for_task(
        t,
        journal_highlights=journal_highlights,
        files_changed=files_changed,
        pr_diff_summary=diff,
    )
    return Envelope.ok(
        status=str(t.status),
        task_id=str(task_id),
        next="write docs in your workspace, commit them, then call i_documented(task_id, notes, files)",
        evidence=ev.as_dict(),
        context_briefing=await self._briefing_for(doc_agent_id, task_id),
    )


async def i_documented(self, doc_agent_id, task_id, notes, files):
    t = await self.task.get(task_id)
    if t is None:
        return Envelope.not_found(message=f"task {task_id} not found")
    if t.assigned_to != doc_agent_id:
        return Envelope.not_authorized(
            message="not assigned to you",
            remediate="claim it via claim_doc_task(task_id) first",
            context_briefing=await self._briefing_for(doc_agent_id, task_id),
        )
    if not notes or len(notes) < settings.docs_notes_min_chars:
        return Envelope.tracing_gap(
            missing=["docs_notes>=20"],
            remediate=(
                "i_documented requires notes>=20 chars summarizing what you "
                "documented and where (file paths). Include each file in `files=...`."
            ),
            context_briefing=await self._briefing_for(doc_agent_id, task_id),
        )
    if not files or len(files) == 0:
        return Envelope.tracing_gap(
            missing=["files"],
            remediate="i_documented requires files=['<path>', ...] listing the doc files written.",
            context_briefing=await self._briefing_for(doc_agent_id, task_id),
        )
    t = await self.task.docs_complete(doc_agent_id, task_id, notes=notes, files=files)
    # Auto-A2A to Cell PM
    pm_agent = await self.task.cell_pm_for_team(t.team)
    if pm_agent is not None:
        await self.a2a.send(
            from_agent=doc_agent_id,
            to_agent=pm_agent.id,
            skill="task_management",
            task_id=task_id,
            body=f"Docs complete for {t.id}. Ready for PM review + merge.",
        )
    return Envelope.ok(
        status=str(t.status),
        task_id=str(task_id),
        next="idle until PM completes",
        context_briefing=await self._briefing_for(doc_agent_id, task_id),
    )
```

- [ ] **Step 1.4: Add helpers to TaskService**

In `roboco/services/task.py`, add:
- `doc_claim(agent_id, task_id)` — claims a task from awaiting_documentation
- `docs_complete(agent_id, task_id, notes, files)` — sets `docs_complete=True`, `documents=files`, transitions to `awaiting_pm_review`. Audit actor = agent_id.
- `cell_pm_for_team(team)` → returns the cell PM agent for a team (look up by role + team)
- `documenter_for_team(team)` → ditto for documenter

(These compose existing transition mechanics; the actor-attribution fix kills #11 for these paths.)

- [ ] **Step 1.5: Run tests — expect PASS**

- [ ] **Step 1.6: Commit**

```bash
git add roboco/services/gateway/choreographer.py roboco/services/task.py tests/unit/gateway/test_choreographer_doc.py
git commit -m "feat(gateway): implement claim_doc_task and i_documented with file-list and notes-min-chars gates"
```

---

## Task 2: Choreographer — `triage` (Cell PM) and `triage_all` (Main PM)

**Files:**
- Modify: `roboco/services/gateway/choreographer.py`
- Test: `tests/unit/gateway/test_choreographer_cell_pm.py`, `tests/unit/gateway/test_choreographer_main_pm.py`

- [ ] **Step 2.1: Write failing tests**

```python
# tests/unit/gateway/test_choreographer_cell_pm.py
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from roboco.services.gateway.choreographer import Choreographer


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
async def test_cell_pm_triage_returns_blocked_first(make_choreographer):
    pm_id = uuid4()
    blocked_task = MagicMock(id=uuid4(), status="blocked", title="b", team="backend")
    pending_task = MagicMock(id=uuid4(), status="awaiting_pm_review", title="p", team="backend")
    task_svc = AsyncMock()
    task_svc.list_blocked_for_team.return_value = [blocked_task]
    task_svc.list_awaiting_pm_review_for_team.return_value = [pending_task]
    evidence_repo = AsyncMock()
    for attr in ("list_unread_a2a", "list_unread_mentions", "list_pending_notifications",
                 "task_metadata_gaps", "recent_team_activity", "blockers_in_lane"):
        getattr(evidence_repo, attr).return_value = []
    c = make_choreographer(task=task_svc, evidence_repo=evidence_repo)

    env = await c.triage(pm_id)
    body = env.as_dict()
    assert body["task_id"] == str(blocked_task.id)
    assert "unblock" in body["next"].lower()
```

```python
# tests/unit/gateway/test_choreographer_main_pm.py
@pytest.mark.asyncio
async def test_main_pm_triage_all_includes_cross_team(make_choreographer):
    pm_id = uuid4()
    task_svc = AsyncMock()
    task_svc.list_blocked_all_teams.return_value = [MagicMock(id=uuid4(), status="blocked", team="backend", title="x")]
    task_svc.list_awaiting_main_pm_all.return_value = []
    evidence_repo = AsyncMock()
    for attr in ("list_unread_a2a", "list_unread_mentions", "list_pending_notifications",
                 "task_metadata_gaps", "recent_team_activity", "blockers_in_lane"):
        getattr(evidence_repo, attr).return_value = []
    c = make_choreographer(task=task_svc, evidence_repo=evidence_repo)

    env = await c.triage_all(pm_id)
    body = env.as_dict()
    assert body["error"] is None
    assert body["task_id"] is not None
```

- [ ] **Step 2.2: Run tests — expect FAIL**

- [ ] **Step 2.3: Implement**

```python
async def triage(self, pm_agent_id):
    """Cell PM triage: prioritize blocked > awaiting_pm_review > stale-claim > available."""
    pm = await self.task.agent_for(pm_agent_id)
    blocked = await self.task.list_blocked_for_team(pm.team)
    if blocked:
        t = blocked[0]
        return Envelope.ok(
            status=str(t.status), task_id=str(t.id),
            next=f"investigate the block, then unblock(task_id='{t.id}')",
            context_briefing=await self._briefing_for(pm_agent_id, t.id),
        )
    awaiting = await self.task.list_awaiting_pm_review_for_team(pm.team)
    if awaiting:
        t = awaiting[0]
        return Envelope.ok(
            status=str(t.status), task_id=str(t.id),
            next=f"review and complete(task_id='{t.id}')",
            context_briefing=await self._briefing_for(pm_agent_id, t.id),
        )
    return Envelope.ok(
        status="idle", task_id=None,
        next="no PM work — call i_am_idle",
        context_briefing=await self._briefing_for(pm_agent_id, None),
    )


async def triage_all(self, pm_agent_id):
    """Main PM triage: across all teams. Same priority order, but spans cells."""
    blocked = await self.task.list_blocked_all_teams()
    if blocked:
        t = blocked[0]
        return Envelope.ok(
            status=str(t.status), task_id=str(t.id),
            next=f"escalation/cross-cell help required: investigate, then unblock(task_id='{t.id}') or escalate_up()",
            context_briefing=await self._briefing_for(pm_agent_id, t.id),
        )
    awaiting = await self.task.list_awaiting_main_pm_all()
    if awaiting:
        t = awaiting[0]
        return Envelope.ok(
            status=str(t.status), task_id=str(t.id),
            next=f"complete(task_id='{t.id}') opens master PR + escalates to CEO",
            context_briefing=await self._briefing_for(pm_agent_id, t.id),
        )
    return Envelope.ok(
        status="idle", task_id=None,
        next="no Main PM work",
        context_briefing=await self._briefing_for(pm_agent_id, None),
    )
```

- [ ] **Step 2.4: Add helpers to TaskService**

```python
async def agent_for(self, agent_id) -> Agent: ...
async def list_blocked_for_team(self, team) -> list[Task]: ...
async def list_awaiting_pm_review_for_team(self, team) -> list[Task]: ...
async def list_blocked_all_teams(self) -> list[Task]: ...
async def list_awaiting_main_pm_all(self) -> list[Task]: ...  # tasks where a Main PM (root) is the next reviewer
```

- [ ] **Step 2.5: Run tests — expect PASS**

- [ ] **Step 2.6: Commit**

```bash
git add roboco/services/gateway/choreographer.py roboco/services/task.py tests/unit/gateway/test_choreographer_cell_pm.py tests/unit/gateway/test_choreographer_main_pm.py
git commit -m "feat(gateway): implement triage (cell PM) and triage_all (main PM) with priority order"
```

---

## Task 3: Choreographer — `unblock` with state restoration (#23)

**Files:**
- Modify: `roboco/services/gateway/choreographer.py`
- Create: `roboco/services/gateway/unblock_restore.py`
- Test: append to `tests/unit/gateway/test_choreographer_cell_pm.py`

- [ ] **Step 3.1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_unblock_restores_pre_block_state(make_choreographer):
    pm_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id, status="blocked",
        pre_block_state="awaiting_documentation",
        pre_block_assignee=uuid4(),
        pre_block_metadata={"some_field": "x"},
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.unblock_with_restore.return_value = MagicMock(
        **{**t.__dict__, "status": "awaiting_documentation", "assigned_to": t.pre_block_assignee}
    )
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    c = make_choreographer(task=task_svc, journal=journal_svc)

    env = await c.unblock(pm_id, task_id, restore=True)
    assert env.error is None
    assert env.status == "awaiting_documentation"
    task_svc.unblock_with_restore.assert_awaited_once_with(pm_id, task_id, restore=True)


@pytest.mark.asyncio
async def test_unblock_default_restores(make_choreographer):
    pm_id = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="blocked", pre_block_state="awaiting_qa", pre_block_assignee=uuid4(),
                  pre_block_metadata={})
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.unblock_with_restore.return_value = MagicMock(**{**t.__dict__, "status": "awaiting_qa"})
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    c = make_choreographer(task=task_svc, journal=journal_svc)

    # restore omitted -> defaults to True
    env = await c.unblock(pm_id, task_id)
    assert env.status == "awaiting_qa"
```

- [ ] **Step 3.2: Run test — expect FAIL**

- [ ] **Step 3.3: Implement**

```python
async def unblock(self, pm_agent_id, task_id, *, restore=True):
    t = await self.task.get(task_id)
    if t is None:
        return Envelope.not_found(message=f"task {task_id} not found")
    if str(t.status) != "blocked":
        return Envelope.invalid_state(
            message=f"task {task_id} is in {t.status}, expected blocked",
            remediate="this task is not blocked; call triage() to find blocked tasks",
            context_briefing=await self._briefing_for(pm_agent_id, task_id),
        )
    has_decision = await self.journal.has_decision_for_task(pm_agent_id, task_id)
    if not has_decision:
        from roboco.services.gateway.remediation import hint_for_missing_journal_decision
        return Envelope.tracing_gap(
            missing=["journal:decision"],
            remediate=hint_for_missing_journal_decision(),
            context_briefing=await self._briefing_for(pm_agent_id, task_id),
        )
    t = await self.task.unblock_with_restore(pm_agent_id, task_id, restore=restore)
    return Envelope.ok(
        status=str(t.status),
        task_id=str(task_id),
        next="task restored to its pre-block state — original assignee will resume" if restore
              else "task back to in_progress; you'll need to re-engage the workflow",
        context_briefing=await self._briefing_for(pm_agent_id, task_id),
    )
```

- [ ] **Step 3.4: Add `unblock_with_restore` to TaskService**

```python
async def unblock_with_restore(self, pm_agent_id, task_id, *, restore=True):
    """Unblock and optionally restore pre_block_state.

    When restore=True (default):
      - status = pre_block_state
      - assigned_to = pre_block_assignee
      - other snapshot fields restored from pre_block_metadata
      - clear pre_block_* fields after restore
    When restore=False:
      - status = in_progress; assigned_to = pm_agent_id (legacy behavior)
      - leave pre_block_* fields cleared
    Audit actor = pm_agent_id.
    """
    t = await self.get(task_id)
    if restore and t.pre_block_state:
        await self._db.execute(
            update(TaskModel).where(TaskModel.id == task_id).values(
                status=t.pre_block_state,
                assigned_to=t.pre_block_assignee,
                pre_block_state=None, pre_block_assignee=None, pre_block_metadata=None,
            )
        )
    else:
        await self._db.execute(
            update(TaskModel).where(TaskModel.id == task_id).values(
                status="in_progress",
                assigned_to=pm_agent_id,
                pre_block_state=None, pre_block_assignee=None, pre_block_metadata=None,
            )
        )
    await self.audit.write(
        actor_id=pm_agent_id, target_type="task", target_id=task_id,
        event_type="task.unblocked", severity="info",
        details={"restore": restore, "pre_block_state": str(t.pre_block_state)},
    )
    await self._db.flush()
    return await self.get(task_id)
```

(Use the existing audit-write pattern in your codebase.)

- [ ] **Step 3.5: Snapshot the pre-block state during the `block` transition**

Find the existing `block` method on TaskService (or wherever block transitions are written). Update it to snapshot:

```python
async def block(self, agent_id, task_id, reason):
    t = await self.get(task_id)
    await self._db.execute(
        update(TaskModel).where(TaskModel.id == task_id).values(
            status="blocked",
            pre_block_state=t.status,
            pre_block_assignee=t.assigned_to,
            pre_block_metadata={
                "blocked_at": datetime.now(tz=timezone.utc).isoformat(),
                "reason": reason,
            },
        )
    )
    ...
```

- [ ] **Step 3.6: Run tests — expect PASS**

- [ ] **Step 3.7: Commit**

```bash
git add roboco/services/gateway/choreographer.py roboco/services/task.py tests/unit/gateway/test_choreographer_cell_pm.py
git commit -m "feat(gateway): implement unblock with pre_block_state restoration (kills #23)"
```

---

## Task 4: Choreographer — Cell PM `complete` with auto-merge (#22)

**Files:**
- Modify: `roboco/services/gateway/choreographer.py`
- Test: append to `tests/unit/gateway/test_choreographer_cell_pm.py`

- [ ] **Step 4.1: Write failing tests**

```python
@pytest.mark.asyncio
async def test_cell_pm_complete_merges_then_completes(make_choreographer):
    pm_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id, status="awaiting_pm_review", assigned_to=pm_id,
        pr_number=8, branch_name="feature/backend/abc--def", parent_task_id=uuid4(),
        team="backend",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.all_subtasks_terminal.return_value = True
    task_svc.cell_pm_complete.return_value = MagicMock(**{**t.__dict__, "status": "completed"})
    git_svc = AsyncMock()
    git_svc.pr_merge.return_value = {"merged": True, "merge_commit_sha": "merge-abc"}
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    c = make_choreographer(task=task_svc, git=git_svc, journal=journal_svc)

    env = await c.cell_pm_complete(pm_id, task_id, notes="reviewed and approved")
    assert env.error is None
    assert env.status == "completed"
    git_svc.pr_merge.assert_awaited_once_with(8, target="feature/backend/abc")


@pytest.mark.asyncio
async def test_cell_pm_complete_blocks_if_subtasks_unfinished(make_choreographer):
    pm_id = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="awaiting_pm_review", assigned_to=pm_id, parent_task_id=uuid4())
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.all_subtasks_terminal.return_value = False
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    c = make_choreographer(task=task_svc, journal=journal_svc)

    env = await c.cell_pm_complete(pm_id, task_id, notes="x")
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "subtasks" in str(body["missing"]).lower()


@pytest.mark.asyncio
async def test_cell_pm_complete_requires_journal_decision(make_choreographer):
    pm_id = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="awaiting_pm_review", assigned_to=pm_id, parent_task_id=uuid4())
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.all_subtasks_terminal.return_value = True
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = False
    c = make_choreographer(task=task_svc, journal=journal_svc)

    env = await c.cell_pm_complete(pm_id, task_id, notes="x")
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "journal:decision" in body["missing"]
```

- [ ] **Step 4.2: Run tests — expect FAIL**

- [ ] **Step 4.3: Implement**

```python
from roboco.services.gateway.merge_chain import parent_branch_for


async def cell_pm_complete(self, pm_agent_id, task_id, notes):
    t = await self.task.get(task_id)
    if t is None:
        return Envelope.not_found(message=f"task {task_id} not found")
    if t.assigned_to != pm_agent_id:
        return Envelope.not_authorized(
            message="not assigned to you",
            remediate="claim the task or wait for it to be assigned",
            context_briefing=await self._briefing_for(pm_agent_id, task_id),
        )
    if str(t.status) != "awaiting_pm_review":
        return Envelope.invalid_state(
            message=f"task {task_id} is in {t.status}, expected awaiting_pm_review",
            remediate="this task is not ready for completion",
            context_briefing=await self._briefing_for(pm_agent_id, task_id),
        )
    has_decision = await self.journal.has_decision_for_task(pm_agent_id, task_id)
    if not has_decision:
        from roboco.services.gateway.remediation import hint_for_missing_journal_decision
        return Envelope.tracing_gap(
            missing=["journal:decision"],
            remediate=hint_for_missing_journal_decision(),
            context_briefing=await self._briefing_for(pm_agent_id, task_id),
        )
    all_terminal = await self.task.all_subtasks_terminal(task_id)
    if not all_terminal:
        return Envelope.tracing_gap(
            missing=["subtasks not all terminal"],
            remediate=(
                "all subtasks must be in completed/cancelled before completing parent. "
                "Call triage() to find pending subtasks."
            ),
            context_briefing=await self._briefing_for(pm_agent_id, task_id),
        )
    if t.pr_number is None:
        return Envelope.invalid_state(
            message="task has no PR; cannot merge",
            remediate="this state should not occur post-Phase-1; investigate dev's i_am_done path",
            context_briefing=await self._briefing_for(pm_agent_id, task_id),
        )
    target = parent_branch_for(t.branch_name)
    merge_result = await self.git.pr_merge(t.pr_number, target=target)
    t = await self.task.cell_pm_complete(pm_agent_id, task_id, notes, merge_commit=merge_result.get("merge_commit_sha"))
    return Envelope.ok(
        status=str(t.status),
        task_id=str(task_id),
        next=f"merged into {target}; triage() for next item",
        context_briefing=await self._briefing_for(pm_agent_id, task_id),
    )
```

- [ ] **Step 4.4: Add `cell_pm_complete` and `all_subtasks_terminal` to TaskService**

```python
async def all_subtasks_terminal(self, task_id) -> bool:
    """True if every subtask of task_id is in {completed, cancelled}."""
    from sqlalchemy import select, func
    q = select(func.count(TaskModel.id)).where(
        TaskModel.parent_task_id == task_id,
        TaskModel.status.notin_(["completed", "cancelled"]),
    )
    result = await self._db.execute(q)
    return (result.scalar() or 0) == 0


async def cell_pm_complete(self, pm_agent_id, task_id, notes, *, merge_commit=None):
    """Cell PM completes a task — final state transition.

    Audit actor = pm_agent_id. Records merge_commit in commits[] for traceability.
    """
    t = await self.get(task_id)
    new_commits = list(t.commits or [])
    if merge_commit:
        new_commits.append({"sha": merge_commit, "message": f"Merge of {t.pr_number}", "is_merge": True})
    await self._db.execute(
        update(TaskModel).where(TaskModel.id == task_id).values(
            status="completed",
            completed_at=datetime.now(tz=timezone.utc),
            commits=new_commits,
        )
    )
    await self.audit.write(
        actor_id=pm_agent_id, target_type="task", target_id=task_id,
        event_type="task.completed", severity="info",
        details={"notes": notes, "merge_commit": merge_commit},
    )
    await self._db.flush()
    return await self.get(task_id)
```

- [ ] **Step 4.5: Confirm `GitService.pr_merge` exists**

Run: `grep -n "pr_merge\|def pr_merge" roboco/services/git.py`
If not present, add it. It calls the GitHub API via the project's PAT to merge the PR with `target_branch` as the base. Returns `{"merged": bool, "merge_commit_sha": str}`.

- [ ] **Step 4.6: Run tests — expect PASS**

- [ ] **Step 4.7: Commit**

```bash
git add roboco/services/gateway/choreographer.py roboco/services/task.py roboco/services/git.py tests/unit/gateway/test_choreographer_cell_pm.py
git commit -m "feat(gateway): implement cell_pm_complete with auto-merge to parent branch (kills #22 for cell scope)"
```

---

## Task 5: Choreographer — Main PM `complete` (master PR + CEO escalation)

**Files:**
- Modify: `roboco/services/gateway/choreographer.py`
- Test: append to `tests/unit/gateway/test_choreographer_main_pm.py`

- [ ] **Step 5.1: Write failing tests**

```python
@pytest.mark.asyncio
async def test_main_pm_complete_opens_master_pr_and_escalates(make_choreographer):
    main_pm_id = uuid4()
    root_task_id = uuid4()
    t = MagicMock(
        id=root_task_id, status="awaiting_pm_review", assigned_to=main_pm_id,
        pr_number=None, branch_name="feature/backend/root123", parent_task_id=None,
        team="backend",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.escalate_to_ceo.return_value = MagicMock(**{**t.__dict__, "status": "awaiting_ceo_approval"})
    task_svc.all_subtasks_terminal.return_value = True
    git_svc = AsyncMock()
    git_svc.create_pr.return_value = {"pr_number": 99, "pr_url": "https://github.com/x/y/pull/99"}
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    c = make_choreographer(task=task_svc, git=git_svc, journal=journal_svc)

    env = await c.main_pm_complete(main_pm_id, root_task_id, notes="ready for prod")
    body = env.as_dict()
    assert env.error is None
    assert env.status == "awaiting_ceo_approval"
    git_svc.create_pr.assert_awaited_once_with(
        "feature/backend/root123", parent="master", is_root_pr=True
    )
    task_svc.escalate_to_ceo.assert_awaited_once()


@pytest.mark.asyncio
async def test_main_pm_complete_skips_pr_creation_if_already_open_to_master(make_choreographer):
    main_pm_id = uuid4()
    root_task_id = uuid4()
    t = MagicMock(
        id=root_task_id, status="awaiting_pm_review", assigned_to=main_pm_id,
        pr_number=42, pr_target="master", branch_name="feature/backend/root123",
        parent_task_id=None, team="backend",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.escalate_to_ceo.return_value = MagicMock(**{**t.__dict__, "status": "awaiting_ceo_approval"})
    task_svc.all_subtasks_terminal.return_value = True
    git_svc = AsyncMock()
    git_svc.pr_target.return_value = "master"
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    c = make_choreographer(task=task_svc, git=git_svc, journal=journal_svc)

    env = await c.main_pm_complete(main_pm_id, root_task_id, notes="ready")
    git_svc.create_pr.assert_not_awaited()
    task_svc.escalate_to_ceo.assert_awaited_once()
```

- [ ] **Step 5.2: Run tests — expect FAIL**

- [ ] **Step 5.3: Implement**

```python
async def main_pm_complete(self, main_pm_agent_id, root_task_id, notes):
    t = await self.task.get(root_task_id)
    if t is None:
        return Envelope.not_found(message=f"task {root_task_id} not found")
    if t.assigned_to != main_pm_agent_id:
        return Envelope.not_authorized(
            message="not assigned to you",
            remediate="wait for assignment or claim",
            context_briefing=await self._briefing_for(main_pm_agent_id, root_task_id),
        )
    if str(t.status) != "awaiting_pm_review":
        return Envelope.invalid_state(
            message=f"task {root_task_id} is in {t.status}, expected awaiting_pm_review",
            remediate="this task is not ready for main-PM completion",
            context_briefing=await self._briefing_for(main_pm_agent_id, root_task_id),
        )
    if t.parent_task_id is not None:
        return Envelope.invalid_state(
            message="main_pm complete only operates on root tasks (no parent_task_id)",
            remediate="cell PM should complete this task; main PM only completes root tasks",
            context_briefing=await self._briefing_for(main_pm_agent_id, root_task_id),
        )
    has_decision = await self.journal.has_decision_for_task(main_pm_agent_id, root_task_id)
    if not has_decision:
        from roboco.services.gateway.remediation import hint_for_missing_journal_decision
        return Envelope.tracing_gap(
            missing=["journal:decision"],
            remediate=hint_for_missing_journal_decision(),
            context_briefing=await self._briefing_for(main_pm_agent_id, root_task_id),
        )
    all_terminal = await self.task.all_subtasks_terminal(root_task_id)
    if not all_terminal:
        return Envelope.tracing_gap(
            missing=["subtasks not all terminal"],
            remediate="all subtasks must be in completed/cancelled state",
            context_briefing=await self._briefing_for(main_pm_agent_id, root_task_id),
        )
    # Open master PR if not already targeting master
    needs_pr = t.pr_number is None
    if not needs_pr:
        current_target = await self.git.pr_target(t.pr_number)
        needs_pr = current_target != "master"
    if needs_pr:
        await self.git.create_pr(t.branch_name, parent="master", is_root_pr=True)
    t = await self.task.escalate_to_ceo(main_pm_agent_id, root_task_id, notes)
    return Envelope.ok(
        status=str(t.status),
        task_id=str(root_task_id),
        next="idle until CEO approves (or rejects) via UI",
        context_briefing=await self._briefing_for(main_pm_agent_id, root_task_id),
    )
```

- [ ] **Step 5.4: Add `escalate_to_ceo` to TaskService and `pr_target` to GitService**

```python
# task.py
async def escalate_to_ceo(self, pm_agent_id, root_task_id, notes):
    """Transition awaiting_pm_review -> awaiting_ceo_approval. Audit actor = pm_agent_id."""
    ...

# git.py
async def pr_target(self, pr_number) -> str:
    """Return the base branch the PR targets (via GH API)."""
    ...
```

- [ ] **Step 5.5: Run tests — expect PASS**

- [ ] **Step 5.6: Commit**

```bash
git add roboco/services/gateway/choreographer.py roboco/services/task.py roboco/services/git.py tests/unit/gateway/test_choreographer_main_pm.py
git commit -m "feat(gateway): implement main_pm_complete (open master PR + escalate to CEO)"
```

---

## Task 6: Choreographer — `complete` dispatcher (cell vs main)

**Files:**
- Modify: `roboco/services/gateway/choreographer.py`

- [ ] **Step 6.1: Add a single `complete` entry point that dispatches by role**

```python
async def complete(self, agent_id, task_id, notes):
    """Dispatch to cell_pm_complete or main_pm_complete based on agent role."""
    agent = await self.task.agent_for(agent_id)
    if agent.role == "cell_pm":
        return await self.cell_pm_complete(agent_id, task_id, notes)
    if agent.role == "main_pm":
        return await self.main_pm_complete(agent_id, task_id, notes)
    return Envelope.not_authorized(
        message=f"role {agent.role} cannot complete tasks via this verb",
        remediate="only cell_pm and main_pm can call complete",
        context_briefing=await self._briefing_for(agent_id, task_id),
    )
```

- [ ] **Step 6.2: Add a quick test**

```python
@pytest.mark.asyncio
async def test_complete_dispatches_by_role(make_choreographer):
    cell_pm_id = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="awaiting_pm_review", assigned_to=cell_pm_id,
                   parent_task_id=uuid4(), pr_number=8, branch_name="feature/backend/a--b",
                   team="backend")
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="cell_pm")
    task_svc.all_subtasks_terminal.return_value = True
    task_svc.cell_pm_complete.return_value = MagicMock(**{**t.__dict__, "status": "completed"})
    git_svc = AsyncMock()
    git_svc.pr_merge.return_value = {"merged": True, "merge_commit_sha": "x"}
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    c = make_choreographer(task=task_svc, git=git_svc, journal=journal_svc)

    env = await c.complete(cell_pm_id, task_id, "ok")
    assert env.status == "completed"
```

- [ ] **Step 6.3: Run + Commit**

Run: `uv run pytest tests/unit/gateway/test_choreographer_cell_pm.py::test_complete_dispatches_by_role -v`
Then: 

```bash
git add roboco/services/gateway/choreographer.py tests/unit/gateway/test_choreographer_cell_pm.py
git commit -m "feat(gateway): add complete() dispatcher routing to cell_pm_complete or main_pm_complete by role"
```

---

## Task 7: Choreographer — `escalate_up`

**Files:**
- Modify: `roboco/services/gateway/choreographer.py`

- [ ] **Step 7.1: Test + Implement**

```python
@pytest.mark.asyncio
async def test_escalate_up_routes_by_escalation_target(make_choreographer):
    pm_id = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="blocked", assigned_to=pm_id, team="backend")
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", escalation_target="main_pm")
    task_svc.escalate_up_to_role.return_value = MagicMock(**{**t.__dict__, "assigned_to": uuid4()})
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    c = make_choreographer(task=task_svc, journal=journal_svc)

    env = await c.escalate_up(pm_id, task_id, reason="cross-cell coordination needed")
    assert env.error is None
    task_svc.escalate_up_to_role.assert_awaited_once()
```

```python
async def escalate_up(self, pm_agent_id, task_id, reason):
    t = await self.task.get(task_id)
    if t is None:
        return Envelope.not_found(message=f"task {task_id} not found")
    has_decision = await self.journal.has_decision_for_task(pm_agent_id, task_id)
    if not has_decision:
        from roboco.services.gateway.remediation import hint_for_missing_journal_decision
        return Envelope.tracing_gap(
            missing=["journal:decision"],
            remediate=hint_for_missing_journal_decision(),
            context_briefing=await self._briefing_for(pm_agent_id, task_id),
        )
    me = await self.task.agent_for(pm_agent_id)
    target_role = me.escalation_target
    if not target_role:
        return Envelope.invalid_state(
            message="no escalation target configured for your role",
            remediate="check agents_config.py for your role's escalation_target",
            context_briefing=await self._briefing_for(pm_agent_id, task_id),
        )
    t = await self.task.escalate_up_to_role(pm_agent_id, task_id, target_role, reason)
    return Envelope.ok(
        status=str(t.status),
        task_id=str(task_id),
        next=f"escalated to {target_role}; idle until they respond",
        context_briefing=await self._briefing_for(pm_agent_id, task_id),
    )
```

- [ ] **Step 7.2: Add helper `escalate_up_to_role` to TaskService**

```python
async def escalate_up_to_role(self, from_agent_id, task_id, target_role, reason):
    """Reassign task to a same-team agent of target_role. For Cell PM -> Main PM cross-team coordination."""
    target = await self._find_agent_for_role_team(target_role, await self._team_for_task(task_id))
    if target is None:
        # Fall back to root role agent (e.g., Main PM doesn't have per-team)
        target = await self._find_root_agent_for_role(target_role)
    if target is None:
        raise ValueError(f"no agent for role {target_role}")
    await self._db.execute(
        update(TaskModel).where(TaskModel.id == task_id).values(
            status="blocked",
            assigned_to=target.id,
            blocker_raised_by=from_agent_id,
        )
    )
    await self.audit.write(
        actor_id=from_agent_id, target_type="task", target_id=task_id,
        event_type="task.escalated_up", severity="warning",
        details={"reason": reason, "to_role": target_role, "to_agent_id": str(target.id)},
    )
    await self._db.flush()
    return await self.get(task_id)
```

- [ ] **Step 7.3: Commit**

```bash
git add roboco/services/gateway/choreographer.py roboco/services/task.py tests/unit/gateway/test_choreographer_cell_pm.py
git commit -m "feat(gateway): implement escalate_up routing by role.escalation_target"
```

---

## Task 8: API v2 — Doc, Cell PM, Main PM endpoints

**Files:**
- Create: `roboco/api/routes/v2/flow_doc.py`, `flow_cell_pm.py`, `flow_main_pm.py`
- Modify: `roboco/api/schemas/v2/flow.py` — add new request models
- Modify: `roboco/api/__init__.py` — mount routers

- [ ] **Step 8.1: Add request schemas**

Append to `roboco/api/schemas/v2/flow.py`:

```python
class ClaimDocTaskRequest(BaseModel):
    task_id: UUID


class IDocumentedRequest(BaseModel):
    task_id: UUID
    notes: str = Field(..., min_length=1)
    files: list[str] = Field(..., min_length=1)


class TriageRequest(BaseModel):
    pass


class UnblockRequest(BaseModel):
    task_id: UUID
    restore: bool = True


class CompleteRequest(BaseModel):
    task_id: UUID
    notes: str = Field(..., min_length=1)


class EscalateUpRequest(BaseModel):
    task_id: UUID
    reason: str = Field(..., min_length=1)
```

- [ ] **Step 8.2: Doc router**

```python
# roboco/api/routes/v2/flow_doc.py
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header

from roboco.api.deps import get_choreographer
from roboco.api.schemas.v2.flow import (
    ClaimDocTaskRequest, GiveMeWorkRequest, IAmIdleRequest, IDocumentedRequest,
)
from roboco.services.gateway.choreographer import Choreographer

router = APIRouter(prefix="/api/v2/flow/documenter", tags=["v2-flow-documenter"])


@router.post("/give_me_work")
async def doc_give_me_work(_: GiveMeWorkRequest, x_agent_id: UUID = Header(..., alias="X-Agent-ID"),
                            c: Choreographer = Depends(get_choreographer)):
    env = await c.give_me_work(x_agent_id); return env.as_dict()


@router.post("/claim_doc_task")
async def doc_claim(body: ClaimDocTaskRequest, x_agent_id: UUID = Header(..., alias="X-Agent-ID"),
                     c: Choreographer = Depends(get_choreographer)):
    env = await c.claim_doc_task(x_agent_id, body.task_id); return env.as_dict()


@router.post("/i_documented")
async def doc_done(body: IDocumentedRequest, x_agent_id: UUID = Header(..., alias="X-Agent-ID"),
                    c: Choreographer = Depends(get_choreographer)):
    env = await c.i_documented(x_agent_id, body.task_id, body.notes, body.files); return env.as_dict()


@router.post("/i_am_idle")
async def doc_idle(_: IAmIdleRequest, x_agent_id: UUID = Header(..., alias="X-Agent-ID"),
                    c: Choreographer = Depends(get_choreographer)):
    env = await c.i_am_idle(x_agent_id); return env.as_dict()
```

- [ ] **Step 8.3: Cell PM router**

```python
# roboco/api/routes/v2/flow_cell_pm.py
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header

from roboco.api.deps import get_choreographer
from roboco.api.schemas.v2.flow import (
    CompleteRequest, EscalateUpRequest, GiveMeWorkRequest, IAmIdleRequest,
    TriageRequest, UnblockRequest,
)
from roboco.services.gateway.choreographer import Choreographer

router = APIRouter(prefix="/api/v2/flow/cell_pm", tags=["v2-flow-cell-pm"])


@router.post("/triage")
async def triage(_: TriageRequest, x_agent_id: UUID = Header(..., alias="X-Agent-ID"),
                  c: Choreographer = Depends(get_choreographer)):
    env = await c.triage(x_agent_id); return env.as_dict()


@router.post("/unblock")
async def unblock(body: UnblockRequest, x_agent_id: UUID = Header(..., alias="X-Agent-ID"),
                   c: Choreographer = Depends(get_choreographer)):
    env = await c.unblock(x_agent_id, body.task_id, restore=body.restore); return env.as_dict()


@router.post("/complete")
async def complete(body: CompleteRequest, x_agent_id: UUID = Header(..., alias="X-Agent-ID"),
                    c: Choreographer = Depends(get_choreographer)):
    env = await c.complete(x_agent_id, body.task_id, body.notes); return env.as_dict()


@router.post("/escalate_up")
async def escalate_up(body: EscalateUpRequest, x_agent_id: UUID = Header(..., alias="X-Agent-ID"),
                        c: Choreographer = Depends(get_choreographer)):
    env = await c.escalate_up(x_agent_id, body.task_id, body.reason); return env.as_dict()


@router.post("/give_me_work")
async def cell_pm_give_me_work(_: GiveMeWorkRequest, x_agent_id: UUID = Header(..., alias="X-Agent-ID"),
                                c: Choreographer = Depends(get_choreographer)):
    env = await c.give_me_work(x_agent_id); return env.as_dict()


@router.post("/i_am_idle")
async def cell_pm_idle(_: IAmIdleRequest, x_agent_id: UUID = Header(..., alias="X-Agent-ID"),
                        c: Choreographer = Depends(get_choreographer)):
    env = await c.i_am_idle(x_agent_id); return env.as_dict()
```

- [ ] **Step 8.4: Main PM router** (similar pattern, swap `triage` for `triage_all`)

```python
# roboco/api/routes/v2/flow_main_pm.py
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header

from roboco.api.deps import get_choreographer
from roboco.api.schemas.v2.flow import (
    CompleteRequest, EscalateUpRequest, IAmIdleRequest, TriageRequest,
)
from roboco.services.gateway.choreographer import Choreographer

router = APIRouter(prefix="/api/v2/flow/main_pm", tags=["v2-flow-main-pm"])


@router.post("/triage_all")
async def triage_all(_: TriageRequest, x_agent_id: UUID = Header(..., alias="X-Agent-ID"),
                      c: Choreographer = Depends(get_choreographer)):
    env = await c.triage_all(x_agent_id); return env.as_dict()


@router.post("/complete")
async def main_pm_complete(body: CompleteRequest, x_agent_id: UUID = Header(..., alias="X-Agent-ID"),
                            c: Choreographer = Depends(get_choreographer)):
    env = await c.main_pm_complete(x_agent_id, body.task_id, body.notes); return env.as_dict()


@router.post("/escalate_up")
async def main_pm_escalate_up(body: EscalateUpRequest, x_agent_id: UUID = Header(..., alias="X-Agent-ID"),
                               c: Choreographer = Depends(get_choreographer)):
    env = await c.escalate_up(x_agent_id, body.task_id, body.reason); return env.as_dict()


@router.post("/i_am_idle")
async def main_pm_idle(_: IAmIdleRequest, x_agent_id: UUID = Header(..., alias="X-Agent-ID"),
                        c: Choreographer = Depends(get_choreographer)):
    env = await c.i_am_idle(x_agent_id); return env.as_dict()
```

- [ ] **Step 8.5: Mount routers**

```python
# roboco/api/__init__.py
from roboco.api.routes.v2 import flow_doc, flow_cell_pm, flow_main_pm
app.include_router(flow_doc.router)
app.include_router(flow_cell_pm.router)
app.include_router(flow_main_pm.router)
```

- [ ] **Step 8.6: Integration tests**

```python
# tests/integration/v2/test_flow_doc.py
@pytest.mark.asyncio
async def test_claim_doc_task_returns_evidence(client, doc_agent, awaiting_doc_task_with_pr):
    r = await client.post(
        "/api/v2/flow/documenter/claim_doc_task",
        headers={"X-Agent-ID": str(doc_agent.id)},
        json={"task_id": str(awaiting_doc_task_with_pr.id)},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["evidence"]["pr_number"] == awaiting_doc_task_with_pr.pr_number
```

```python
# tests/integration/v2/test_flow_cell_pm.py
@pytest.mark.asyncio
async def test_complete_auto_merges(client, cell_pm_agent, awaiting_pm_review_task_with_pr, mocker):
    # mock GitHub PR merge to avoid hitting real GH in tests
    mocker.patch("roboco.services.git.GitService.pr_merge",
                  return_value={"merged": True, "merge_commit_sha": "abc123"})
    r = await client.post(
        "/api/v2/flow/cell_pm/complete",
        headers={"X-Agent-ID": str(cell_pm_agent.id)},
        json={"task_id": str(awaiting_pm_review_task_with_pr.id), "notes": "ok"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed"
```

```python
# tests/integration/v2/test_flow_main_pm.py
@pytest.mark.asyncio
async def test_main_pm_complete_escalates_to_ceo(client, main_pm_agent, root_task_awaiting_review, mocker):
    mocker.patch("roboco.services.git.GitService.create_pr",
                  return_value={"pr_number": 99, "pr_url": "https://x/y/pull/99"})
    r = await client.post(
        "/api/v2/flow/main_pm/complete",
        headers={"X-Agent-ID": str(main_pm_agent.id)},
        json={"task_id": str(root_task_awaiting_review.id), "notes": "shipping"},
    )
    body = r.json()
    assert body["status"] == "awaiting_ceo_approval"
```

- [ ] **Step 8.7: Run tests + Commit**

Run: `uv run pytest tests/integration/v2/test_flow_doc.py tests/integration/v2/test_flow_cell_pm.py tests/integration/v2/test_flow_main_pm.py -v`

```bash
git add roboco/api/routes/v2/ roboco/api/schemas/v2/flow.py roboco/api/__init__.py tests/integration/v2/
git commit -m "feat(api/v2): add /api/v2/flow/{documenter,cell_pm,main_pm}/* endpoints"
```

---

## Task 9: Update `roboco-flow` MCP server with Doc/PM verbs

**Files:**
- Modify: `roboco/mcp/flow_server.py`

- [ ] **Step 9.1: Register Doc + Cell PM + Main PM verbs**

Append to `flow_server.py`:

```python
# Doc verbs
@mcp.tool()
def claim_doc_task(task_id: str) -> dict:
    """Doc: claim a task in awaiting_documentation state."""
    return _post(_role_path("claim_doc_task"), {"task_id": task_id})


@mcp.tool()
def i_documented(task_id: str, notes: str, files: list[str]) -> dict:
    """Doc: mark documentation complete. files=['<doc-path>', ...]."""
    return _post(_role_path("i_documented"), {"task_id": task_id, "notes": notes, "files": files})


# PM verbs (Cell PM and Main PM share triage/unblock/complete; main PM also has triage_all)
@mcp.tool()
def triage() -> dict:
    """PM: get the most important task to act on next."""
    return _post(_role_path("triage"), {})


@mcp.tool()
def triage_all() -> dict:
    """Main PM: triage across all teams."""
    return _post(_role_path("triage_all"), {})


@mcp.tool()
def unblock(task_id: str, restore: bool = True) -> dict:
    """PM: unblock a task. restore=True (default) restores pre_block_state."""
    return _post(_role_path("unblock"), {"task_id": task_id, "restore": restore})


@mcp.tool()
def complete(task_id: str, notes: str) -> dict:
    """PM: complete a task. Cell PM: auto-merges leaf PR. Main PM: opens master PR + escalates to CEO."""
    return _post(_role_path("complete"), {"task_id": task_id, "notes": notes})


@mcp.tool()
def escalate_up(task_id: str, reason: str) -> dict:
    """PM/Doc/Dev: escalate to your role's escalation target."""
    return _post(_role_path("escalate_up"), {"task_id": task_id, "reason": reason})
```

Update the `implemented` set in `_register_role_specific_tools`:

```python
implemented = {
    # dev
    "give_me_work", "i_will_work_on", "i_have_committed",
    "i_am_done", "i_am_blocked", "i_am_idle",
    # qa
    "claim_review", "pass", "fail",
    # doc
    "claim_doc_task", "i_documented",
    # pm
    "triage", "triage_all", "unblock", "complete", "escalate_up",
}
```

- [ ] **Step 9.2: Smoke test**

Spawn one of each role; verify only role-appropriate verbs are usable. (HTTP API still rejects role mismatches — the MCP exposes all but the API gates by `target_role` matching agent's actual role.)

- [ ] **Step 9.3: Commit**

```bash
git add roboco/mcp/flow_server.py
git commit -m "feat(mcp): add Doc + PM verbs to roboco-flow MCP server (claim_doc_task, i_documented, triage, triage_all, unblock, complete, escalate_up)"
```

---

## Task 10: Slim role prompts — Doc, Cell PM, Main PM

**Files:**
- Modify: `agents/prompts/roles/documenter.md`, `cell_pm.md`, `main_pm.md`

- [ ] **Step 10.1: documenter.md**

```markdown
# Documenter

You write documentation for completed work. You document — you don't develop or merge.

## Who you are
- Team: {team}    Workspace: /data/workspaces/{project}/{team}/{your-slug}/

## Your verbs (already loaded — no ToolSearch needed)
- `give_me_work()` — returns a task in awaiting_documentation or `idle`
- `claim_doc_task(task_id)` — claim. **Response includes pr_url, files_changed, dev_summary inline.**
- `commit(message)` — commit your doc changes (auto-prefixed [task-id])
- `note(text, scope?)` — journal
- `i_documented(task_id, notes, files)` — mark docs complete; `files=['<doc-path>', ...]`; notes >= 20 chars
- `say(channel, text)` / `dm(recipient, text)` — comms
- `evidence(task_id)` — fetch full diff if you need to inspect
- `i_am_idle()` — done for now

## Ground rules
- The dev's PR diff is in `claim_doc_task`'s response — read it. Don't go grepping for what changed.
- Edit/Write limited to your workspace. Commit your doc files there.
- `i_documented` server-side requires notes >= 20 chars + at least one file in `files`.
- Errors include a `remediate` field — follow it.
```

- [ ] **Step 10.2: cell_pm.md**

```markdown
# Cell PM

You triage your cell's work, unblock blocked tasks, and complete (merge) tasks ready for review.

## Who you are
- Team: {team}    Workspace: /data/workspaces/{project}/{team}/{your-slug}/
- Escalation target: main-pm

## Your verbs (already loaded — no ToolSearch needed)
- `triage()` — returns the highest-priority task to act on (blocked > awaiting_pm_review)
- `unblock(task_id, restore=True)` — unblock. With restore=True (default), task returns to its pre-block state.
- `complete(task_id, notes)` — mark a task complete. **Auto-merges the leaf PR into the parent task branch.**
- `escalate_up(task_id, reason)` — escalate to Main PM
- `note(text, scope?)` — journal. Required: `scope='decision'` before unblock/complete/escalate_up.
- `say(channel, text)` / `dm(recipient, text)` — comms
- `evidence(task_id)` — inspect a task's PR + commits + diff
- `give_me_work()` / `i_am_idle()` — like other roles

## Ground rules
- Complete is irreversible (merge happens). Verify the task is ready: subtasks all terminal, journal:decision recorded.
- Errors include a `remediate` field — follow it.
- Don't bypass the gate. The system catches missing tracing.
```

- [ ] **Step 10.3: main_pm.md**

```markdown
# Main PM

You coordinate across cells, open root-task PRs to master, and escalate to CEO.

## Who you are
- Team: board    Workspace: /data/workspaces/{project}/board/main-pm/
- Escalation target: ceo

## Your verbs (already loaded)
- `triage_all()` — across all teams (blocked > awaiting_pm_review)
- `unblock(task_id, restore=True)` — same as Cell PM
- `complete(task_id, notes)` — for root tasks: opens master PR if not already open, then escalates to CEO
- `escalate_up(task_id, reason)` — escalate to CEO directly
- `note(text, scope?)` — journal. Required: `scope='decision'` before complete/escalate_up.
- `say(channel, text)` / `dm(recipient, text)` — comms
- `evidence(task_id)` — inspect a task
- `give_me_work()` / `i_am_idle()`

## Ground rules
- Main PM only completes ROOT tasks (no parent_task_id). Cell PMs complete their own scope.
- After your `complete`, the task is in awaiting_ceo_approval — CEO acts via UI.
- Errors include a `remediate` field — follow it.
```

- [ ] **Step 10.4: Commit**

```bash
git add agents/prompts/roles/documenter.md agents/prompts/roles/cell_pm.md agents/prompts/roles/main_pm.md
git commit -m "docs(prompts): rewrite Doc, Cell PM, and Main PM role prompts for gateway verbs"
```

---

## Task 11: Enable gateway flag for Doc, Cell PM, Main PM

**Files:**
- Modify: `roboco/runtime/orchestrator.py`

- [ ] **Step 11.1: Extend the role set**

```python
GATEWAY_ENABLED_ROLES = {"developer", "qa", "documenter", "cell_pm", "main_pm"}
```

- [ ] **Step 11.2: Verify all four roles get the manifest mounted**

Spawn one of each. Verify `/app/tool-manifest.json` mounted, manifest has the right verbs.

- [ ] **Step 11.3: Commit**

```bash
git add roboco/runtime/orchestrator.py
git commit -m "feat(runtime): enable gateway flag for Doc, Cell PM, and Main PM roles (Phase 3 cutover)"
```

---

## Task 12: Full pending → completed integration test

**Files:**
- Create: `tests/integration/v2/test_full_pending_to_completed.py`

- [ ] **Step 12.1: Write the end-to-end test**

```python
# tests/integration/v2/test_full_pending_to_completed.py
"""End-to-end: a single task threads through dev -> QA -> doc -> Cell PM -> Main PM."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
@pytest.mark.integration
async def test_pending_to_awaiting_ceo_approval(
    client: AsyncClient,
    seeded_project,
    seeded_agents,  # dev, qa, doc, cell_pm, main_pm pre-existing
    mocker,
):
    """Drive a task pending -> in_progress -> ... -> awaiting_ceo_approval through the gateway."""
    # Stub external GH calls
    mocker.patch("roboco.services.git.GitService.push", return_value=None)
    mocker.patch(
        "roboco.services.git.GitService.create_pr",
        return_value={"pr_number": 100, "pr_url": "https://github.com/x/y/pull/100"},
    )
    mocker.patch(
        "roboco.services.git.GitService.pr_merge",
        return_value={"merged": True, "merge_commit_sha": "merge-abc"},
    )
    mocker.patch("roboco.services.git.GitService.diff", return_value="+++ diff content")
    mocker.patch("roboco.services.git.GitService.pr_target", return_value="master")

    dev = seeded_agents["dev"]
    qa = seeded_agents["qa"]
    doc = seeded_agents["doc"]
    cell_pm = seeded_agents["cell_pm"]
    main_pm = seeded_agents["main_pm"]

    # 0. PM creates the root task (or it already exists in fixture)
    root_task = seeded_project["root_task"]
    leaf_task = seeded_project["leaf_task"]  # parent_task_id = root_task.id, assigned_to = dev

    # 1. Dev: give_me_work -> i_will_work_on
    r = await client.post(
        "/api/v2/flow/dev/give_me_work",
        headers={"X-Agent-ID": str(dev.id)},
        json={},
    )
    assert r.json()["task_id"] == str(leaf_task.id)

    r = await client.post(
        "/api/v2/flow/dev/i_will_work_on",
        headers={"X-Agent-ID": str(dev.id)},
        json={"task_id": str(leaf_task.id), "plan": "edit README and add timestamp"},
    )
    assert r.json()["status"] == "in_progress"

    # 2. Dev: commit + note(reflect) + i_am_done
    r = await client.post(
        "/api/v2/do/commit",
        headers={"X-Agent-ID": str(dev.id)},
        json={"message": "feat(readme): add updated timestamp comment"},
    )
    assert r.json()["error"] is None

    r = await client.post(
        "/api/v2/do/note",
        headers={"X-Agent-ID": str(dev.id)},
        json={"text": "Reflected: implementation matches the spec; timestamp comment added.", "scope": "reflect"},
    )
    assert r.json()["error"] is None

    r = await client.post(
        "/api/v2/flow/dev/i_am_done",
        headers={"X-Agent-ID": str(dev.id)},
        json={"task_id": str(leaf_task.id), "notes": "all done"},
    )
    body = r.json()
    assert body["status"] == "awaiting_qa", body

    # 3. QA: claim_review -> pass
    r = await client.post(
        "/api/v2/flow/qa/claim_review",
        headers={"X-Agent-ID": str(qa.id)},
        json={"task_id": str(leaf_task.id)},
    )
    body = r.json()
    assert body["evidence"]["pr_number"] == 100  # inline evidence — kills #15

    r = await client.post(
        "/api/v2/do/note",
        headers={"X-Agent-ID": str(qa.id)},
        json={"text": "Reviewed PR; all acceptance criteria addressed.", "scope": "learning"},
    )
    assert r.json()["error"] is None

    long_notes = (
        "Reviewed PR #100 carefully. Branch convention correct. Commit message includes task ID. "
        "README diff matches spec. No security concerns. All acceptance criteria are addressed."
    )
    r = await client.post(
        "/api/v2/flow/qa/pass",
        headers={"X-Agent-ID": str(qa.id)},
        json={"task_id": str(leaf_task.id), "notes": long_notes},
    )
    assert r.json()["status"] == "awaiting_documentation"

    # 4. Doc: claim_doc_task -> i_documented
    r = await client.post(
        "/api/v2/flow/documenter/claim_doc_task",
        headers={"X-Agent-ID": str(doc.id)},
        json={"task_id": str(leaf_task.id)},
    )
    assert r.json()["error"] is None

    r = await client.post(
        "/api/v2/flow/documenter/i_documented",
        headers={"X-Agent-ID": str(doc.id)},
        json={
            "task_id": str(leaf_task.id),
            "notes": "Wrote backend/guides/feature-x.md with usage and config sections.",
            "files": ["backend/guides/feature-x.md"],
        },
    )
    assert r.json()["status"] == "awaiting_pm_review"

    # 5. Cell PM: complete (auto-merge)
    r = await client.post(
        "/api/v2/do/note",
        headers={"X-Agent-ID": str(cell_pm.id)},
        json={"text": "Decision: approve and merge.", "scope": "decision"},
    )
    assert r.json()["error"] is None

    r = await client.post(
        "/api/v2/flow/cell_pm/complete",
        headers={"X-Agent-ID": str(cell_pm.id)},
        json={"task_id": str(leaf_task.id), "notes": "Approved and merged"},
    )
    body = r.json()
    assert body["status"] == "completed"

    # 6. Main PM: complete root (opens master PR + escalates to CEO)
    r = await client.post(
        "/api/v2/do/note",
        headers={"X-Agent-ID": str(main_pm.id)},
        json={"text": "Root task ready for prod.", "scope": "decision"},
    )
    assert r.json()["error"] is None

    r = await client.post(
        "/api/v2/flow/main_pm/complete",
        headers={"X-Agent-ID": str(main_pm.id)},
        json={"task_id": str(root_task.id), "notes": "Ready"},
    )
    body = r.json()
    assert body["status"] == "awaiting_ceo_approval"

    # CEO would approve via UI; not tested here.
```

- [ ] **Step 12.2: Run the test**

Run: `uv run pytest tests/integration/v2/test_full_pending_to_completed.py -v`
Expected: PASS.

- [ ] **Step 12.3: Commit**

```bash
git add tests/integration/v2/test_full_pending_to_completed.py
git commit -m "test(integration): full pending->awaiting_ceo_approval test through dev/QA/doc/cell-PM/main-PM gateway path"
```

---

## Task 13: Smoke test — full pending → completed in real stack

- [ ] **Step 13.1: Reset state**

```bash
ssh renzof@renzof-nas.local 'cd /volume1/roboco/ && bash scripts/reset_runtime_state.sh'
```

- [ ] **Step 13.2: Run the smoke-test scenario**

Create a smoke-test task. Watch:
1. Dev spawns → claims → commits → done
2. QA spawns → claim_review → response has `evidence.pr_url` inline → passes
3. Doc spawns → claim → writes docs → i_documented
4. Cell PM spawns → complete → auto-merges leaf PR
5. Main PM spawns → complete → opens master PR + awaiting_ceo_approval
6. CEO approves manually via UI → final merge to master + complete

- [ ] **Step 13.3: Verify each fix landed**

- #15: QA's response payload from `claim_review` has `pr_number` and `pr_url`; QA passes (no false fail).
- #22: Cell PM's `complete` calls `pr_merge` (visible in logs); task transitions completed.
- #23: If a task got blocked and unblocked during this run, verify the unblock returned to the right state.
- #11: Audit log entries for transitions all have non-NULL `agent_id`.
- #17, #24: No phantom respawns of QA after task moves on; no thundering herd of agents on a single task.

- [ ] **Step 13.4: Tag**

```bash
git tag phase-3-doc-pms-complete
git push origin phase-3-doc-pms-complete
```

---

## Self-Review

1. **Spec coverage:** Doc verbs (Task 1), PM triage/unblock/complete/escalate (Tasks 2-7), API endpoints (Task 8), MCP wiring (Task 9), prompts (Task 10), gateway flag (Task 11), full integration test (Task 12), smoke test (Task 13). All Phase 3 spec items covered. ✓
2. **Placeholder scan:** Service-layer additions (`unblock_with_restore`, `cell_pm_complete`, `escalate_to_ceo`, `pr_merge`, `pr_target`) call out the existing pattern to follow but require the engineer to wire to your codebase's actual repository methods. The pattern is clear (audit-actor never NULL, transactional update). No "TBD" left dangling. ✓
3. **Type consistency:** `complete()` dispatcher routes by `agent.role` to `cell_pm_complete` or `main_pm_complete`. Both return `Envelope`. `merge_chain.parent_branch_for(branch)` from Phase 0 used in Task 4. `unblock_with_restore` matches the column names from Phase 0 migration. ✓
4. **Spec alignment:** Auto-merge chain (Cell PM → parent branch, Main PM → master + CEO escalation) matches §7.2 of the spec exactly. State restoration on unblock matches §8.3. Tracing gates (journal:decision before unblock/complete/escalate_up) match §6.1. ✓
