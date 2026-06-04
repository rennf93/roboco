"""Documenter verbs (audit P2-2 second per-role split).

Mixin for ``claim_doc_task`` and ``i_documented``. Inherits typed
helpers via ``ChoreographerHelpers`` under ``TYPE_CHECKING``; runtime
class is the composed ``Choreographer``.

Tasks 22 (lifecycle canonical spec): both verbs route their role/state
gate through ``spec.can_invoke_intent``. The verb-specific helpers
(``_verify_doc_owner``, ``_check_doc_gates``) STAY — they encode the
notes-length / files-list / journal:reflect gates the spec doesn't
model. The self-review block on ``docs_complete`` lives at the atomic-
action layer (``_ATOMIC_ACTIONS["docs_complete"].self_review_block=True``)
and naturally fires when the verb body builds a Context with
``actor_slug == original_developer_slug``; no verb-body retrofits
needed.

P2 Task 10: ``_check_doc_gates`` delegates the actual requirement
checking to ``foundation.policy.tracing.check_requirements`` — the
verb→required-set mapping lives in ``VERB_REQUIREMENTS`` (single
source of truth). The hint translation lives in the shared
``_build_tracing_gap`` on Choreographer.

``claim_doc_task`` composes ``("claim", "start")`` in the spec, but
the runtime semantic is "documenter inspects, status stays at
awaiting_documentation". The verb body therefore owns dispatch via
``task.doc_claim`` (mirroring ``claim_review``'s pattern in qa.py) so
the subsequent ``i_documented`` call still finds the task in
awaiting_documentation as the spec's ``docs_complete`` source-status
requires.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from roboco.config import settings
from roboco.foundation.policy import lifecycle as spec_module
from roboco.foundation.policy import tracing as _tr
from roboco.models.task import DocRef
from roboco.services.gateway.envelope import Envelope
from roboco.services.gateway.evidence_builder import build_evidence_for_task

if TYPE_CHECKING:
    from uuid import UUID

    from roboco.services.gateway.choreographer._protocol import ChoreographerHelpers

    _Base = ChoreographerHelpers
else:
    _Base = object


def _doc_refs_for(files: list[str], agent_id: UUID) -> list[dict[str, Any]]:
    """DocRef-shaped dicts for ``Task.documents`` (a JSON column).

    ``i_documented`` receives a flat list of file paths, but
    ``Task.documents`` is ``list[DocRef]`` persisted as dicts — readers do
    ``DocRef(**d)`` / ``d["path"]`` and the doc indexer does ``d.get``.
    Stamping bare strings 500s ``list_docs`` and breaks indexing (#169).
    """
    now = datetime.now(UTC).isoformat()
    slug = str(agent_id)
    return [
        DocRef(
            path=f,
            title=Path(f).name,
            doc_type="doc",
            created_by=slug,
            created_at=now,
            updated_by=slug,
            updated_at=now,
        ).model_dump()
        for f in files
    ]


def _extract_original_developer(task: Any) -> str | None:
    """Pull the original_developer slug out of a task's quick_context, if any.

    Mirrors ``_impl._extract_original_developer``. Lives here too so
    the Doc mixin doesn't depend on ``_impl``'s module-level helper —
    the spec's self-review block reads ``ctx.original_developer_slug``
    and the mixin builds the Context.
    """
    qc = getattr(task, "quick_context", None) or ""
    marker = "original_developer:"
    if marker not in qc:
        return None
    tail = qc.split(marker, 1)[1].strip()
    if not tail:
        return None
    return tail.split()[0] or None


class DocMixin(_Base):
    """Documenter-role verbs."""

    async def _claim_doc_task_spec_gate(
        self, doc_agent_id: UUID, task_id: UUID, t: Any
    ) -> tuple[Envelope | None, Any, str, dict[str, Any]]:
        """Run the spec gate for claim_doc_task.

        Returns (rejection, agent, role_str, briefing). Builds the
        Context with ``actor_slug`` + ``original_developer_slug`` so the
        spec evaluates self-review correctly even though ``claim``
        doesn't block self-review (the documenter-claims-own-doc-task
        case is a non-issue at claim time but the field is set anyway
        for downstream actions).
        """
        agent = await self.task.agent_for(doc_agent_id)
        role_str = str(agent.role) if agent is not None else "documenter"
        briefing = await self._briefing_for(doc_agent_id, task_id)
        try:
            role = spec_module.Role(role_str)
        except ValueError:
            rejection = await self._emit_rejection(
                Envelope.not_authorized(
                    message=f"unknown role '{role_str}'",
                    remediate="role is not declared in the lifecycle spec",
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
                agent_id=doc_agent_id,
                task_id=task_id,
                verb="claim_doc_task",
            )
            return rejection, agent, role_str, briefing
        spec_ctx = spec_module.Context(
            actor_id=doc_agent_id,
            actor_slug=getattr(agent, "slug", None) if agent is not None else None,
            original_developer_slug=_extract_original_developer(t),
        )
        decision = spec_module.can_invoke_intent(role, "claim_doc_task", t, spec_ctx)
        if not decision.allowed:
            rejection = await self._emit_rejection(
                Envelope.from_decision(decision, briefing=briefing).with_introspection(
                    task=t, role=role_str
                ),
                agent_id=doc_agent_id,
                task_id=task_id,
                verb="claim_doc_task",
            )
            return rejection, agent, role_str, briefing
        return None, agent, role_str, briefing

    async def claim_doc_task(self, doc_agent_id: UUID, task_id: UUID) -> Envelope:
        """Documenter claims task in awaiting_documentation; returns evidence inline.

        Spec gate runs first and enforces role membership (documenter
        only) plus the composed ``claim`` action's source-status
        constraint (AWAITING_DOCUMENTATION is one of the allowed
        sources). After the gate accepts, the verb body owns dispatch
        via ``task.doc_claim`` (specialized claim that keeps status at
        AWAITING_DOCUMENTATION so the downstream ``docs_complete``
        source-status requirement still matches). The behavioral claim
        guards (already_active / paused) still run after the spec gate.
        """
        t = await self.task.get(task_id)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=doc_agent_id,
                task_id=task_id,
                verb="claim_doc_task",
            )
        (
            spec_rejection,
            _agent,
            role_str,
            briefing,
        ) = await self._claim_doc_task_spec_gate(doc_agent_id, task_id, t)
        if spec_rejection is not None:
            return spec_rejection

        guard = await self._run_claim_guards(
            agent_id=doc_agent_id,
            task=t,
        )
        if guard:
            guard.with_introspection(task=t, role=role_str)
            return await self._emit_rejection(
                self._with_briefing(guard, briefing),
                agent_id=doc_agent_id,
                task_id=task_id,
                verb="claim_doc_task",
            )

        # Verb body owns dispatch — claim_doc_task's spec composes=("claim",
        # "start") but doc_claim is the runtime-correct specialized form
        # that keeps status at AWAITING_DOCUMENTATION. See module docstring.
        t = await self.task.doc_claim(doc_agent_id, task_id)
        # Task #162: the documenter's clone is separate from the dev's;
        # the task branch already exists (dev created it) so no checkout
        # ran in the doc's workspace. Put the doc on the task branch now
        # so roboco_docs_write / commit don't fail BRANCH_MISMATCH.
        # Best-effort — a checkout hiccup must not fail the claim.
        if t.branch_name:
            with contextlib.suppress(Exception):
                await self.git.checkout_branch_in_agent_workspace(
                    t.branch_name, actor_agent_id=doc_agent_id
                )
        ev = await self._claim_doc_evidence(t, task_id)
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=spec_module._INTENT_VERBS["claim_doc_task"].next_hint(t),
            evidence=ev,
            context_briefing=briefing,
        ).with_introspection(task=t, role=role_str)

    async def _claim_doc_evidence(self, task: Any, task_id: UUID) -> dict[str, Any]:
        """Build the evidence dict surfaced inline on claim_doc_task ok envelopes.

        Task #154: files_changed sourced from git (authoritative) instead
        of ``work_session.files_modified``, which the gateway commit()
        does not populate. The docs writer sees an accurate file list.
        """
        files_changed: list[str] = []
        diff = ""
        if task.branch_name:
            diff = await self.git.diff(branch_name=task.branch_name)
            files_changed = await self.git.list_changed_files(
                branch_name=task.branch_name
            )
        journal_highlights = await self.evidence_repo.journal_highlights_for_task(
            task_id
        )
        return build_evidence_for_task(
            task,
            journal_highlights=journal_highlights,
            files_changed=files_changed,
            pr_diff_summary=diff,
        ).as_dict()

    async def _verify_doc_owner(
        self, doc_agent_id: UUID, task_id: UUID
    ) -> tuple[Envelope | None, Any]:
        """Lookup task + verify doc agent is assignee. Returns (rejection, task)."""
        t = await self.task.get(task_id)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=doc_agent_id,
                task_id=task_id,
                verb="i_documented",
            ), None
        if t.assigned_to != doc_agent_id:
            return await self._emit_rejection(
                Envelope.not_authorized(
                    message="not assigned to you",
                    remediate="claim it via claim_doc_task(task_id) first",
                    context_briefing=await self._briefing_for(doc_agent_id, task_id),
                ).with_introspection(task=t, role="documenter"),
                agent_id=doc_agent_id,
                task_id=task_id,
                verb="i_documented",
            ), None
        return None, t

    async def _check_doc_gates(
        self,
        doc_agent_id: UUID,
        task_id: UUID,
        notes: str,
        files: list[str],
        task: Any,
    ) -> Envelope | None:
        """i_documented field + journal gates via foundation.policy.tracing.

        Returns rejection envelope or None on pass. The required-set for
        ``i_documented`` (DOCS_FILES_NON_EMPTY + DOCS_NOTES_MIN_CHARS +
        JOURNAL_REFLECT) lives in ``VERB_REQUIREMENTS``.

        The verb's ``notes`` argument and ``files`` list haven't been
        persisted to the task yet (the spec runner / verb body writes
        them via the atomic action / pre-dispatch stamp), so we thread
        them through a SimpleNamespace shim with the minimal attributes
        the foundation checkers read off the task object (dev_notes +
        documents — see foundation.policy.tracing._check_docs_notes_min_chars
        and _check_docs_files_non_empty).
        """
        has_reflect = await self.journal.has_reflect_for_task(doc_agent_id, task_id)
        task_view = SimpleNamespace(
            dev_notes=notes,
            documents=list(files),
        )
        ctx = _tr.GateContext(
            journal_reflect_present=has_reflect,
            docs_notes_min_chars=settings.docs_notes_min_chars,
        )
        result = _tr.check_requirements(
            task=task_view,
            requirements=list(_tr.requirements_for("i_documented")),
            ctx=ctx,
        )
        if result.passed:
            return None
        return await self._emit_rejection(
            (
                await self._build_tracing_gap(doc_agent_id, task_id, result.missing)
            ).with_introspection(task=task, role="documenter"),
            agent_id=doc_agent_id,
            task_id=task_id,
            verb="i_documented",
        )

    async def _ensure_doc_reflect(
        self,
        doc_agent_id: UUID,
        task_id: UUID,
        notes: str,
        files: list[str],
    ) -> None:
        """Record the journal:reflect i_documented requires, synthesized from
        the documenter's own submission, when they didn't journal one.

        ``i_documented`` requires a journal:reflect entry (pre-gateway parity,
        VERB_REQUIREMENTS). Documenters that never call note(scope='reflect')
        used to loop on the gate's tracing_gap until the per-verb circuit
        breaker (limit 3 / 60s) locked them out, stranding the task in
        awaiting_documentation. The ``notes`` + ``files`` this verb already
        carries ARE the reflection's substance, so we write the entry from
        them — one call, no loop.

        Synthesis is skipped when (a) the agent already authored a reflect
        (theirs is richer — never clobber it) or (b) the submission is below
        the notes/files gate thresholds, so we never persist a reflect built
        from input ``_check_doc_gates`` will reject anyway; the agent retries
        with a real submission and we synthesize then.
        """
        if await self.journal.has_reflect_for_task(doc_agent_id, task_id):
            return
        if len(notes.strip()) < settings.docs_notes_min_chars or not files:
            return
        content = (
            f"## What Done\n{notes.strip()}\n\n"
            f"Documented files: {', '.join(files)}\n\n"
            "## Next Steps\n- Hand off to PM review"
        )
        title = notes.strip().split("\n", 1)[0][:200]
        await self.journal.write_entry(
            agent_id=doc_agent_id,
            task_id=task_id,
            scope="reflect",
            title=title,
            content=content,
        )

    async def _i_documented_spec_gate(
        self,
        doc_agent_id: UUID,
        task_id: UUID,
        owned_task: Any,
        notes: str,
        files: list[str],
    ) -> tuple[Envelope | None, Any, str, spec_module.Context, dict[str, Any]]:
        """Run the spec gate for i_documented.

        Returns (rejection, agent, role_str, spec_ctx, briefing). Builds
        the Context with ``actor_slug`` + ``original_developer_slug`` so
        the docs_complete action's ``self_review_block=True`` fires when
        the documenter is the original developer.
        """
        agent = await self.task.agent_for(doc_agent_id)
        role_str = str(agent.role) if agent is not None else "documenter"
        briefing = await self._briefing_for(doc_agent_id, task_id)
        try:
            role = spec_module.Role(role_str)
        except ValueError:
            rejection = await self._emit_rejection(
                Envelope.not_authorized(
                    message=f"unknown role '{role_str}'",
                    remediate="role is not declared in the lifecycle spec",
                    context_briefing=briefing,
                ).with_introspection(task=owned_task, role=role_str),
                agent_id=doc_agent_id,
                task_id=task_id,
                verb="i_documented",
            )
            return rejection, agent, role_str, spec_module.Context(), briefing
        spec_ctx = spec_module.Context(
            actor_id=doc_agent_id,
            actor_slug=getattr(agent, "slug", None) if agent is not None else None,
            original_developer_slug=_extract_original_developer(owned_task),
            notes=notes,
            files=tuple(files),
        )
        decision = spec_module.can_invoke_intent(
            role, "i_documented", owned_task, spec_ctx
        )
        if not decision.allowed:
            rejection = await self._emit_rejection(
                Envelope.from_decision(decision, briefing=briefing).with_introspection(
                    task=owned_task, role=role_str
                ),
                agent_id=doc_agent_id,
                task_id=task_id,
                verb="i_documented",
            )
            return rejection, agent, role_str, spec_ctx, briefing
        return None, agent, role_str, spec_ctx, briefing

    async def i_documented(
        self,
        doc_agent_id: UUID,
        task_id: UUID,
        notes: str,
        files: list[str],
    ) -> Envelope:
        """Documenter signals docs complete.

        Transitions awaiting_documentation → awaiting_pm_review.

        Spec gate runs first and enforces role membership (documenter)
        plus the composed ``docs_complete`` action's source-status
        (AWAITING_DOCUMENTATION), task_type, and self-review block (the
        atomic action's ``self_review_block=True`` rejects when the
        documenter is the original developer of the task). After the
        spec gate accepts, the verb-specific gates stay (ownership via
        ``_verify_doc_owner`` and field + journal gates via
        ``_check_doc_gates``); none are modelled by the spec.
        Files are stamped onto ``task.documents`` before the runner
        dispatches ``docs_complete`` so the indexer sees them, then the
        verb body reassigns to the cell PM for handoff.
        """
        rejection, owned_task = await self._verify_doc_owner(doc_agent_id, task_id)
        if rejection is not None:
            return rejection
        (
            spec_rejection,
            agent,
            role_str,
            spec_ctx,
            briefing,
        ) = await self._i_documented_spec_gate(
            doc_agent_id, task_id, owned_task, notes, files
        )
        if spec_rejection is not None:
            return spec_rejection

        # Satisfy the journal:reflect requirement from this submission's
        # notes + files when the documenter didn't journal one themselves —
        # otherwise the gate's tracing_gap loops into the circuit breaker.
        await self._ensure_doc_reflect(doc_agent_id, task_id, notes, files)

        gate_rejection = await self._check_doc_gates(
            doc_agent_id, task_id, notes, files, owned_task
        )
        if gate_rejection is not None:
            return gate_rejection

        # TaskService.docs_complete signature is (task_id, doc_notes); it
        # reads task.documents for indexing. Stamp the file list onto the
        # task before the runner dispatches docs_complete so the indexer
        # sees it.
        existing = await self.task.get(task_id)
        if existing is not None:
            existing.documents = _doc_refs_for(files, doc_agent_id)
            await self.task.session.flush()

        runner = self._verb_runner()
        try:
            t = await runner.run_intent("i_documented", owned_task, agent, spec_ctx)
        except Exception as exc:
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message=f"verb runner failed: {exc}",
                    remediate="check workspace + retry; if persistent, escalate",
                    context_briefing=briefing,
                ).with_introspection(task=owned_task, role=role_str),
                agent_id=doc_agent_id,
                task_id=task_id,
                verb="i_documented",
            )

        await self._handoff_to_cell_pm(doc_agent_id, task_id, t)
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=spec_module._INTENT_VERBS["i_documented"].next_hint(t),
            context_briefing=briefing,
        ).with_introspection(task=t, role=role_str)

    async def _handoff_to_cell_pm(
        self, doc_agent_id: UUID, task_id: UUID, task: Any
    ) -> None:
        """Reassign + a2a-notify the cell PM after docs_complete dispatch.

        Side effect outside the spec; lives here so ``i_documented``'s
        body stays under the cyclomatic-complexity ceiling.
        """
        pm_agent = await self.task.cell_pm_for_team(task.team)
        if pm_agent is None:
            return
        await self.task.reassign(task_id, pm_agent.id)
        await self.a2a.send(
            from_agent=doc_agent_id,
            to_agent=pm_agent.id,
            skill="task_management",
            task_id=task_id,
            body=f"Docs complete for {task.id}. Ready for PM review + merge.",
        )
