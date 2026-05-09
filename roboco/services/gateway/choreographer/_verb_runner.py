"""Composed-actions runner with atomicity invariant.

Used by every choreographer verb body that has a non-trivial
composition. The runner:

  1. Wraps the composed atomic actions in `session.begin_nested()`
     (a SAVEPOINT). A mid-sequence failure rolls the DB back to the
     pre-verb state.
  2. Runs side effects (git push, PR creation, etc.) AFTER the
     savepoint commits, never before. Each side effect is itself
     idempotent + retryable per the open_pr atomicity pattern.

Preconditions are NOT this runner's concern — `spec.can_invoke_intent`
runs before the verb body, so by the time the runner is called the
Decision is `allow`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from roboco.lifecycle import spec

_AtomicHandler = Callable[[Any, Any, Any, spec.Context], Awaitable[Any]]
_SideEffectHandler = Callable[[Any, Any, Any], Awaitable[Any]]


@dataclass(frozen=True)
class VerbRunner:
    """Lightweight composition runner; one instance per choreographer."""

    task_service: Any
    git_service: Any

    async def run_intent(
        self,
        intent_name: str,
        task: Any,
        agent: Any,
        context: spec.Context,
    ) -> Any:
        """Run composed atomic actions in order, then side effects.

        Returns the final task object (post-composition). Raises whatever
        the underlying TaskService methods raise; the savepoint context
        rolls the DB back on raise.
        """
        intent = spec._INTENT_VERBS[intent_name]
        async with self.task_service.session.begin_nested():
            for action_name in intent.composes:
                task = await self._dispatch_atomic(action_name, task, agent, context)
        for side_effect_name in intent.side_effects:
            await self._dispatch_side_effect(side_effect_name, task, agent)
        return task

    async def _dispatch_atomic(
        self, action_name: str, task: Any, agent: Any, context: spec.Context
    ) -> Any:
        """Dispatch one atomic action by name. Returns the post-action task."""
        handler = self._atomic_handlers().get(action_name)
        if handler is None:
            raise ValueError(f"unknown atomic action '{action_name}'")
        return await handler(self, task, agent, context)

    async def _dispatch_side_effect(
        self, side_effect_name: str, task: Any, agent: Any
    ) -> Any:
        """Dispatch one side effect by name. Idempotent operations only."""
        handler = self._side_effect_handlers().get(side_effect_name)
        if handler is None:
            raise ValueError(f"unknown side effect '{side_effect_name}'")
        return await handler(self, task, agent)

    # -- Atomic handlers ---------------------------------------------------

    async def _do_claim(self, task: Any, agent: Any, _ctx: spec.Context) -> Any:
        return await self.task_service.claim(task.id, agent.id)

    async def _do_set_plan(self, task: Any, _agent: Any, ctx: spec.Context) -> Any:
        return await self.task_service.set_plan(task.id, ctx.plan or "")

    async def _do_start(self, task: Any, agent: Any, _ctx: spec.Context) -> Any:
        return await self.task_service.start(task.id, agent.id)

    async def _do_submit_verification(
        self, task: Any, agent: Any, ctx: spec.Context
    ) -> Any:
        return await self.task_service.submit_verification(
            agent.id, task.id, ctx.notes or ""
        )

    async def _do_submit_qa(self, task: Any, agent: Any, ctx: spec.Context) -> Any:
        return await self.task_service.submit_qa(agent.id, task.id, ctx.notes or "")

    async def _do_qa_pass(self, task: Any, agent: Any, ctx: spec.Context) -> Any:
        return await self.task_service.qa_pass(agent.id, task.id, ctx.notes or "")

    async def _do_qa_fail(self, task: Any, agent: Any, ctx: spec.Context) -> Any:
        return await self.task_service.qa_fail(
            agent.id, task.id, ctx.notes or "", list(ctx.issues)
        )

    async def _do_docs_complete(self, task: Any, _agent: Any, ctx: spec.Context) -> Any:
        return await self.task_service.docs_complete(task.id, doc_notes=ctx.notes or "")

    async def _do_complete(self, task: Any, agent: Any, ctx: spec.Context) -> Any:
        return await self.task_service.cell_pm_complete(
            agent.id, task.id, ctx.notes or ""
        )

    async def _do_submit_pm_review(
        self, task: Any, agent: Any, ctx: spec.Context
    ) -> Any:
        return await self.task_service.submit_pm_review(
            agent.id, task.id, ctx.notes or ""
        )

    async def _do_escalate_to_ceo(
        self, task: Any, agent: Any, ctx: spec.Context
    ) -> Any:
        # Use the actor's real role — escalate_to_ceo is allow-listed for
        # main_pm, product_owner, head_marketing in the spec, and the task
        # service stamps the escalator's role into the audit trail.
        agent_role = str(agent.role) if agent is not None else "main_pm"
        return await self.task_service.escalate_to_ceo(
            task_id=task.id, agent_role=agent_role, notes=ctx.notes or ""
        )

    async def _do_block(self, task: Any, agent: Any, ctx: spec.Context) -> Any:
        return await self.task_service.escalate(agent.id, task.id, ctx.notes or "")

    async def _do_unblock(self, task: Any, agent: Any, _ctx: spec.Context) -> Any:
        return await self.task_service.unblock_with_restore(
            agent.id, task.id, restore=True
        )

    async def _do_resume(self, task: Any, agent: Any, _ctx: spec.Context) -> Any:
        return await self.task_service.resume_for_agent(task.id, agent.id)

    async def _do_create_subtask(
        self, _task: Any, _agent: Any, _ctx: spec.Context
    ) -> Any:
        raise NotImplementedError(
            "create_subtask requires DelegateInputs; verb body owns dispatch"
        )

    @classmethod
    def _atomic_handlers(cls) -> dict[str, _AtomicHandler]:
        return {
            "claim": cls._do_claim,
            "set_plan": cls._do_set_plan,
            "start": cls._do_start,
            "submit_verification": cls._do_submit_verification,
            "submit_qa": cls._do_submit_qa,
            "qa_pass": cls._do_qa_pass,
            "qa_fail": cls._do_qa_fail,
            "docs_complete": cls._do_docs_complete,
            "complete": cls._do_complete,
            "submit_pm_review": cls._do_submit_pm_review,
            "escalate_to_ceo": cls._do_escalate_to_ceo,
            "block": cls._do_block,
            "unblock": cls._do_unblock,
            "resume": cls._do_resume,
            "create_subtask": cls._do_create_subtask,
        }

    # -- Side-effect handlers ---------------------------------------------

    async def _do_push_branch(self, task: Any, _agent: Any) -> Any:
        return await self.git_service.push_branch(task.branch_name)

    async def _do_create_pr(self, task: Any, _agent: Any) -> Any:
        from roboco.services.gateway.merge_chain import parent_branch_for

        parent = parent_branch_for(task.branch_name)
        return await self.git_service.create_pr(
            task.branch_name, parent=parent, is_root_pr=False
        )

    async def _do_pr_merge(self, task: Any, agent: Any) -> Any:
        from roboco.services.gateway.merge_chain import parent_branch_for

        target = parent_branch_for(task.branch_name)
        return await self.git_service.pr_merge(
            task.pr_number, target=target, actor_agent_id=agent.id
        )

    @classmethod
    def _side_effect_handlers(cls) -> dict[str, _SideEffectHandler]:
        return {
            "push_branch": cls._do_push_branch,
            "create_pr": cls._do_create_pr,
            "pr_merge": cls._do_pr_merge,
        }
