"""PM verbs: delegate and the sub-task it creates, submit_up and submit_root,
pm_give_me_work and triage, unblock and request_changes, and complete.

Role mixins composed onto ``Choreographer`` in ``__init__.py``."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, cast
from uuid import UUID

import structlog

from roboco.exceptions import MergeConflictError
from roboco.foundation.identity import slug_for_uuid_or_none
from roboco.foundation.policy import lifecycle as spec_module
from roboco.foundation.policy.batch import is_batch_root_subtask, is_batch_umbrella
from roboco.foundation.policy.content import (
    ContentValidationError,
    markers,
    validate_findings,
)
from roboco.services.base import UnauthorizedError
from roboco.services.content_notes import apply_structured_note
from roboco.services.gateway.choreographer import findings as findings_lib
from roboco.services.gateway.choreographer._impl import (
    _BLOCK_FLIP_NOTIFY_THRESHOLD,
    _OSCILLATION_TRIP_THRESHOLD,
    DelegateInputs,
    PmDecisionOutcome,
    _extract_original_developer,
)
from roboco.services.gateway.choreographer._protocol import actor_context_fields
from roboco.services.gateway.envelope import Envelope
from roboco.services.gateway.merge_chain import (
    resolve_parent_branch,
)
from roboco.services.gateway.remediation import (
    hint_for_missing_ac_coverage,
)

if TYPE_CHECKING:
    from roboco.models.base import TaskNature
    from roboco.services.gateway.choreographer._protocol import ChoreographerHelpers

    _Base = ChoreographerHelpers
else:
    _Base = object

logger = structlog.get_logger()


class DelegateMixin(_Base):
    """``delegate``: a PM creating a sub-task behind the spec, coverage,
    lifecycle, chain and assignee task-type guards, and the success envelope
    with its acceptance-criteria coverage."""

    async def delegate(
        self,
        pm_agent_id: UUID,
        parent_task_id: UUID,
        inputs: DelegateInputs,
    ) -> Envelope:
        """Create a subtask under parent_task_id with delegation-chain validation.

        Atomic: spec.can_invoke_intent runs first for the role+state gate.
        Delegate-specific gates the spec doesn't model (chain validation,
        assignee-vs-task_type, enum coercion, parent-ownership, subtask
        cap) run after the spec gate. Main PM may delegate to a Cell PM
        slug; a Cell PM may delegate to its own team's developers. The
        atomic ``create_subtask`` action is special — its handler raises
        NotImplementedError because it requires DelegateInputs — so the
        verb body owns the dispatch to ``_create_subtask_from_inputs``.
        """
        parent = await self.task.get(parent_task_id)
        if parent is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {parent_task_id} not found"),
                agent_id=pm_agent_id,
                task_id=parent_task_id,
                verb="delegate",
            )
        # Serialize same-parent delegates across the sibling-dedup read ->
        # create_subtask write critical section. The dedup guard reads the
        # parent's existing subtasks via an unlocked SELECT, then the verb
        # body creates the subtask — with no DB serialization two concurrent
        # same-parent delegates each read a duplicate-free set and each
        # create a subtask (the duplicate the guard exists to prevent). The
        # per-parent transaction-scoped advisory lock is held until the outer
        # request commits, so the second same-parent delegate blocks until
        # the first commits and its dedup read then sees the committed
        # sibling. Acquired before the first get_subtasks read (the briefing
        # context read AND the dedup sibling read) so it spans the whole
        # section. Per-parent (not per-agent) so a coordinator PM's parallel
        # root planning is not serialized — only same-parent delegates are.
        await self.task.acquire_delegate_parent_lock(parent_task_id)
        agent = await self.task.agent_for(pm_agent_id)
        role_str = str(agent.role) if agent is not None else "cell_pm"
        briefing = await self._briefing_for(pm_agent_id, parent_task_id, task=parent)
        try:
            role = spec_module.Role(role_str)
        except ValueError:
            return await self._emit_rejection(
                Envelope.not_authorized(
                    message=f"unknown role '{role_str}'",
                    remediate="role is not declared in the lifecycle spec",
                    context_briefing=briefing,
                ).with_introspection(task=parent, role=role_str),
                agent_id=pm_agent_id,
                task_id=parent_task_id,
                verb="delegate",
            )
        spec_ctx = spec_module.Context(
            actor_id=pm_agent_id,
            actor_slug=getattr(agent, "slug", None) if agent is not None else None,
            agent_team=str(agent.team) if agent is not None and agent.team else None,
            original_developer_slug=_extract_original_developer(parent),
        )
        soup = self._free_text_soup(
            checks=(
                ("title", inputs.title, 5),
                ("description", inputs.description, 10),
            )
        )
        decision = spec_module.can_invoke_intent(role, "delegate", parent, spec_ctx)
        if env := self._soup_or_decision_env(soup, decision, briefing):
            return await self._emit_rejection(
                env.with_introspection(task=parent, role=role_str),
                agent_id=pm_agent_id,
                task_id=parent_task_id,
                verb="delegate",
            )
        # The foundation/policy/task_completeness gate runs BEFORE the
        # static/lifecycle guards. Auto-fill helpers patch unambiguous fields
        # (team-from-slug, priority-from-parent), then `check(TASK_AT_CREATE,
        # ...)` rejects under-filled payloads with `Envelope.incomplete_input`
        # — the spec §5.2.1 interrogation pattern. Defense-in-depth: the
        # service-layer raise still catches non-gateway callers.
        completeness_env = self._delegate_completeness_check(
            inputs, parent, briefing, role_str
        )
        if completeness_env is not None:
            return await self._emit_rejection(
                completeness_env,
                agent_id=pm_agent_id,
                task_id=parent_task_id,
                verb="delegate",
            )
        # Write-then-gate: the delegated subtask's title + description (the
        # PM's own articulation of the work) is recorded as the
        # journal:decision the tracing guard below requires.
        pm_decision_outcome = await self._ensure_pm_decision(
            pm_agent_id,
            parent_task_id,
            f"Delegating subtask '{inputs.title}': {inputs.description}",
        )
        # Spec gate passed. Run delegate-specific guards the spec doesn't
        # model: tracing (journal:decision), chain validation, enum
        # coercion + assignee-vs-task_type, parent-ownership/subtask-cap, and
        # (last) decomposition-coverage.
        guard = await self._delegate_post_spec_guards(
            pm_agent_id,
            parent_task_id,
            parent,
            role_str,
            inputs,
            pm_decision_outcome=pm_decision_outcome,
        )
        if guard is not None:
            return await self._emit_rejection(
                guard.with_introspection(task=parent, role=role_str),
                agent_id=pm_agent_id,
                task_id=parent_task_id,
                verb="delegate",
            )
        return await self._create_subtask_and_envelope(
            pm_agent_id, parent, inputs, briefing, role_str
        )

    async def _delegate_post_spec_guards(
        self,
        pm_agent_id: UUID,
        parent_task_id: UUID,
        parent: Any,
        role_str: str,
        inputs: DelegateInputs,
        *,
        pm_decision_outcome: PmDecisionOutcome,
    ) -> Envelope | None:
        """``_delegate_extra_guards``, then the decomposition-coverage gate.

        Split out only to keep ``delegate()``'s own return count under the
        complexity budget. The coverage gate deliberately runs LAST, after
        every cap/chain/tracing/static rejection, so those guards keep their
        own specific message (existing tests assert on it) and the coverage
        gate only ever fires on an otherwise-valid delegate.
        """
        guard = await self._delegate_extra_guards(
            pm_agent_id,
            parent_task_id,
            parent,
            role_str,
            inputs,
            pm_decision_outcome=pm_decision_outcome,
        )
        if guard is not None:
            return guard
        return await self._delegate_ac_coverage_guard(parent, inputs)

    async def _delegate_ac_coverage_guard(
        self, parent: Any, inputs: DelegateInputs
    ) -> Envelope | None:
        """Reject a child that doesn't map to the parent's own criteria.

        A parent with real acceptance criteria (every task has some) requires
        every child to declare which ones it advances via
        ``covers_parent_criteria`` — a child mapped to nothing is either scope
        creep or a sign the parent's ACs are stale, and either way the PM must
        decide now, not discover the gap late at submit_up's roll-up gate
        (``_parent_acs_covered_envelope``) after the whole wave already ran.
        Inert when the parent has no acceptance criteria at all (nothing to
        map against) — mirrors the safe-by-construction posture of
        ``uncovered_parent_acceptance_criteria`` et al. Does NOT require full
        coverage in one call: a wave may deliberately leave criteria for a
        later delegate (see the success envelope's ``parent_ac_coverage``
        evidence for that signal).

        On reject, self-heals a legacy/drifted parent's
        ``acceptance_criteria_ids`` in place before rendering the hint (same
        touchpoint ``uncovered_parent_acceptance_criteria`` et al. reach via
        ``_parent_ac_ref_sets``) — otherwise a criteria-bearing parent with
        empty ids renders a ``'<id>'`` placeholder the PM can't act on.
        """
        ac_texts = parent.acceptance_criteria or []
        if not ac_texts:
            return None
        refs = inputs.covers_parent_criteria or []
        bad = self.task.unknown_ac_refs(parent, refs) if refs else []
        if refs and not bad:
            return None
        await self.task.self_heal_ac_ids(parent)
        if not refs:
            message = (
                f"'{inputs.title}' declares no covers_parent_criteria, but the "
                "parent has acceptance criteria to decompose."
            )
        else:
            message = (
                f"'{inputs.title}' covers_parent_criteria has unresolvable "
                f"ref(s): {'; '.join(bad)}"
            )
        return Envelope.invalid_state(
            message=message,
            remediate=hint_for_missing_ac_coverage(
                ids=parent.acceptance_criteria_ids or [],
                texts=ac_texts,
                title=inputs.title,
            ),
            context_briefing={},
        )

    # Gate Set B subtask cap (pre-gateway implicit, made explicit here).
    # Soft warn at 8, hard block at 13. Cap enforced by ``_subtask_cap_guard``.
    _SUBTASK_HARD_CAP: int = 12

    async def _delegate_extra_guards(
        self,
        pm_agent_id: UUID,
        parent_task_id: UUID,
        parent: Any,
        role_str: str,
        inputs: DelegateInputs,
        *,
        pm_decision_outcome: PmDecisionOutcome,
    ) -> Envelope | None:
        """Delegate-specific guards the spec doesn't model.

        Order: tracing (journal:decision per VERB_REQUIREMENTS) ->
        chain validation -> static (project_id, enum coercion,
        assignee-vs-task_type) -> lifecycle (parent ownership + subtask
        cap). Each returns an Envelope rejection or None to allow.

        The role-only check is no longer here — ``spec.can_invoke_intent``
        handles role+state in the verb body before this is called.
        """
        # Pre-gateway PM.md required journal:decision before each delegate.
        if env := await self._check_pm_decision_required(
            "delegate",
            pm_agent_id,
            parent_task_id,
            parent,
            pm_decision_outcome=pm_decision_outcome,
        ):
            return env
        chain_error = self._validate_delegation_chain(role_str, inputs.assigned_to)
        if chain_error is not None:
            return Envelope.not_authorized(
                message=chain_error,
                remediate=(
                    "Main PM delegates to be-pm/fe-pm/ux-pm. "
                    "Cell PM delegates to its own team's devs (e.g. backend "
                    "PM -> be-dev-1/be-dev-2)."
                ),
                context_briefing=await self._briefing_for(pm_agent_id, parent_task_id),
            )
        if guard := await self._delegate_static_guards(
            pm_agent_id, parent_task_id, parent, inputs
        ):
            return guard
        # Sibling dedup: catch the PM-decomposition bug where the same
        # parent gets two subtasks for the same role + task_type
        # (observed on smoke run 2026-05-11: Main PM created two planning
        # tasks for be-pm; Cell PM created two code tasks for be-dev-1).
        if guard := await self._delegate_sibling_dedup_guard(parent_task_id, inputs):
            return guard
        # Split-before-claim: reject an egregiously-bundled code leaf so the PM
        # splits it before any dev can claim it.
        if guard := self._delegate_sizing_guard(inputs):
            return guard
        # Gate Set B: PARENT_NOT_CLAIMED + SUBTASK_CAP
        return await self._delegate_lifecycle_guards(
            pm_agent_id, parent_task_id, parent
        )

    async def _delegate_static_guards(
        self,
        pm_agent_id: UUID,
        parent_task_id: UUID,
        parent: Any,
        inputs: DelegateInputs,
    ) -> Envelope | None:
        """project_id / enum guards. Pure data-shape checks.

        The slug-validity check used to live here, but
        `_validate_delegation_chain` runs first (in `_delegate_extra_guards`)
        and rejects any slug outside the allowed delegation targets —
        which is a strict subset of `AGENT_UUIDS` — so any AGENT_UUIDS
        check here was unreachable.
        """
        if (
            parent.project_id is None
            and getattr(parent, "product_id", None) is None
            and not getattr(parent, "cell_projects", None)
        ):
            return Envelope.invalid_state(
                message="parent task has neither a project_id nor a product_id",
                remediate=(
                    "the parent must have a project (single repo) or a product "
                    "(cell->project map) so subtasks can resolve a repo"
                ),
                context_briefing=await self._briefing_for(pm_agent_id, parent_task_id),
            )
        try:
            self._resolve_delegate_enums(inputs)
        except ValueError as exc:
            return Envelope.invalid_state(
                message=f"invalid enum value: {exc}",
                remediate="check team/task_type/estimated_complexity",
                context_briefing=await self._briefing_for(pm_agent_id, parent_task_id),
            )
        if type_error := self._validate_assignee_task_type(
            inputs.assigned_to, inputs.task_type
        ):
            return Envelope.invalid_state(
                message=type_error,
                remediate=self._assignee_task_type_remediate(inputs.assigned_to),
                context_briefing=await self._briefing_for(pm_agent_id, parent_task_id),
            )
        if str(inputs.task_type) == "documentation":
            # The lifecycle auto-handles documentation. After a
            # `code` subtask passes QA it transitions to
            # awaiting_documentation and a *documenter* is spawned
            # automatically. A PM-created `documentation` subtask assigned
            # to a developer can never be spawned (dev-dispatch rejects
            # role/task_type mismatch) — it becomes a permanent orphan
            # that deadlocks submit_up (all subtasks must be terminal).
            # The spine-cap is per-type so this slips past the
            # code-vs-code dedup; reject it explicitly here.
            return Envelope.invalid_state(
                message=(
                    "task_type='documentation' subtasks are not PM-"
                    "delegatable: the lifecycle creates the documentation "
                    "phase automatically after the code subtask passes QA."
                ),
                remediate=(
                    "Delegate ONLY the code subtask (task_type='code'). "
                    "Once it passes QA the gateway transitions it to "
                    "awaiting_documentation and spawns a documenter for "
                    "you — do NOT create a separate documentation subtask "
                    "or assign docs to a developer. Re-issue delegate(...) "
                    "with task_type='code' and i_am_idle when done."
                ),
                context_briefing=await self._briefing_for(pm_agent_id, parent_task_id),
            )
        return None

    @staticmethod
    def _validate_assignee_task_type(assigned_to: str, task_type: str) -> str | None:
        """Reject role-vs-type misclassifications.

        Rules:
        - delegating to a PM (Cell PM OR Main PM) requires
          ``task_type='planning'``. PMs decompose/coordinate; they don't
          execute. A freshly delegated subtask is never in ``needs_revision``,
          so the issue-resolution carve-out (a PM taking a code task to resolve
          review issues) does not apply here — that lives at the claim gate
          (``lifecycle._check_claim_rules_narrow`` via ``pm_cannot_own_code``).
          Main PM was previously omitted (only the cell-PM slug set was
          checked), leaving a delegate-to-main-pm-as-code hole.
        - (2026-05-11 smoke): delegating to a Developer requires
          ``task_type in {'code', 'documentation', 'research'}``. Devs
          implement. Planning/design/administrative belong to PMs/board.
          The 'research' allowance covers genuine spike work (try a
          library, prototype an approach) — NOT coordination/handoff,
          which is PM work.
        - (#7): the UX/UI cell's developers ARE its designers — for a
          DEVELOPER on ``Team.UX_UI`` ``task_type='design'`` is legitimate
          cell work (mockups, specs, design assets committed to the repo),
          so ux-dev-1/ux-dev-2 also accept ``design``. Backend/frontend
          devs still cannot be handed ``design`` — that routing belongs to
          the UX cell. The orchestrator already dispatches a developer for a
          ``design`` task (``_dev_dispatch_role_matches`` returns True), so
          this does not create the orphan that blocks ``documentation``.
        - Delegating to a QA requires ``task_type='code'`` (their work is
          to review PRs of code changes).
        - Delegating to a Documenter requires ``task_type='documentation'``.
        """
        pm_err = DelegateMixin._pm_task_type_error(assigned_to, task_type)
        if pm_err is not None:
            return pm_err
        from roboco.foundation.identity import AGENTS, Role, Team

        agent = AGENTS.get(assigned_to)
        if agent is None:
            return None
        if agent.role is Role.DEVELOPER:
            dev_err = DelegateMixin._developer_task_type_error(
                assigned_to, agent.team is Team.UX_UI, task_type
            )
            if dev_err is not None:
                return dev_err
        if agent.role is Role.QA and task_type != "code":
            return (
                f"task_type={task_type!r} is invalid for assignee {assigned_to!r}: "
                f"QA reviews code PRs — task_type must be 'code'."
            )
        if agent.role is Role.DOCUMENTER and task_type != "documentation":
            return (
                f"task_type={task_type!r} is invalid for assignee {assigned_to!r}: "
                f"Documenters write documentation — task_type must be "
                f"'documentation'."
            )
        return None

    @staticmethod
    def _pm_task_type_error(assigned_to: str, task_type: str) -> str | None:
        """Reject a code/non-planning task_type delegated to a PM (cell or main).

        Extracted from ``_validate_assignee_task_type`` so the compound PM guard
        doesn't inflate the dispatcher's complexity. A freshly delegated subtask
        is never in ``needs_revision``, so the issue-resolution carve-out does
        not apply here (it lives at the claim gate).
        """
        from roboco.foundation.identity import AGENTS, Role

        agent = AGENTS.get(assigned_to)
        if agent is None or agent.role not in (Role.CELL_PM, Role.MAIN_PM):
            return None
        if task_type == "planning":
            return None
        return (
            f"task_type={task_type!r} is invalid for assignee"
            f" {assigned_to!r}: PMs own planning tasks, not"
            f" code/documentation/etc."
        )

    @staticmethod
    def _developer_task_type_error(
        assigned_to: str, is_ux_dev: bool, task_type: str
    ) -> str | None:
        """Reject a developer's task_type. UX-cell developers additionally
        accept 'design' (mockups/specs/design assets are their cell work)."""
        allowed = {"code", "documentation", "research"}
        owned = "code/documentation/research"
        design_clause = "; design belongs to the UX cell."
        if is_ux_dev:
            allowed.add("design")
            owned = "code/documentation/research/design"
            design_clause = "."
        if task_type in allowed:
            return None
        return (
            f"task_type={task_type!r} is invalid for assignee "
            f"{assigned_to!r}: Developers own {owned}. Coordination, "
            f"planning, and administrative work belong to PMs{design_clause}"
        )

    @staticmethod
    def _assignee_task_type_remediate(assigned_to: str) -> str:
        """Per-assignee-class remediation for an assignee-vs-task_type reject.

        The reject `message` is already case-specific; this gives the PM the
        right *next call* for the kind of assignee it just mis-typed instead
        of a one-size-fits-all 'pass planning to a Cell PM' hint.
        """
        from roboco.foundation.identity import AGENTS, Role, Team

        agent = AGENTS.get(assigned_to)
        if agent is not None and agent.role is Role.MAIN_PM:
            return (
                "Main PMs own PLANNING tasks — they coordinate across cells and "
                "delegate execution. Pass task_type='planning' when delegating to "
                "the Main PM; route code work to a developer via the cell PM."
            )
        if agent is not None and agent.role is Role.CELL_PM:
            return (
                "Cell PMs (be-pm/fe-pm/ux-pm) own PLANNING tasks — they "
                "decompose the slice and delegate code work to devs. Pass "
                "task_type='planning' when delegating to a Cell PM."
            )
        if agent is not None and agent.role is Role.DEVELOPER:
            if agent.team is Team.UX_UI:
                return (
                    "UX developers take task_type in "
                    "{'code','documentation','research','design'}. Use "
                    "'design' for mockups/specs/design-asset work, 'code' "
                    "for implementation."
                )
            return (
                "Developers take task_type in "
                "{'code','documentation','research'}. Use 'code' for "
                "implementation; planning/administrative work stays with you "
                "(the PM), and design work is routed to the UX cell."
            )
        return (
            "Match task_type to the assignee's role: 'code' for devs/QA, "
            "'documentation' for documenters, 'planning' for Cell PMs."
        )

    async def _delegate_lifecycle_guards(
        self,
        pm_agent_id: UUID,
        parent_task_id: UUID,
        parent: Any,
    ) -> Envelope | None:
        """Gate Set B: PARENT_NOT_CLAIMED + SUBTASK_CAP.

        Pre-gateway, the orchestrator enforced both implicitly: a PM only
        ever called task_create after the orchestrator spawned them
        post-claim, and naturally never created more than a handful of
        subtasks in one spawn cycle.

        With the gateway exposing ``delegate`` as a first-class verb,
        these gates must be explicit.
        """
        if str(parent.status) != "in_progress":
            return Envelope.invalid_state(
                message=(
                    f"parent task {parent_task_id} is in {parent.status}; "
                    "must be in_progress to accept subtasks"
                ),
                remediate=(
                    f"call i_will_plan(parent_task_id='{parent_task_id}',"
                    " plan='...') before delegating subtasks"
                ),
                context_briefing=await self._briefing_for(pm_agent_id, parent_task_id),
            )
        if parent.assigned_to != pm_agent_id:
            return Envelope.not_authorized(
                message=(
                    f"parent task {parent_task_id} is assigned to "
                    f"{parent.assigned_to}, not you"
                ),
                remediate=(
                    f"call i_will_plan(parent_task_id='{parent_task_id}',"
                    " plan='...') to claim before delegating subtasks"
                ),
                context_briefing=await self._briefing_for(pm_agent_id, parent_task_id),
            )
        existing = await self.task.get_subtasks(parent_task_id)
        if len(existing) >= self._SUBTASK_HARD_CAP:
            return Envelope.invalid_state(
                message=(
                    f"parent already has {len(existing)} subtasks; "
                    f"cap is {self._SUBTASK_HARD_CAP}"
                ),
                remediate=(
                    "consolidate or split into a separate parent task before"
                    " adding more subtasks"
                ),
                context_briefing=await self._briefing_for(pm_agent_id, parent_task_id),
            )
        return None

    @staticmethod
    def _resolve_delegate_enums(inputs: DelegateInputs) -> tuple[Any, Any, Any]:
        """Convert string inputs to Team/TaskType/Complexity enums.

        Raises ValueError if any string is not a member of its enum.
        """
        from roboco.models.base import Complexity, TaskType, Team

        return (
            Team(inputs.team),
            TaskType(inputs.task_type),
            Complexity(inputs.estimated_complexity),
        )

    def _delegate_completeness_check(
        self,
        inputs: DelegateInputs,
        parent: Any,
        briefing: dict[str, Any],
        role_str: str,
    ) -> Envelope | None:
        """The foundation/policy/task_completeness gate for delegate.

        Auto-fills unambiguous fields (team-from-slug, priority-from-parent)
        without overwriting explicit values, then runs `check(TASK_AT_CREATE,
        ...)` against the payload. Returns:

        - ``None`` when every TASK_AT_CREATE requirement is satisfied (the
          verb continues into the static/lifecycle guards).
        - ``Envelope.incomplete_input`` (with `with_introspection` applied)
          when any field is missing — the agent gets a structured
          field-by-field guide (spec §5.2.1 interrogation pattern).

        The `acceptance_criteria=inputs.acceptance_criteria or []` collapse
        at `_create_subtask_from_inputs` was removed alongside this gate,
        so under-filled payloads now hit the service-layer raise
        instead of being silently substituted. This method is the
        gateway-side defense; the service raise is defense-in-depth for
        non-gateway callers.
        """
        from types import SimpleNamespace

        from roboco.foundation.policy import task_completeness as tc

        payload: dict[str, Any] = {
            "title": inputs.title,
            "description": inputs.description,
            "assigned_to": inputs.assigned_to,
            "team": inputs.team,
            "task_type": inputs.task_type,
            "nature": inputs.nature,
            "estimated_complexity": inputs.estimated_complexity,
            "acceptance_criteria": inputs.acceptance_criteria,
            # Collision surface — required for code subtasks (TASK_AT_DELEGATE):
            # the sibling collision DAG can only order what is declared, and a
            # no-surface code sibling silently runs parallel to everything.
            "intends_to_touch": inputs.intends_to_touch,
        }
        # Auto-fill (spec §5.2.1 (a)) — never overwrites explicit values.
        # team-from-slug is harmless when the caller already supplied team;
        # priority-from-parent records `__priority_inherited=True` for
        # post-create journal:note (best-effort observability).
        payload = tc.fill_team_from_assignee(payload)
        payload = tc.fill_priority_from_parent(payload, parent)
        completeness_input = SimpleNamespace(
            **{k: v for k, v in payload.items() if not k.startswith("__")}
        )
        result = tc.check(tc.TASK_AT_DELEGATE, completeness_input)
        if result.passed:
            return None
        return Envelope.incomplete_input(
            missing=result.missing,
            field_hints=result.field_hints,
            remediate=(
                "re-issue delegate(...) with these fields filled: "
                f"{', '.join(result.missing)}. Each field's required shape "
                "is in `field_hints`."
            ),
            context_briefing=briefing,
        ).with_introspection(task=parent, role=role_str)

    async def _create_subtask_and_envelope(
        self,
        pm_agent_id: UUID,
        parent: Any,
        inputs: DelegateInputs,
        briefing: dict[str, Any],
        role_str: str,
    ) -> Envelope:
        """Run subtask creation and translate completeness raises into envelopes.

        The defensive raises inside `_create_subtask_from_inputs`
        catch under-filled payloads that slipped past the gateway gate. Without
        this translator they surface as Starlette 500s — which means the agent
        never sees `field_hints`, retries indefinitely, and looks like a
        runaway. Converting to `Envelope.incomplete_input` here closes that
        loop so the agent gets the same interrogation-pattern reply it would
        have gotten from the upfront completeness check.
        """
        from roboco.foundation.policy.task_completeness import TaskCompletenessError
        from roboco.services.base import ValidationError

        parent_task_id = parent.id
        try:
            new_task = await self._create_subtask_from_inputs(
                pm_agent_id, parent_task_id, parent, inputs
            )
        except TaskCompletenessError as exc:
            return await self._emit_rejection(
                Envelope.incomplete_input(
                    missing=exc.missing,
                    field_hints=exc.field_hints,
                    remediate=(
                        "re-issue delegate(...) with corrected fields: "
                        f"{', '.join(exc.missing)}. Each field's required "
                        "shape is in `field_hints`."
                    ),
                    context_briefing=briefing,
                ).with_introspection(task=parent, role=role_str),
                agent_id=pm_agent_id,
                task_id=parent_task_id,
                verb="delegate",
            )
        except ValidationError as exc:
            # A user-input error from task creation (e.g. delegating past
            # MAX_TASK_DEPTH — ``_validate_parent_depth`` raises this with a
            # remediation message telling the PM to create a sibling instead of
            # a further nested subtask). Without this translator it escaped
            # uncaught as a 500 ExceptionGroup; the agent never saw the fix.
            # ``invalid_state`` carries the message as the remediation so the
            # PM gets a clean, actionable rejection it can act on.
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message=exc.message,
                    remediate=exc.message,
                    context_briefing=briefing,
                ).with_introspection(task=parent, role=role_str),
                agent_id=pm_agent_id,
                task_id=parent_task_id,
                verb="delegate",
            )
        await self._wire_ux_frontend_dependency(new_task, parent)
        hint = self._sizing_hint(inputs)
        if hint is not None:
            briefing = {**briefing, "sizing_hint": hint}
        # Recompute coverage AFTER creation so the PM sees this child counted and
        # exactly which parent criteria are still unclaimed before the next
        # delegate / idle. The pre-create briefing built in delegate() is stale.
        coverage = await self.task.parent_ac_coverage(parent_task_id)
        if coverage:
            briefing = {
                **briefing,
                "parent_ac_coverage": coverage,
                "unclaimed_parent_acs": [c["id"] for c in coverage if not c["claimed"]],
                "delegate_hint": self._COVERS_PARENT_CRITERIA_HINT,
            }
        return Envelope.ok(
            status="created",
            task_id=str(new_task.id),
            next=spec_module._INTENT_VERBS["delegate"].next_hint(new_task),
            evidence=await self._delegate_ac_coverage_evidence(parent, parent_task_id),
            context_briefing=briefing,
        ).with_introspection(task=new_task, role=role_str)

    async def _delegate_ac_coverage_evidence(
        self, parent: Any, parent_task_id: UUID
    ) -> dict[str, Any]:
        """``evidence.parent_ac_coverage`` for a successful delegate.

        Reuses ``uncovered_parent_acceptance_criteria`` — the exact primitive
        submit_up's roll-up gate checks — so the PM sees, in the same turn,
        which parent criteria the wave-so-far still leaves uncovered instead
        of discovering it later at assembly. Wave-based planning stays legal:
        this never blocks, it only reports. Empty (``{}``) when the parent
        carries no acceptance criteria at all.
        """
        ac_texts = parent.acceptance_criteria or []
        if not ac_texts:
            return {}
        uncovered = await self.task.uncovered_parent_acceptance_criteria(parent_task_id)
        covered = [t for t in ac_texts if t not in uncovered]
        return {"parent_ac_coverage": {"covered": covered, "uncovered": uncovered}}

    @staticmethod
    def _validate_delegation_chain(pm_role: str, target_slug: str) -> str | None:
        """Return error string if delegation chain is invalid; else None."""
        cell_pm_targets = {
            "cell_pm": {
                "backend": {"be-dev-1", "be-dev-2"},
                "frontend": {"fe-dev-1", "fe-dev-2"},
                "ux_ui": {"ux-dev-1", "ux-dev-2"},
            },
        }
        main_pm_targets = {"be-pm", "fe-pm", "ux-pm"}

        if pm_role == "main_pm":
            if target_slug not in main_pm_targets:
                return (
                    f"main_pm cannot delegate to {target_slug!r}; allowed: "
                    f"{sorted(main_pm_targets)}"
                )
            return None
        if pm_role == "cell_pm":
            allowed_for_any_team: set[str] = set()
            for team_targets in cell_pm_targets["cell_pm"].values():
                allowed_for_any_team |= team_targets
            if target_slug not in allowed_for_any_team:
                return (
                    f"cell_pm cannot delegate to {target_slug!r}; allowed: "
                    f"{sorted(allowed_for_any_team)}"
                )
            return None
        return f"role {pm_role!r} cannot delegate"


class SiblingDedupMixin(_Base):
    """``delegate``'s sizing and duplicate checks: a code leaf with too many
    acceptance criteria, a near-duplicate title in the same assignee's
    queue, and the per-parent spine caps."""

    # Per-parent concurrency cap by spine task_type. `planning` and
    # `documentation` stay sequential (one non-terminal at a time). `code` has
    # NO per-parent cap: a PM delegates a full per-dev queue up front (Spec 3
    # per-dev sequenced queues) — both cell devs build in parallel, each works
    # its own queue in sequence order, and the orchestrator's per-lane dispatch
    # barrier (`_blocked_by_earlier_lane_sibling`) keeps a dev to one live task
    # at a time. Total fan-out is still bounded by `_SUBTASK_HARD_CAP`.
    _SPINE_TYPE_CAPS: ClassVar[dict[str, int]] = {
        "planning": 1,
        "documentation": 1,
    }

    # Split-before-claim sizing thresholds for a `code` leaf (by acceptance-
    # criteria count). Above the nudge count we flag a possible split in the
    # success envelope; above the hard count we reject so the PM splits the
    # bundle before any dev can claim it.
    _SIZING_NUDGE_AC_COUNT: ClassVar[int] = 5

    _SIZING_HARD_AC_COUNT: ClassVar[int] = 8

    @classmethod
    def _code_leaf_ac_count(cls, inputs: DelegateInputs) -> int | None:
        """Acceptance-criteria count for a ``code`` subtask, else None.

        Only ``code`` leaves are sized this way — ``planning`` briefs (main_pm
        → cell_pm) legitimately carry many criteria and are exempt.
        """
        if str(inputs.task_type or "") != "code":
            return None
        return len(inputs.acceptance_criteria or [])

    @classmethod
    def _delegate_sizing_guard(cls, inputs: DelegateInputs) -> Envelope | None:
        """Hard-block an egregiously-bundled code leaf (split-before-claim).

        A ``code`` subtask carrying more than ``_SIZING_HARD_AC_COUNT``
        acceptance criteria bundles too many independent concerns into one leaf:
        QA can't pass a partial, the dev re-touches unrelated parts, and criteria
        get dropped. Reject it so the PM splits the bundle before a dev ever
        claims it. Moderate bundling is allowed but flagged (see
        :meth:`_sizing_hint`).
        """
        ac_count = cls._code_leaf_ac_count(inputs)
        if ac_count is None or ac_count <= cls._SIZING_HARD_AC_COUNT:
            return None
        return Envelope.invalid_state(
            message=(
                f"code subtask bundles {ac_count} acceptance criteria — too many "
                f"independent concerns for one leaf "
                f"(cap {cls._SIZING_HARD_AC_COUNT})."
            ),
            remediate=(
                "Split this into smaller code subtasks before delegating — one "
                "concern each, 2-4 acceptance criteria per subtask. Hand "
                "independent concerns to both cell devs in parallel; sequence "
                "dependent ones. A bundled leaf drives multi-round QA failures "
                "and drops criteria."
            ),
            context_briefing={},
        )

    @classmethod
    def _sizing_hint(cls, inputs: DelegateInputs) -> str | None:
        """Soft nudge for a code leaf in the moderate sizing band.

        Fires above the nudge count and below the hard cap (the hard cap rejects
        outright). Surfaced in the delegate success envelope, never blocking.
        """
        ac_count = cls._code_leaf_ac_count(inputs)
        if ac_count is None or ac_count <= cls._SIZING_NUDGE_AC_COUNT:
            return None
        return (
            f"This code subtask carries {ac_count} acceptance criteria. If they "
            "span more than one independent concern, consider splitting it so "
            "each concern is its own subtask — and hand independents to both "
            "devs in parallel. (Allowed, just flagged.)"
        )

    @staticmethod
    def _spine_type_dup_envelope(
        new_type: str, sibling: Any, sib_assignee: str, cap: int = 1
    ) -> Envelope:
        """Rule-1 rejection: a sequential spine (planning/documentation) is full.

        Only ``planning`` and ``documentation`` are cap-limited now (1 at a
        time); ``code`` has no per-parent cap (Spec 3 per-dev queues), so this
        only ever fires for the sequential spines. ``cap`` is kept for the
        message but is 1 in every live call.
        """
        return Envelope.invalid_state(
            message=(
                f"parent already has {cap} non-terminal "
                f"task_type={new_type!r} subtask(s) "
                f"(e.g. {sibling.id}, assigned_to={sib_assignee!r}, "
                f"status={sibling.status}). The {new_type!r} spine is "
                "sequential — only one non-terminal at a time."
            ),
            remediate=(
                "Drive an existing sibling to completion / cancel it before "
                "delegating another of the same type. If the work is genuinely "
                "parallel, split this parent into two sibling parents instead.\n\n"
                "**DO NOT work around this by delegating again with a "
                "different task_type** (e.g. 'documentation' or "
                "'research' as a 'verification' subtask). The lifecycle "
                "handles QA, documentation, and PM-review automatically "
                "after the code subtask finishes — you do not create "
                "auxiliary subtasks for those roles. Call i_am_idle() "
                "now and wait for an existing child to come back."
            ),
            context_briefing={},
        )

    @staticmethod
    def _same_assignee_dup_envelope(
        new_type: str, new_assignee: str, sibling: Any
    ) -> Envelope:
        """Rule-2 rejection: same assignee already owns same task_type."""
        return Envelope.invalid_state(
            message=(
                f"sibling subtask already assigned to "
                f"{new_assignee!r} with task_type={new_type!r}: "
                f"id={sibling.id} status={sibling.status}"
            ),
            remediate=(
                "Either drive the existing sibling to completion / "
                "cancel it, or split this work into a subtask of "
                "the existing sibling rather than a new sibling."
            ),
            context_briefing={},
        )

    @staticmethod
    def _is_cross_team_planning(new_type: str, new_team: str, sib_team: str) -> bool:
        """Planning subtasks on different teams are NOT
        over-decomposition — main_pm fans planning out to per-cell PMs.
        Both teams must be non-empty so an empty-team escape hatch can't
        bypass the cap defensively.
        """
        return (
            new_type == "planning"
            and bool(new_team)
            and bool(sib_team)
            and new_team != sib_team
        )

    @classmethod
    def _sibling_cap_envelope(
        cls,
        siblings: list[Any],
        new_type: str,
        new_team: str,
        new_assignee: str,
        new_title: str = "",
    ) -> Envelope | None:
        """Apply Rule-2 (same-assignee) then Rule-1 (spine concurrency cap).

        Rule 2: prevent accidental duplicate delegation to one agent — for
        ``planning`` / ``documentation`` no two same-type subtasks per parent;
        for ``code`` only an exact same-title repeat (a dev may own a sequenced
        queue otherwise).

        Rule 1: a parent may hold at most ``_SPINE_TYPE_CAPS[type]`` non-terminal
        subtasks of a spine type. ``code`` has no per-parent cap (a full per-dev
        queue is delegated up front; the per-lane dispatch barrier and
        ``_SUBTASK_HARD_CAP`` bound it); ``planning`` and ``documentation`` stay
        at 1. ``planning`` on a different team does not count toward the cap —
        that's main_pm's legitimate cross-cell fanout (see
        :meth:`_is_cross_team_planning`).
        """
        live = [
            s
            for s in siblings
            if str(getattr(s, "status", "")) not in cls._TERMINAL_STATUSES
        ]
        return cls._same_assignee_rejection(
            live, new_type, new_assignee, new_title
        ) or cls._spine_cap_rejection(live, new_type, new_team)

    @staticmethod
    def _norm_title(title: str) -> str:
        """Lower-case, whitespace-collapsed title for duplicate comparison."""
        return " ".join((title or "").lower().split())

    # Near-duplicate title detection for code queues. Containment coefficient
    # over stopword-stripped tokens: how much of the SHORTER title appears in
    # the longer. 0.85 catches a rephrasing with a suffix ("... queue" vs
    # "... queue (cap growth; optional prune-on-ack)") and stopword drift
    # ("rewrite the stale X" vs "rewrite stale X to assert Y"), while leaving
    # genuinely distinct queue items alone.
    _DUP_TITLE_CONTAINMENT: float = 0.85

    _TITLE_STOPWORDS: frozenset[str] = frozenset(
        {"the", "a", "an", "and", "for", "to", "of", "in", "on", "with"}
    )

    @classmethod
    def _titles_are_duplicates(cls, norm_a: str, norm_b: str) -> bool:
        """True when one open sibling's title is a near-duplicate of the new
        one. Live failure (be-pm, 2026-09-27): re-planning a paused parent
        re-delegated work that already existed as awaiting_qa / in_progress
        siblings under near-identical titles, and the exact-match rule let
        all of it through — three copies of one task, two of another."""
        if norm_a == norm_b:
            return True
        strip = cls._TITLE_STOPWORDS
        a_tokens = set(norm_a.split()) - strip
        b_tokens = set(norm_b.split()) - strip
        if not a_tokens or not b_tokens:
            return False
        smaller, larger = sorted((a_tokens, b_tokens), key=len)
        containment = len(smaller & larger) / len(smaller)
        return containment >= cls._DUP_TITLE_CONTAINMENT

    @classmethod
    def _same_assignee_rejection(
        cls, live: list[Any], new_type: str, new_assignee: str, new_title: str = ""
    ) -> Envelope | None:
        """Rule 2: prevent accidental duplicate delegation to one agent.

        For ``planning`` / ``documentation`` (sequential spines) a PM never
        gives one agent two non-terminal subtasks of the same type under one
        parent. For ``code`` a dev legitimately owns a *queue* of sequenced
        subtasks (Spec 3 per-dev parallelism), so same-type alone is allowed —
        only an exact same-title duplicate (the accidental re-delegation bug,
        e.g. "Cell PM created two code tasks for be-dev-1") is rejected.
        """
        if not new_assignee:
            return None
        norm_new = cls._norm_title(new_title)
        for sibling in live:
            if str(getattr(sibling, "assigned_to", "") or "") != new_assignee:
                continue
            if str(getattr(sibling, "task_type", "")) != new_type:
                continue
            if new_type == "code":
                # A distinct queue item is fine; a rephrased repeat of an open
                # sibling is not.
                rejection = cls._code_queue_dup_rejection(
                    norm_new, new_type, new_assignee, sibling
                )
                if rejection is not None:
                    return rejection
                continue
            return cls._same_assignee_dup_envelope(new_type, new_assignee, sibling)
        return None

    @classmethod
    def _code_queue_dup_rejection(
        cls, norm_new: str, new_type: str, new_assignee: str, sibling: Any
    ) -> Envelope | None:
        """The duplicate envelope when an open code sibling rephrases
        ``norm_new``; ``None`` for a distinct queue item (Spec 3 lets a dev
        own a queue, so same-type alone must not reject)."""
        sib_title = cls._norm_title(str(getattr(sibling, "title", "") or ""))
        if norm_new and sib_title and cls._titles_are_duplicates(norm_new, sib_title):
            return cls._same_assignee_dup_envelope(new_type, new_assignee, sibling)
        return None

    @classmethod
    def _spine_cap_rejection(
        cls, live: list[Any], new_type: str, new_team: str
    ) -> Envelope | None:
        """Rule 1: at most ``_SPINE_TYPE_CAPS[type]`` non-terminal same-type."""
        cap = cls._SPINE_TYPE_CAPS.get(new_type)
        if cap is None:
            return None
        same_spine = [
            s
            for s in live
            if str(getattr(s, "task_type", "")) == new_type
            and not cls._is_cross_team_planning(
                new_type, new_team, str(getattr(s, "team", "") or "")
            )
        ]
        if len(same_spine) < cap:
            return None
        blocker = same_spine[0]
        return cls._spine_type_dup_envelope(
            new_type, blocker, str(getattr(blocker, "assigned_to", "") or ""), cap=cap
        )

    async def _delegate_sibling_dedup_guard(
        self,
        parent_task_id: UUID,
        inputs: DelegateInputs,
    ) -> Envelope | None:
        """Block PM-decomposition over-spread (the smoke-run runaway pattern).

        Two rules:

        1. **Same-type concurrency cap**: a parent may hold at most
           ``_SPINE_TYPE_CAPS[type]`` non-terminal subtasks of a spine type —
           ``planning`` and ``documentation`` stay at 1. ``code`` has no
           per-parent cap (a full per-dev sequenced queue is delegated up front;
           concurrency is bounded by the per-lane dispatch barrier and
           ``_SUBTASK_HARD_CAP``). Exception: ``planning`` on a different team
           does not count — that's main_pm's legitimate cross-cell fanout.
        2. **Same-assignee duplicate** (fallback): a PM never delegates two
           ``planning``/``documentation`` subtasks of the same type to one
           agent under one parent; for ``code`` a dev may own a queue, so only
           an exact same-title repeat is blocked (accidental re-delegation).

        Both rules surface an existing sibling id so the PM can finish
        or cancel it instead of guessing.
        """
        siblings = await self.task.get_subtasks(parent_task_id)
        return self._sibling_cap_envelope(
            list(siblings),
            str(inputs.task_type or ""),
            str(inputs.team or ""),
            str(inputs.assigned_to or ""),
            str(inputs.title or ""),
        )


class SubtaskCreateMixin(_Base):
    """Creating a delegated sub-task: its project, the completeness it needs,
    and the UX-before-frontend/backend dependencies wired across cells."""

    @staticmethod
    def _team_value(value: Any) -> str:
        return value.value if hasattr(value, "value") else str(value)

    async def _wire_ux_frontend_dependency(self, new_task: Any, parent: Any) -> None:
        """Cross-cell sequencing: in a multi-cell fan-out the implementation cells
        (FRONTEND and BACKEND) depend on the UX/UI cell task — UX design defines
        the screens and API contracts both cells build against, so it is upstream
        of implementation. Wires the dependency in either delegation order. A
        dev/code subtask delegated under a cell task that is itself still waiting
        on that dependency inherits it, so the developer is held until UX is done
        instead of coding ahead of the design. Best-effort: never breaks delegate.

        Fires for a product fan-out (``product_id`` set) AND a MegaTask
        root-subtask (a batch item that fans out to its own cells via
        ``cell_projects`` and carries no ``product_id``) — without the latter the
        cross-cell edges were never wired for MegaTask cells, so they ran fully
        in parallel and the sequence was ignored (divergent branches).
        """
        if parent is None:
            return
        from roboco.foundation.identity import Team
        from roboco.foundation.policy.batch import is_batch_root_subtask

        is_fanout = getattr(
            parent, "product_id", None
        ) is not None or is_batch_root_subtask(
            batch_id=getattr(parent, "batch_id", None),
            parent_task_id=getattr(parent, "parent_task_id", None),
        )
        if not is_fanout:
            return

        nt_team = self._team_value(new_task.team)
        try:
            # A subtask of a cell task that is waiting on another cell must wait
            # too — propagate the parent's unmet cross-cell dependencies down.
            await self.task.inherit_unmet_dependencies(new_task.id, parent.id)
            if nt_team == Team.FRONTEND.value:
                await self._depend_frontend_on_ux(new_task, parent.id)
            elif nt_team == Team.BACKEND.value:
                await self._depend_backend_on_ux(new_task, parent.id)
            elif nt_team == Team.UX_UI.value:
                await self._depend_pending_frontends_on_ux(new_task, parent.id)
                await self._depend_pending_backends_on_ux(new_task, parent.id)
        except Exception as exc:
            logger.warning(
                "cross-cell UX->implementation sequencing wiring failed",
                error=str(exc),
                parent_task_id=str(getattr(parent, "id", None)),
            )

    async def _depend_frontend_on_ux(self, fe_task: Any, parent_id: Any) -> None:
        """Make a new FRONTEND cell task wait on its non-terminal UX/UI sibling.

        The wire is followed by a wave restamp (not a hand-rolled ``ux+1``
        write) so the sequence reflects EVERY same-parent dependency the task
        carries — a relative write could undercut a collision-derived stamp
        and invert the claim gate's order against a wired edge.
        """
        from roboco.foundation.identity import Team
        from roboco.models.base import TaskStatus

        terminal = {TaskStatus.COMPLETED, TaskStatus.CANCELLED}
        siblings = await self.task.get_subtasks(parent_id)
        ux = next(
            (
                s
                for s in siblings
                if self._team_value(s.team) == Team.UX_UI.value
                and s.id != fe_task.id
                and s.status not in terminal
            ),
            None,
        )
        if ux is not None:
            await self.task.add_dependency(fe_task.id, ux.id)
            await self.task.stamp_wave_sequence(fe_task.id)

    async def _depend_backend_on_ux(self, be_task: Any, parent_id: Any) -> None:
        """Make a new BACKEND cell task wait on its non-terminal UX/UI sibling.

        Wire + wave restamp, mirroring :meth:`_depend_frontend_on_ux`.
        """
        from roboco.foundation.identity import Team
        from roboco.models.base import TaskStatus

        terminal = {TaskStatus.COMPLETED, TaskStatus.CANCELLED}
        siblings = await self.task.get_subtasks(parent_id)
        ux = next(
            (
                s
                for s in siblings
                if self._team_value(s.team) == Team.UX_UI.value
                and s.id != be_task.id
                and s.status not in terminal
            ),
            None,
        )
        if ux is not None:
            await self.task.add_dependency(be_task.id, ux.id)
            await self.task.stamp_wave_sequence(be_task.id)

    async def _depend_pending_frontends_on_ux(
        self, ux_task: Any, parent_id: Any
    ) -> None:
        """Retro-wire not-yet-started FRONTEND siblings onto a new UX/UI task.

        Each retro-wired sibling is wave-restamped so it lands above the UX
        task's OWN stamped wave (the new task is stamped before this runs) —
        a relative ``ux+1`` write here read the pre-stamp sequence and could
        invert the order.
        """
        from roboco.foundation.identity import Team
        from roboco.models.base import TaskStatus

        not_started = {TaskStatus.BACKLOG, TaskStatus.PENDING}
        siblings = await self.task.get_subtasks(parent_id)
        for fe in siblings:
            if (
                self._team_value(fe.team) == Team.FRONTEND.value
                and fe.id != ux_task.id
                and fe.status in not_started
            ):
                await self.task.add_dependency(fe.id, ux_task.id)
                await self.task.stamp_wave_sequence(fe.id)

    async def _depend_pending_backends_on_ux(
        self, ux_task: Any, parent_id: Any
    ) -> None:
        """Retro-wire not-yet-started BACKEND siblings onto a new UX/UI task.

        Wire + wave restamp per sibling, mirroring
        :meth:`_depend_pending_frontends_on_ux`.
        """
        from roboco.foundation.identity import Team
        from roboco.models.base import TaskStatus

        not_started = {TaskStatus.BACKLOG, TaskStatus.PENDING}
        siblings = await self.task.get_subtasks(parent_id)
        for be in siblings:
            if (
                self._team_value(be.team) == Team.BACKEND.value
                and be.id != ux_task.id
                and be.status in not_started
            ):
                await self.task.add_dependency(be.id, ux_task.id)
                await self.task.stamp_wave_sequence(be.id)

    async def _resolve_subtask_project(
        self, parent: Any, inputs: DelegateInputs
    ) -> UUID:
        """Resolve the project a delegated subtask lands in.

        Priority: explicit inputs.project_id -> the parent's ad-hoc cell_projects
        map for this cell -> the parent's Product map for this cell -> the
        parent's own project. Raises TaskCompletenessError only for a fan-out
        parent (product or cell map, no own project) whose map has no mapping for
        this cell — i.e. the subtask would have no repo to land in.
        """
        if inputs.project_id is not None:
            return inputs.project_id
        # Ad-hoc per-cell map (a multi-cell MegaTask root-subtask): mirror the
        # product.project_for lookup but read the map off the parent task itself.
        parent_cell_map = getattr(parent, "cell_projects", None)
        if parent_cell_map:
            for mapping in parent_cell_map:
                if mapping.team == inputs.team:
                    return UUID(str(mapping.project_id))
        parent_product_id = getattr(parent, "product_id", None)
        if self.product is not None and parent_product_id is not None:
            mapped = await self.product.project_for(parent_product_id, inputs.team)
            if mapped is not None:
                return UUID(str(mapped))
        if parent.project_id is not None:
            return UUID(str(parent.project_id))
        from roboco.foundation.policy.task_completeness import TaskCompletenessError

        raise TaskCompletenessError(
            missing=["project_id"],
            field_hints={
                "project_id": (
                    f"no project for team {inputs.team!r}: add a "
                    f"{inputs.team}->project mapping to the parent's cell map or "
                    "product, or pass an explicit project_id on delegate"
                )
            },
            message=f"cannot resolve a project for the {inputs.team} subtask",
        )

    @staticmethod
    def _require_subtask_completeness(
        inputs: DelegateInputs,
    ) -> tuple[TaskNature, list[str]]:
        """Defensive completeness check (callers run ``_delegate_completeness_check``
        first); returns the validated ``TaskNature`` and the non-empty
        ``acceptance_criteria``. Raises ``TaskCompletenessError`` with field hints
        if a non-gateway caller bypassed the gateway check — never silently
        substitutes an empty acceptance list.
        """
        from roboco.foundation.policy.task_completeness import TaskCompletenessError
        from roboco.models.base import TaskNature

        if not inputs.acceptance_criteria:
            raise TaskCompletenessError(
                missing=["acceptance_criteria"],
                field_hints={
                    "acceptance_criteria": (
                        "non-empty list[str]; each item describes a verifiable outcome"
                    )
                },
                message=(
                    "_create_subtask_from_inputs called with empty "
                    "acceptance_criteria — completeness check must run first"
                ),
            )
        if inputs.nature is None:
            raise TaskCompletenessError(
                missing=["nature"],
                field_hints={"nature": "one of: technical | non_technical"},
                message=(
                    "_create_subtask_from_inputs called with no nature — "
                    "completeness check must run first"
                ),
            )
        try:
            nature_enum = TaskNature(inputs.nature)
        except ValueError as exc:
            raise TaskCompletenessError(
                missing=["nature"],
                field_hints={"nature": "one of: technical | non_technical"},
                message=f"invalid nature {inputs.nature!r}: {exc}",
            ) from exc
        return nature_enum, inputs.acceptance_criteria

    async def _create_subtask_from_inputs(
        self,
        pm_agent_id: UUID,
        parent_task_id: UUID,
        parent: Any,
        inputs: DelegateInputs,
    ) -> Any:
        """Resolve enums + AGENT_UUIDS slug and call TaskService.create_subtask.

        By contract, callers (the `delegate` verb body) MUST run
        `_delegate_completeness_check` first, so `inputs.acceptance_criteria`
        and `inputs.nature` are guaranteed non-None / non-empty here. The
        defensive `TaskCompletenessError` raises preserve correctness if
        a future caller bypasses the gateway path — defense-in-depth in
        line with the service-layer raise.
        """
        from roboco.models.task import TaskCreateRequest
        from roboco.seeds.initial_data import AGENT_UUIDS

        team_enum, type_enum, complexity_enum = self._resolve_delegate_enums(inputs)
        assignee_id = UUID(AGENT_UUIDS[inputs.assigned_to])
        # The gateway runs `_delegate_completeness_check` BEFORE this helper, so
        # empty/None acceptance_criteria or nature here means a non-gateway caller
        # bypassed the check — _require_subtask_completeness raises with field
        # hints rather than silently substituting.
        nature_enum, acceptance_criteria = self._require_subtask_completeness(inputs)
        resolved_project_id = await self._resolve_subtask_project(parent, inputs)
        req = TaskCreateRequest(
            title=inputs.title,
            description=inputs.description,
            acceptance_criteria=acceptance_criteria,
            parent_ac_refs=inputs.covers_parent_criteria or [],
            team=team_enum,
            created_by=pm_agent_id,
            project_id=resolved_project_id,
            product_id=getattr(parent, "product_id", None),
            parent_task_id=parent_task_id,
            assigned_to=assignee_id,
            task_type=type_enum,
            nature=nature_enum,
            estimated_complexity=complexity_enum,
            # Dev-task collision surface (multi-level sequencing — edge kind 3)
            # + explicit dependency override. Forwarded so create_subtask can
            # persist them (Phase S2 runs SequencingService over the surfaced
            # siblings and wires the collision DAG via add_dependency).
            intends_to_touch=inputs.intends_to_touch,
            adds_migration=inputs.adds_migration,
            touches_shared=inputs.touches_shared,
            dependency_ids=list(inputs.depends_on) if inputs.depends_on else [],
        )
        new_task = await self.task.create_subtask(req)
        # Wire the dev-task collision DAG (multi-level sequencing edge kind 3):
        # run the deterministic analyzer over the parent's surfaced siblings
        # and add_dependency each collision edge so a later dev task stays
        # PENDING until the sibling it collides with completes (PR merged) —
        # the cross-dev ordering the assignee-keyed spawn barrier could not
        # guarantee (live 2026-06-27 out-of-order break). Incremental +
        # idempotent: re-run after every delegate; add_dependency dedupes, and
        # dev_task_collision_edges orders by (priority, sequence) so re-runs
        # only add edges (never flip an existing pair's order into a cycle).
        await self.task.wire_sibling_collision_dag(parent_task_id)
        # Multi-level sequencing — edge kinds 2 + 4: the cell-task wave chain
        # and the by-osmosis dev-task edge. Dispatch on the parent's team: a
        # MAIN_PM parent is a root-subtask, so the new task is a CELL-TASK →
        # wire kind 2 (it depends on the previous wave's cell-tasks); a cell
        # team parent is a cell-task, so the new task is a DEV TASK → wire kind
        # 4 (its first dev task carries the previous wave's merged tail). kind 3
        # (wire_sibling_collision_dag, above) runs for both. Idempotent +
        # best-effort: missing predecessors / cell-tasks contribute no edge.
        from roboco.foundation.identity import Team

        parent_team = getattr(parent, "team", None)
        if parent_team == Team.MAIN_PM.value:
            await self.task.wire_cell_task_wave_chain(new_task.id)
        elif parent_team in (
            Team.BACKEND.value,
            Team.FRONTEND.value,
            Team.UX_UI.value,
        ):
            await self.task.wire_by_osmosis_edge(new_task.id)
        # Post-wiring wave stamp: sequence = 1 + max(same-parent dependency
        # sequences), 0 when independent — so independent siblings tie (and
        # run in parallel under the sequence claim gate) while colliding /
        # ordered ones ascend. Replaces the raw per-sibling ordinal, which
        # gave independent siblings distinct sequences and serialized ALL
        # delegated work fleet-wide. The cross-cell UX wiring (in the caller)
        # restamps any task it re-wires, so every sequence write in the
        # delegate flow is this one wave computation; merge order for wave
        # ties falls back to created_at in the merge/lane dispatch barriers.
        await self.task.stamp_wave_sequence(new_task.id)
        return new_task


class SubmitUpMixin(_Base):
    """``submit_up``: a cell PM's cell-to-root PR into the PR-review gate, its
    ownership, state and unchanged-PR guards, the sub-task checks it shares
    with ``complete``, and the PR-reviewer notice."""

    async def _non_terminal_subtask_ids(self, parent_task_id: UUID) -> str:
        """Return a human-readable comma-separated list of non-terminal subtasks.

        Used by Gate Set F closure-time guards to name exactly which
        subtasks are blocking parent completion.
        """
        terminal = {"completed", "cancelled"}
        subtasks: list[Any] = await self.task.get_subtasks(parent_task_id)
        non_terminal = [s for s in subtasks if str(s.status) not in terminal]
        if not non_terminal:
            return "(none — query out-of-sync, retry)"
        # Format: "<id> (<status>)"
        return ", ".join(f"{s.id} ({s.status})" for s in non_terminal)

    async def _subtasks_not_terminal_envelope(
        self,
        agent_id: UUID,
        task_id: UUID,
        *,
        context_phrase: str,
    ) -> Envelope | None:
        """Return a tracing_gap rejection if any subtask of ``task_id`` is non-terminal.

        Centralizes the closure-time "all subtasks terminal" gate that fires
        in submit_up, cell_pm_complete, main_pm_complete.
        ``context_phrase`` lets each caller name the action being blocked
        (e.g., "bubbling up", "completing parent").
        """
        if await self.task.all_subtasks_terminal(task_id):
            return None
        non_terminal = await self._non_terminal_subtask_ids(task_id)
        return Envelope.tracing_gap(
            missing=["subtasks not all terminal"],
            remediate=(
                f"all subtasks must be in completed/cancelled before"
                f" {context_phrase}. Non-terminal subtasks: {non_terminal}"
            ),
            context_briefing=await self._briefing_for(agent_id, task_id),
        )

    async def _parent_acs_covered_envelope(
        self,
        agent_id: UUID,
        task_id: UUID,
        *,
        context_phrase: str,
    ) -> Envelope | None:
        """Reject roll-up if any parent acceptance criterion is unsatisfied.

        A parent AC is satisfied only when a COMPLETED child declared it via
        ``covers_parent_criteria`` (→ ``parent_ac_refs``). Safe-by-construction:
        ``uncovered_parent_acceptance_criteria`` returns nothing unless coverage
        is declared on the children, so this is inert for tasks decomposed before
        coverage tracking and bites only once a PM maps children to parent
        criteria. The backstop that stops a half-built parent (criteria silently
        dropped at decompose-time) from rolling up "done" — the PR #175 hole.
        """
        uncovered = await self.task.uncovered_parent_acceptance_criteria(task_id)
        # isinstance keeps the gate inert under partial test mocks (an AsyncMock
        # TaskService returns a truthy stub, not a concrete list) and on any
        # unexpected return — enforce only on a real, non-empty list.
        if not isinstance(uncovered, list) or not uncovered:
            return None
        listing = "; ".join(uncovered)
        return Envelope.tracing_gap(
            missing=["parent acceptance criteria not satisfied"],
            remediate=(
                f"{len(uncovered)} parent acceptance criteria are not covered by a "
                f"completed subtask before {context_phrase}: {listing}. Delegate "
                "(or reassign) subtasks covering them and let those pass QA + "
                "complete first. If a completed subtask already implements a "
                "criterion, stamp it: declare_coverage(task_id=<child>, "
                "criteria=[...])."
            ),
            context_briefing=await self._briefing_for(agent_id, task_id),
        )

    async def _notify_pr_reviewer(self, task_id: UUID, t: Any) -> None:
        """Reassign the in-path PR-review-gate reviewer for this task.

        ``submit_for_review`` clears ``assigned_to`` at gate entry (mirrors
        ``submit_qa``); this explicitly reassigns to the reviewer
        ``TaskService.pr_reviewer_for`` resolves so the task's ownership
        names the real reviewer instead of sitting unassigned in the
        queue. No-op unless the task actually landed in the gate: a
        zero-diff branch is PR-waived straight to ``awaiting_pm_review``
        (see ``VerbRunner``), and reassigning to the reviewer there would
        overwrite the PM ownership that state depends on. The dispatcher
        spawn is the reviewer's signal, not an A2A ping: root level can't
        (the a2a matrix denies PM -> the main PM's root->master reviewer
        outright) and cell level doesn't need one (the gate dispatcher
        spawns the reviewer by status+team on its own).
        """
        from roboco.models.base import TaskStatus

        if str(t.status) != str(TaskStatus.AWAITING_PR_REVIEW):
            return
        reviewer = await self.task.pr_reviewer_for(t)
        if reviewer is not None:
            await self.task.reassign(task_id, reviewer.id)

    async def submit_up(
        self,
        pm_agent_id: UUID,
        task_id: UUID,
        notes: str,
        resolved_findings: list[dict[str, Any]] | None = None,
    ) -> Envelope:
        """Cell PM bubbles a finished cell-scope task up to the Main PM.

        Spec gate runs first and enforces role membership (cell_pm only)
        plus the composed ``submit_pm_review`` action's source-status
        constraint (IN_PROGRESS only). After the gate accepts, the
        verb-specific ``_submit_up_guard`` runs the rest of the
        preflight checks the spec doesn't model: ownership, notes
        length, journal:decision presence, subtasks-terminal, branch
        present, and — via FINDINGS_ADDRESSED — every open pr_gate/pm/
        ceo-origin ledger finding on this root. ``resolved_findings``
        ({finding_id, commit?, note?} entries) marks ledger rows
        addressed BEFORE that gate runs (mirrors ``i_am_done``). Then
        ``VerbRunner.run_intent("submit_up", ...)`` dispatches the
        (submit_pm_review,) atomic chain plus the (create_pr,) side
        effect inside a savepoint. After the runner returns, the task
        is handed off to the Main PM (reassign + a2a).
        """
        t = await self.task.get(task_id)
        briefing = await self._briefing_for(
            pm_agent_id, task_id, task=t, include_ac_coverage=True
        )
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="submit_up",
            )
        agent = await self.task.agent_for(pm_agent_id)
        role_str = str(agent.role) if agent is not None else "cell_pm"
        try:
            role = spec_module.Role(role_str)
        except ValueError:
            return await self._emit_rejection(
                Envelope.not_authorized(
                    message=f"unknown role '{role_str}'",
                    remediate="role is not declared in the lifecycle spec",
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="submit_up",
            )
        actor_slug, agent_team = actor_context_fields(agent)
        spec_ctx = spec_module.Context(
            actor_id=pm_agent_id,
            actor_slug=actor_slug,
            agent_team=agent_team,
            original_developer_slug=_extract_original_developer(t),
            notes=notes,
        )
        soup = self._free_text_soup(checks=(("notes", notes, 10),))
        decision = spec_module.can_invoke_intent(role, "submit_up", t, spec_ctx)
        if env := self._soup_or_decision_env(soup, decision, briefing):
            return await self._emit_rejection(
                env.with_introspection(task=t, role=role_str),
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="submit_up",
            )

        # Write-then-gate: resolve findings before the guard's
        # FINDINGS_ADDRESSED check re-reads open_finding_ids (mirrors
        # i_am_done's _apply_resolved_findings). Owner-gated: a stale
        # non-owner PM's resolutions must not mutate the ledger — the
        # ownership guard below rejects it, but the session still commits.
        if t.assigned_to == pm_agent_id:
            await self._apply_resolved_findings_for(task_id, resolved_findings)
        # Verb-specific preflight: ownership + notes-length + journal:decision
        # + subtasks-terminal + branch-present. None of these are modelled by
        # the spec yet — keep them in the verb body.
        guard = await self._submit_up_guard(pm_agent_id, task_id, t, notes)
        # Cell-level unchanged-PR loop-stopper. Consulted only after the state
        # guard above passes; a prior preflight reject short-circuits before the
        # head-sha comparison runs (mirroring submit_root).
        if guard is None:
            guard = await self._submit_up_unchanged_pr_guard(t, briefing)
        precomputed_ahead: int | None = None
        if guard is None:
            # Assembly integrity (#11) + behind-base auto-sync (B2). Reuses
            # this probe's `ahead` count for the PR-waiver check inside
            # run_intent instead of a second `git fetch origin` against the
            # same branch/base pair.
            guard, precomputed_ahead = await self._assembled_submit_guards(
                t, task_id, "submit_up"
            )
        if guard is not None:
            guard.with_introspection(task=t, role=role_str)
            return await self._emit_rejection(
                guard,
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="submit_up",
            )

        outcome = await self._submit_up_run_intent(
            t, agent, spec_ctx, briefing, role_str, precomputed_ahead=precomputed_ahead
        )
        if isinstance(outcome, Envelope):
            return await self._emit_rejection(
                outcome,
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="submit_up",
            )
        t = outcome
        # Do NOT hand the cell task to Main PM. `submit_for_review` clears
        # PM ownership at gate entry; `_notify_pr_reviewer` immediately
        # reassigns to the cell's in-path gate reviewer (be/fe/ux-pr-
        # reviewer) instead of leaving it unassigned. The cell PM is
        # re-resolved by role when the reviewer passes or fails the gate.
        # Main PM only completes the ROOT (root→master + escalate-to-CEO).
        await self._notify_pr_reviewer(task_id, t)
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=spec_module._INTENT_VERBS["submit_up"].next_hint(t),
            context_briefing=briefing,
        ).with_introspection(task=t, role=role_str)

    async def _submit_up_run_intent(
        self,
        t: Any,
        agent: Any,
        spec_ctx: spec_module.Context,
        briefing: dict[str, Any],
        role_str: str,
        *,
        precomputed_ahead: int | None = None,
    ) -> Any:
        """Dispatch the submit_up composition through VerbRunner.

        Returns the post-composition task on success, or an
        ``invalid_state`` Envelope when the runner raises or the
        underlying ``submit_pm_review`` returns ``None``.
        """
        runner = self._verb_runner()
        try:
            after = await runner.run_intent(
                "submit_up", t, agent, spec_ctx, precomputed_ahead=precomputed_ahead
            )
        except Exception as exc:
            return Envelope.invalid_state(
                message=f"verb runner failed: {exc}",
                remediate="check workspace + retry; if persistent, escalate",
                context_briefing=briefing,
            ).with_introspection(task=t, role=role_str)
        if after is None:
            # The create_pr pre-side-effect already opened the cell→root PR
            # BEFORE submit_for_review ran (its pr_created gate requires it —
            # see lifecycle.py), so a None here means the task raced out of
            # in_progress AFTER the PR was opened: an orphaned PR sits on
            # GitHub whose task is not in awaiting_pr_review. Name the open
            # PR in the remediate so the PM can reconcile it — create_pr is
            # idempotent, so re-issuing once the task is back in_progress
            # re-attaches to the same PR rather than opening a duplicate.
            # Mirrors submit_root's F016 None-envelope remediate.
            return Envelope.invalid_state(
                message=(
                    "submit_up did not transition the task — the cell→root "
                    "PR was already opened or the task is no longer "
                    "in_progress."
                ),
                remediate=(
                    "the cell→root PR is already open (create_pr ran before "
                    "the transition). Re-fetch with evidence(task_id); if it "
                    "is awaiting_pr_review wait for the reviewer, otherwise "
                    "re-delegate the fixes and retry submit_up — create_pr "
                    "is idempotent and re-attaches to the existing PR."
                ),
                context_briefing=briefing,
            )
        return after

    async def _submit_up_guard(
        self, pm_agent_id: UUID, task_id: UUID, t: Any, notes: str
    ) -> Envelope | None:
        """Return a rejection Envelope if any submit_up precondition fails."""
        ownership = await self._submit_up_ownership_guard(
            pm_agent_id, task_id, t, notes
        )
        if ownership is not None:
            return ownership
        return await self._submit_up_state_guard(pm_agent_id, task_id, t, notes)

    async def _submit_up_ownership_guard(
        self, pm_agent_id: UUID, task_id: UUID, t: Any, notes: str
    ) -> Envelope | None:
        """Role + assignment + notes-length guards shared by submit_up + submit_root.

        Both callers run the spec gate (``can_invoke_intent``) first, which
        already restricts submit_up→cell_pm and submit_root→main_pm, so this
        guard must accept either PM role — a hardcoded cell_pm-only check would
        reject the Main PM's submit_root and deadlock root closure (submit_root
        and complete then point at each other). The check stays as a
        defense-in-depth reject of any non-PM actor that reaches here.
        """
        from roboco.config import settings as roboco_settings

        agent = await self.task.agent_for(pm_agent_id)
        if agent is None or agent.role not in ("cell_pm", "main_pm"):
            return Envelope.not_authorized(
                message="submit_up / submit_root are reserved for PM roles",
                remediate=(
                    "only a cell PM (submit_up) or main PM (submit_root)"
                    " may bubble work up"
                ),
                context_briefing=await self._briefing_for(pm_agent_id, task_id),
            )
        if t.assigned_to != pm_agent_id:
            return Envelope.not_authorized(
                message="not assigned to you",
                remediate="claim the task or wait for assignment",
                context_briefing=await self._briefing_for(pm_agent_id, task_id),
            )
        if not notes or len(notes) < roboco_settings.docs_notes_min_chars:
            return Envelope.tracing_gap(
                missing=["notes>=min"],
                remediate=(
                    "submit_up requires substantive notes describing the"
                    " cell's contribution (>= 20 chars)."
                ),
                context_briefing=await self._briefing_for(pm_agent_id, task_id),
            )
        return None

    async def _submit_up_state_guard(
        self, pm_agent_id: UUID, task_id: UUID, t: Any, notes: str
    ) -> Envelope | None:
        """Journal + subtask-closure + branch guards for submit_up.

        Tracing gates (journal:decision, journal:reflect, notes>=min,
        subtasks_terminal) are evaluated by ``_check_submit_up_gates``
        which consumes ``VERB_REQUIREMENTS["submit_up"]``. The
        ``_subtasks_not_terminal_envelope`` call is kept as a fallback
        because its remediation enumerates the non-terminal subtask ids
        — strictly richer than the foundation hint.
        """
        # Write-then-gate: the bubble-up rationale (notes, already validated
        # >= min by the ownership guard) becomes the journal:decision.
        pm_decision_outcome = await self._ensure_pm_decision(
            pm_agent_id, task_id, notes
        )
        if env := await self._check_submit_up_gates(
            pm_agent_id, task_id, notes, pm_decision_outcome=pm_decision_outcome
        ):
            return env
        if env := await self._subtasks_not_terminal_envelope(
            pm_agent_id, task_id, context_phrase="bubbling up"
        ):
            return env
        if env := await self._parent_acs_covered_envelope(
            pm_agent_id, task_id, context_phrase="bubbling up"
        ):
            return env
        if not t.branch_name:
            return Envelope.invalid_state(
                message="task has no branch; cannot open cell-level PR",
                remediate="cell PMs must claim+plan their parent task first",
                context_briefing=await self._briefing_for(pm_agent_id, task_id),
            )
        return None

    async def _submit_up_unchanged_pr_guard(
        self, t: Any, briefing: dict[str, Any]
    ) -> Envelope | None:
        """Cell-level analogue of ``_submit_root_unchanged_pr_guard``.

        Refuses re-submit when the cell PR's current head SHA equals the SHA
        ``pr_fail`` recorded in ``notes_structured.pr_review.head_sha`` (no new
        dev work landed ⇒ byte-identical diff). See ``_unchanged_pr_guard`` for
        the shared logic (including the findings-resolved one-shot exemption).
        """
        return await self._unchanged_pr_guard(
            t,
            briefing,
            verb="submit_up",
            pr_scope="cell",
            delegate_target="owning developer(s)",
            subtask_noun="dev subtasks",
            work_noun="dev work",
        )


class SubmitRootMixin(_Base):
    """``submit_root``: the Main PM's root-to-master PR into the gate, and the
    unchanged-PR loop-stopper ``submit_up`` shares."""

    def _submit_root_preflight(
        self, t: Any, role_str: str, briefing: dict[str, Any]
    ) -> Envelope | None:
        """Reject a submit_root that can never assemble a root→master PR.

        Two early refusals collapse here to keep ``submit_root`` within the
        return-count budget. A MegaTask umbrella assembles no PR of its own — it
        spans many projects (no single master) and each root-subtask opens its
        own PR — so it must never enter the in-path review gate; the Main PM
        completes the umbrella directly once every root-subtask is terminal. And
        an actor whose role is not in the lifecycle spec cannot drive the verb.
        Returns the rejection Envelope (caller adds introspection + emits) or
        None when neither refusal applies.
        """
        if is_batch_umbrella(batch_id=t.batch_id, parent_task_id=t.parent_task_id):
            return Envelope.invalid_state(
                message="a MegaTask umbrella assembles no PR of its own",
                remediate=(
                    "the umbrella spans many projects — each root-subtask opens"
                    " its own PR and is reviewed on its own diff. Do not"
                    " submit_root the umbrella; once every root-subtask is"
                    " terminal, call complete(task_id, notes='...') to escalate"
                    " the MegaTask to the CEO."
                ),
                context_briefing=briefing,
            )
        try:
            spec_module.Role(role_str)
        except ValueError:
            return Envelope.not_authorized(
                message=f"unknown role '{role_str}'",
                remediate="role is not declared in the lifecycle spec",
                context_briefing=briefing,
            )
        return None

    async def _unchanged_pr_guard(
        self,
        t: Any,
        briefing: dict[str, Any],
        *,
        verb: str,
        pr_scope: str,
        delegate_target: str,
        subtask_noun: str,
        work_noun: str,
    ) -> Envelope | None:
        """Shared unchanged-PR loop-stopper for submit_root (root) / submit_up (cell).

        The hard loop-stopper for the 2026-06-27 ``pr_fail`` re-submit loop. A
        prior ``pr_fail`` stamped the assembled PR's head SHA into
        ``notes_structured.pr_review.head_sha`` (see ``_capture_pr_head_sha`` /
        ``_record_gate_verdict``). This looks up the PR's CURRENT head SHA and
        compares. Equal ⇒ no new work has landed on the branch since the fail ⇒
        the diff the reviewer would see is byte-identical to the one just
        rejected. Different ⇒ the branch advanced ⇒ allow.

        A same-head resubmit isn't always a loop: when the rejection round's
        findings needed no code change (e.g. a transient CI-lookup error
        ledgered as a finding, then waived/addressed), a flat refuse deadlocks
        the task forever — the head sha can never move because there is
        nothing to fix. So once every ledger finding is resolved
        (``_open_finding_ids`` empty), ONE resubmission is exempted per head
        sha (``markers.resubmit_unchanged_head``); a second attempt at the SAME
        head still refuses — one-shot, not a standing bypass.

        Returns ``None`` (proceed) on every ambiguous case so the gate FAILS
        OPEN: no prior ``pr_fail`` verdict, no recorded ``head_sha`` (e.g. a
        verdict written before this field existed), no ``pr_number``, no
        resolvable project, or a git/closed-PR lookup that returns ``None``.
        """
        pr_review = (getattr(t, "notes_structured", None) or {}).get("pr_review") or {}
        if pr_review.get("verdict") != "failed":
            return None
        recorded = pr_review.get("head_sha")
        if not recorded:
            return None
        current = await self._current_pr_head_sha(t)
        if current is None or current != recorded:
            return None
        exemption_note = ""
        if not await self._open_finding_ids(t.id):
            if markers.get_resubmit_unchanged_head(t) == current:
                exemption_note = (
                    " (the one-shot exemption for this head was already used)"
                )
            else:
                markers.set_resubmit_unchanged_head(t, current)
                logger.info(
                    "unchanged-PR resubmit exemption granted — all findings"
                    " resolved without a code change",
                    task_id=str(t.id),
                    verb=verb,
                    head=current[:7],
                )
                return None
        return Envelope.invalid_state(
            message=(
                f"the assembled {pr_scope} PR is unchanged since the last"
                f" pr_fail (head {current[:7]}). No new {work_noun} has"
                f" landed on the {pr_scope} branch, so re-submitting would"
                " re-open the exact diff the reviewer just rejected and loop"
                f" straight back to awaiting_pr_review.{exemption_note}"
            ),
            remediate=(
                f"re-delegate the fixes to the {delegate_target} via"
                f" delegate(...) and wait for the {subtask_noun} to complete"
                f" and the {pr_scope} branch to be re-assembled. Do NOT call"
                f" {verb} again until new {work_noun} has advanced the"
                f" {pr_scope} branch HEAD."
            ),
            context_briefing=briefing,
        )

    async def _submit_root_unchanged_pr_guard(
        self, t: Any, briefing: dict[str, Any]
    ) -> Envelope | None:
        """Refuse to re-submit a root PR whose diff is unchanged since the last
        fail. See ``_unchanged_pr_guard`` for the shared logic."""
        return await self._unchanged_pr_guard(
            t,
            briefing,
            verb="submit_root",
            pr_scope="root",
            delegate_target="owning cell PM(s)",
            subtask_noun="cell subtasks",
            work_noun="cell work",
        )

    async def _current_pr_head_sha(self, t: Any) -> str | None:
        """Best-effort current head SHA of the task's assembled PR (fail-open).

        The lookup both unchanged-PR gates (submit_root + submit_up)
        compare against — ``pr_fail`` stamps the head SHA for cell AND root
        gate tasks alike (the capture is gate-verb-level, not root-level), so
        one resolver serves both. Returns ``None`` on every ambiguous case (no
        ``pr_number``, no resolvable project slug, a git error, or a
        closed/missing PR) so the gate fails open rather than wedging the PM
        — only the exact-unchanged case is hard-blocked.
        """
        pr_number = getattr(t, "pr_number", None)
        if not pr_number:
            return None
        try:
            # The shared ``_project_slug_for`` resolver (``PRReviewerMixin``; it
            # delegates to the module-level ``resolve_task_project_slug``). Same
            # resolver the pr_fail capture path uses, so one stub controls both
            # gate paths.
            slug = await self._project_slug_for(t)
            if not slug:
                return None
            sha = await self.git.get_pr_head_sha(slug, int(pr_number))
            return sha if isinstance(sha, str) else None
        except Exception as exc:
            # Fail-open (never wedge the PM on a lookup error), but log so a
            # regression in the slug resolver / git helper doesn't silently
            # turn the pr_fail re-submit loop-stopper into a no-op (#5).
            logger.warning(
                "unchanged_pr_guard head_sha lookup failed (fail-open)",
                pr_number=pr_number,
                error=str(exc),
            )
            return None

    async def submit_root(
        self,
        main_pm_agent_id: UUID,
        task_id: UUID,
        notes: str,
        resolved_findings: list[dict[str, Any]] | None = None,
    ) -> Envelope:
        """Main PM opens the root→master PR and enters the in-path review gate.

        The root analogue of the cell PM's submit_up: the spec gate (main_pm +
        in_progress) runs first, then the same preflight guard (ownership, notes
        length, journal:decision, subtasks-terminal, branch present, and every
        open pr_gate/pm/ceo-origin ledger finding via FINDINGS_ADDRESSED).
        ``resolved_findings`` ({finding_id, commit?, note?} entries) marks
        ledger rows addressed BEFORE that gate runs (mirrors ``i_am_done`` /
        ``submit_up``). Then ``VerbRunner.run_intent("submit_root", ...)``
        opens the root→master PR (the ``create_root_pr`` pre-side-effect) and
        transitions in_progress → awaiting_pr_review. The main reviewer then
        reviews the assembled root→master diff; after pr_pass, ``complete``
        escalates to the CEO.
        """
        t = await self.task.get(task_id)
        briefing = await self._briefing_for(
            main_pm_agent_id, task_id, task=t, include_ac_coverage=True
        )
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=main_pm_agent_id,
                task_id=task_id,
                verb="submit_root",
            )
        agent = await self.task.agent_for(main_pm_agent_id)
        role_str = str(agent.role) if agent is not None else "main_pm"
        # Hard-reject an umbrella (no PR of its own) or an unknown role before the
        # spec gate; the preflight collapses both into one Envelope-or-None.
        if env := self._submit_root_preflight(t, role_str, briefing):
            return await self._emit_rejection(
                env.with_introspection(task=t, role=role_str),
                agent_id=main_pm_agent_id,
                task_id=task_id,
                verb="submit_root",
            )
        role = spec_module.Role(role_str)
        actor_slug, agent_team = actor_context_fields(agent)
        spec_ctx = spec_module.Context(
            actor_id=main_pm_agent_id,
            actor_slug=actor_slug,
            agent_team=agent_team,
            notes=notes,
        )
        soup = self._free_text_soup(checks=(("notes", notes, 10),))
        decision = spec_module.can_invoke_intent(role, "submit_root", t, spec_ctx)
        if env := self._soup_or_decision_env(soup, decision, briefing):
            return await self._emit_rejection(
                env.with_introspection(task=t, role=role_str),
                agent_id=main_pm_agent_id,
                task_id=task_id,
                verb="submit_root",
            )
        # Write-then-gate: resolve findings before the guard's
        # FINDINGS_ADDRESSED check re-reads open_finding_ids. Owner-gated:
        # a stale non-owner PM's resolutions must not mutate the ledger —
        # the ownership guard below rejects it, but the session still commits.
        if t.assigned_to == main_pm_agent_id:
            await self._apply_resolved_findings_for(task_id, resolved_findings)
        # Same preflight as submit_up — ownership + notes-length +
        # journal:decision + subtasks-terminal + branch-present. submit_root's
        # tracing requirements mirror submit_up's, so the shared guard applies.
        guard = await self._submit_up_guard(main_pm_agent_id, task_id, t, notes)
        # Hard unchanged-PR gate (the 2026-06-27 pr_fail re-submit loop-stopper).
        # A hint (the pr_fail a2a / next-hint steer "do NOT re-submit") is ignored
        # by a weak coordinator (minimax-m3 re-submitted PR #139 byte-identical),
        # so this refuses the re-submit STRUCTURALLY: if the last pr_fail stamped
        # the assembled PR's head SHA and the current PR head SHA is the same, no
        # new cell work has landed on the root branch since the fail — re-running
        # submit_root would re-open / re-push the exact diff the reviewer just
        # rejected, looping straight back to awaiting_pr_review → pr_fail. Force
        # the PM to re-delegate the fixes and wait for re-assembly instead.
        # Fail-open on ANY ambiguity (no prior fail, no recorded sha, no
        # resolvable project, git error, closed/missing PR) — only the
        # exact-unchanged case is hard-blocked; everything else proceeds and
        # relies on the reviewer to re-fail if the diff is still bad.
        if guard is None:
            guard = await self._submit_root_unchanged_pr_guard(t, briefing)
        precomputed_ahead: int | None = None
        if guard is None:
            # Assembly integrity (#11) + behind-base auto-sync (B2). Reuses
            # this probe's `ahead` count for the PR-waiver check inside
            # run_intent instead of a second `git fetch origin` against the
            # same branch/base pair.
            guard, precomputed_ahead = await self._assembled_submit_guards(
                t, task_id, "submit_root"
            )
        if guard is not None:
            guard.with_introspection(task=t, role=role_str)
            return await self._emit_rejection(
                guard,
                agent_id=main_pm_agent_id,
                task_id=task_id,
                verb="submit_root",
            )
        runner = self._verb_runner()
        try:
            t = await runner.run_intent(
                "submit_root",
                t,
                agent,
                spec_ctx,
                precomputed_ahead=precomputed_ahead,
            )
        except Exception as exc:
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message=f"verb runner failed: {exc}",
                    remediate=(
                        "ensure all subtasks are terminal + the branch exists,"
                        " then retry submit_root"
                    ),
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
                agent_id=main_pm_agent_id,
                task_id=task_id,
                verb="submit_root",
            )
        # submit_for_review returns None when the root->master PR was already
        # opened (race out of in_progress / prior call). The PR exists but the
        # transition did not — guard the None deref with an invalid_state so
        # the PM re-fetches and reconciles instead of crashing.
        return await self._submit_root_finalize(
            main_pm_agent_id, task_id, t, role_str, briefing
        )

    async def _submit_root_finalize(
        self,
        main_pm_agent_id: UUID,
        task_id: UUID,
        t: Any,
        role_str: str,
        briefing: dict[str, Any],
    ) -> Envelope:
        """Build the submit_root result envelope after the verb runner returns.

        ``None`` → invalid_state rejection (the root->master PR was
        already opened / the task raced out of in_progress); otherwise the
        success envelope keyed off the post-transition status.
        """
        if t is None:
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message=(
                        "submit_root did not transition the task — the "
                        "root->master PR was already opened or the task is no "
                        "longer in_progress."
                    ),
                    remediate=(
                        "re-fetch with evidence(task_id); if it is "
                        "awaiting_pr_review the PR is already open — wait for "
                        "the reviewer; otherwise re-delegate the fixes and "
                        "retry submit_root."
                    ),
                    context_briefing=briefing,
                ),
                agent_id=main_pm_agent_id,
                task_id=task_id,
                verb="submit_root",
            )
        # `submit_for_review` clears Main PM ownership at gate entry;
        # reassign to the root->master gate reviewer (pr-reviewer-1)
        # instead of leaving it unassigned.
        await self._notify_pr_reviewer(task_id, t)
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=spec_module._INTENT_VERBS["submit_root"].next_hint(t),
            context_briefing=briefing,
        ).with_introspection(task=t, role=role_str)


class PMWorkMixin(_Base):
    """``pm_give_me_work``, ``triage`` and ``triage_all``: what a PM looks at
    next, and the hint toward its own pending reviews."""

    async def pm_give_me_work(self, pm_agent_id: UUID) -> Envelope:
        """Return the PM's first assigned task in any active status, or idle.

        Mirrors the developer's give_me_work but does not filter to dev-only
        statuses — PMs care about all assigned tasks (planning, paused, in
        progress, awaiting_pm_review).

        Pre-assigned pending tasks are checked first.
        Smoke run 3 showed Main PM getting idle even though c7935d2c was
        pending and assigned_to=main-pm because list_assigned_for_agent
        ordered by priority/updated_at and could rank a pre-assigned pending
        task below other active rows; the pre-assigned pending check now
        wins unconditionally.
        """
        # Pre-assigned pending tasks take priority over everything else.
        pre_assigned = await self._drop_access_denied(
            await self.task.list_pending_for_agent(pm_agent_id), pm_agent_id
        )
        if pre_assigned:
            t = pre_assigned[0]
            await self._touch(t.id)
            return Envelope.ok(
                status=str(t.status),
                task_id=str(t.id),
                next=self._pm_next_hint(str(t.status), t.id),
                context_briefing=await self._briefing_for(
                    pm_agent_id, t.id, task=t, full=True
                ),
            )
        assigned = await self._drop_access_denied(
            await self.task.list_assigned_for_agent(pm_agent_id), pm_agent_id
        )
        if assigned:
            t = assigned[0]
            await self._touch(t.id)
            return Envelope.ok(
                status=str(t.status),
                task_id=str(t.id),
                next=self._pm_next_hint(str(t.status), t.id),
                context_briefing=await self._briefing_for(
                    pm_agent_id, t.id, task=t, full=True
                ),
            )
        return Envelope.ok(
            status="idle",
            task_id=None,
            next="no assigned work — call triage() or i_am_idle()",
            context_briefing=await self._briefing_for(pm_agent_id, None),
        )

    @staticmethod
    def _pm_next_hint(status: str, task_id: Any) -> str:
        """Compose a status-aware next hint for PMs."""
        if status == "pending":
            return f"call i_will_plan(task_id='{task_id}', plan='...') to start"
        if status == "paused":
            return (
                f"check subtasks; when terminal, call complete(task_id='{task_id}')"
                " or submit_up()"
            )
        if status == "blocked":
            return f"investigate then unblock(task_id='{task_id}')"
        if status == "awaiting_pm_review":
            return f"review and complete(task_id='{task_id}')"
        return f"act on task {task_id} — current status: {status}"

    async def triage(self, pm_agent_id: UUID) -> Envelope:
        """Cell PM triage: blocked > awaiting_pm_review > idle."""
        pm = await self.task.agent_for(pm_agent_id)
        blocked = await self.task.list_blocked_for_team(pm.team)
        if blocked:
            t = blocked[0]
            return Envelope.ok(
                status=str(t.status),
                task_id=str(t.id),
                next=f"investigate the block, then unblock(task_id='{t.id}')",
                context_briefing=await self._briefing_for(
                    pm_agent_id, t.id, task=t, full=True
                ),
            )
        awaiting = await self.task.list_awaiting_pm_review_for_team(pm.team)
        if awaiting:
            t = awaiting[0]
            return Envelope.ok(
                status=str(t.status),
                task_id=str(t.id),
                next=f"review and complete(task_id='{t.id}')",
                context_briefing=await self._briefing_for(
                    pm_agent_id, t.id, task=t, full=True
                ),
            )
        return Envelope.ok(
            status="idle",
            task_id=None,
            next="no PM work — call i_am_idle",
            context_briefing=await self._briefing_for(pm_agent_id, None),
        )

    async def triage_all(self, pm_agent_id: UUID) -> Envelope:
        """Main PM triage: across all teams. blocked > awaiting_pm_review > idle."""
        blocked = await self.task.list_blocked_all_teams()
        if blocked:
            t = blocked[0]
            return Envelope.ok(
                status=str(t.status),
                task_id=str(t.id),
                next=(
                    f"escalation/cross-cell help required: investigate, then "
                    f"unblock(task_id='{t.id}') or escalate_up()"
                ),
                context_briefing=await self._briefing_for(
                    pm_agent_id, t.id, task=t, full=True
                ),
            )
        awaiting = await self.task.list_awaiting_main_pm_all()
        if awaiting:
            t = awaiting[0]
            return Envelope.ok(
                status=str(t.status),
                task_id=str(t.id),
                next=f"complete(task_id='{t.id}') opens master PR + escalates to CEO",
                context_briefing=await self._briefing_for(
                    pm_agent_id, t.id, task=t, full=True
                ),
            )
        return Envelope.ok(
            status="idle",
            task_id=None,
            next="no Main PM work",
            context_briefing=await self._briefing_for(pm_agent_id, None),
        )

    async def _own_review_hint(self, pm_agent_id: UUID, exclude_task_id: UUID) -> str:
        """Remediate suffix naming the PM's OWN task ready to complete.

        A PM looped firing complete/unblock at the
        wrong (parent) task_id while its own leaf sat at
        ``awaiting_pm_review``, never named in any rejection — minimax
        never found the one correct call. Surface it explicitly.
        Best-effort: never raises into the rejection path.
        """
        try:
            owned = await self.task.list_by_assignee(pm_agent_id)
        except Exception:
            return ""
        ready = [
            str(o.id)
            for o in owned
            if str(o.status) == "awaiting_pm_review" and o.id != exclude_task_id
        ]
        if not ready:
            return ""
        tid = ready[0]
        return (
            f" You OWN task {tid} which is awaiting_pm_review and ready to "
            f"finish — call complete(task_id='{tid}', notes='...') on THAT "
            "task, not this one."
        )


class PMReviewMixin(_Base):
    """A PM's corrective verbs: ``unblock``, with the oscillation breaker and
    the block-flip notices, and ``request_changes`` with its findings."""

    async def _unblock_preflight_guards(
        self, pm_agent_id: UUID, task_id: UUID, t: Any, role: str
    ) -> Envelope | None:
        """Verb-specific preflight gates for ``unblock``, checked in order;
        the first rejection wins, ``None`` means proceed. Factored out to
        keep ``unblock`` under the return-count budget as gates accrete
        (status / dependency / budget / oscillation).
        """
        if str(t.status) != "blocked":
            return Envelope.invalid_state(
                message=f"task {task_id} is in {t.status}, expected blocked",
                remediate=(
                    "this task is not blocked; call triage() to find blocked tasks"
                ),
                context_briefing=await self._briefing_for(pm_agent_id, task_id),
            ).with_introspection(task=t, role=role)

        # A dependency block must not be cleared by hand. It auto-clears via
        # _unblock_dependents the moment its last dependency reaches a terminal
        # state; forcing it now would let the dependent proceed without the
        # upstream's work (e.g. a frontend task built before its UX design lands).
        dep_ids = list(t.dependency_ids or [])
        unmet = await self.task.unmet_dependency_ids(dep_ids) if dep_ids else []
        if unmet:
            return Envelope.invalid_state(
                message=(
                    f"task {task_id} still depends on {len(unmet)} "
                    "unfinished task(s); a dependency block clears on its "
                    "own once the upstream work completes"
                ),
                remediate=(
                    "don't force this — let the dependency finish; the task "
                    "auto-unblocks the moment its last dependency reaches "
                    "completed/cancelled"
                ),
                context_briefing=await self._briefing_for(pm_agent_id, task_id),
            ).with_introspection(task=t, role=role)

        # Budget-breach block: re-check spend-vs-cap before letting the PM
        # clear it. Without this, a PM unblock on a task the orchestrator's
        # sweep blocked for a $ overrun would silently re-breach the same cap
        # the very next tick if the CEO's raise didn't actually clear it (or
        # never raised it at all).
        if guard := await self._budget_unblock_guard(t):
            return guard.with_introspection(task=t, role=role)

        # Oscillation breaker: once tripped (an escalate_up/unblock round
        # trip repeated past the threshold with no progress between rounds),
        # only a human admin override clears it — see
        # _oscillation_unblock_guard / _maybe_trip_oscillation_breaker.
        if guard := self._oscillation_unblock_guard(t):
            return guard.with_introspection(task=t, role=role)

        return None

    async def unblock(
        self, pm_agent_id: UUID, task_id: UUID, reason: str, *, restore: bool = True
    ) -> Envelope:
        """PM unblocks task; ``reason`` documents why the block is cleared and
        is recorded as the journal:decision the gate requires. restore=True
        (default) returns to pre_block_state."""
        t = await self.task.get(task_id)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="unblock",
            )
        agent = await self.task.agent_for(pm_agent_id)
        role = str(agent.role) if agent is not None else "cell_pm"
        if env := await self._unblock_preflight_guards(pm_agent_id, task_id, t, role):
            return await self._emit_rejection(
                env, agent_id=pm_agent_id, task_id=task_id, verb="unblock"
            )

        # Write-then-gate: the PM's unblock reason is recorded as the
        # journal:decision the gate below requires, so a PM that didn't
        # pre-call note(scope='decision') doesn't stall in a tracing_gap
        # respawn loop.
        pm_decision_outcome = await self._ensure_pm_decision(
            pm_agent_id, task_id, reason
        )
        if env := await self._check_pm_decision_required(
            "unblock", pm_agent_id, task_id, t, pm_decision_outcome=pm_decision_outcome
        ):
            return await self._emit_rejection(
                env.with_introspection(task=t, role=role),
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="unblock",
            )

        # Captured before the restore below clears it (_restore_block_ownership)
        # — the oscillation-trip notification names both sides of the round trip.
        escalator_id = t.blocker_raised_by
        t = await self.task.unblock_with_restore(pm_agent_id, task_id, restore=restore)
        await self._maybe_notify_block_flip(task_id, t, t.title)
        if tripped := await self._maybe_trip_oscillation_breaker(
            task_id, t, escalator_id=escalator_id, resolver_id=pm_agent_id
        ):
            return tripped.with_introspection(task=t, role=role)
        next_msg = (
            "task restored to its pre-block state — original assignee will resume"
            if restore
            else "task back to in_progress; you'll need to re-engage the workflow"
        )
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=next_msg,
            context_briefing=await self._briefing_for(pm_agent_id, task_id),
        ).with_introspection(task=t, role=role)

    async def _maybe_notify_block_flip(
        self, task_id: UUID, t: Any, task_title: str | None = None
    ) -> None:
        """Bump the flip counter; alert the CEO once past the threshold.

        A resolver that keeps unblocking a task that keeps re-blocking
        (fe-pm escalate_up auto-blocks / main_pm unblocks, live incident: 10
        flips, 43 spawns) is a structural wedge, not automatic recovery
        working as intended. Signal it once at the 3rd flip; the unblock
        itself always still succeeds regardless.
        """
        flip_count = markers.bump_block_flip_count(t)
        if (
            flip_count >= _BLOCK_FLIP_NOTIFY_THRESHOLD
            and not markers.is_block_flip_notified(t)
        ):
            markers.mark_block_flip_notified(t)
            await self._notify_ceo_block_flip(task_id, flip_count, task_title)

    async def _notify_ceo_block_flip(
        self, task_id: UUID, flip_count: int, task_title: str | None = None
    ) -> None:
        """Best-effort CEO alert for a repeating block/unblock cycle; never
        raises — the breaker signals, it does not wedge unblock itself."""
        from roboco.services.notification import NotificationService

        try:
            await NotificationService().send_block_flip_notification(
                task_id=str(task_id), flip_count=flip_count, task_title=task_title
            )
        except Exception:
            logger.warning(
                "failed to send CEO block-flip notification",
                task_id=str(task_id),
                flip_count=flip_count,
            )

    def _oscillation_unblock_guard(self, t: Any) -> Envelope | None:
        """Refuse ``unblock`` once the oscillation breaker has tripped.

        Unlike the budget guard (``_budget_unblock_guard``) there is no live
        condition to re-check here (no "spend dropped below cap" analogue) —
        a human must actively resolve this: reassign the task, fix whatever
        the escalation kept citing, or cancel it. An admin status override
        (``admin_set_status`` out of BLOCKED) bypasses this guard entirely and
        clears the marker as part of that restore, so the human path stays
        open regardless.
        """
        if not markers.is_oscillation_tripped(t):
            return None
        return Envelope.invalid_state(
            message=(
                f"task {t.id} was force-blocked: its escalate_up/unblock "
                "cycle repeated with no progress between rounds"
            ),
            remediate=(
                "this needs a human to resolve — reassign the task, fix the "
                "underlying blocker, or cancel it; if it is a zero-diff leaf "
                "(no commits, no PR, nothing left to verify - e.g. a fix "
                "sibling merged first), the owning PM closes it with "
                "cancel_leaf(task_id, reason) instead of looping; unblock() "
                "will keep refusing until an admin moves the task out of "
                "blocked"
            ),
        )

    async def _maybe_trip_oscillation_breaker(
        self,
        task_id: UUID,
        t: Any,
        *,
        escalator_id: Any,
        resolver_id: UUID,
    ) -> Envelope | None:
        """Force-BLOCK a task whose escalate/unblock cycle keeps repeating
        with no progress; returns the envelope to hand back on a fresh trip,
        else ``None``.

        The progress fingerprint is deliberately cheap — commit count +
        revision_count are already loaded on ``t``, plus one COUNT query for
        terminal children. The child count matters because a PM coordination
        root never commits itself (all real progress lands on child rows), so
        the first two components alone are structurally static on one — a
        busy root would false-trip on its 6th lifetime legitimate escalation
        even with children completing between rounds. Findings only move via
        QA/PR-review/PM-reject, a different lifecycle path from
        escalate_up/unblock entirely, so they carry no signal here.
        """
        terminal_children = await self.task.terminal_children_count(task_id)
        progress_fp = [
            len(t.commits or []),
            int(t.revision_count or 0),
            terminal_children,
        ]
        strikes = markers.bump_oscillation_strikes(t, progress_fp)
        if strikes <= _OSCILLATION_TRIP_THRESHOLD:
            return None
        from roboco.models.base import BlockerResolverType, TaskStatus

        try:
            t.blocker_resolver_type = BlockerResolverType.HUMAN
            await self.task.admin_set_status(
                task_id, TaskStatus.BLOCKED, actor_role="system"
            )
            markers.mark_oscillation_tripped(t)
        except Exception:
            logger.warning(
                "failed to force-block oscillating task",
                task_id=str(task_id),
                strikes=strikes,
            )
            return None
        await self._notify_ceo_oscillation(
            task_id,
            strikes,
            t.title,
            escalator_id=escalator_id,
            resolver_id=resolver_id,
        )
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=(
                "this task's escalate/unblock cycle repeated with no progress "
                "and was force-blocked for a human to resolve — do not call "
                "unblock again; wait for the CEO, or if this is a zero-diff "
                "leaf (no commits, no PR - e.g. a fix sibling merged first) "
                "the owning PM can call cancel_leaf(task_id, reason)"
            ),
        )

    async def _notify_ceo_oscillation(
        self,
        task_id: UUID,
        strikes: int,
        task_title: str | None,
        *,
        escalator_id: Any,
        resolver_id: UUID,
    ) -> None:
        """Best-effort CEO alert when the oscillation breaker trips; never
        raises — the block itself already happened regardless."""
        from roboco.services.notification import NotificationService

        try:
            await NotificationService().send_oscillation_blocked_notification(
                task_id=str(task_id),
                strikes=strikes,
                escalator=escalator_id,
                resolver=resolver_id,
                db_session=self.task.session,
                task_title=task_title,
            )
        except Exception:
            logger.warning(
                "failed to send CEO oscillation-blocked notification",
                task_id=str(task_id),
                strikes=strikes,
            )

    @staticmethod
    def _validate_request_changes_findings(
        t: Any, issues: list[str] | None, findings: list[dict[str, Any]] | None
    ) -> tuple[list[Any], Envelope | None]:
        """Normalize + validate request_changes's findings — mirrors QA's
        fail_review / the in-path gate's pr_fail helpers.

        Returns ``(validated, rejection)``; the caller attaches
        ``context_briefing``/introspection (this helper has no ``self``).
        """
        raw = findings_lib.merge_findings_and_issues(findings, issues)
        if not raw:
            return [], Envelope.invalid_state(
                message="request_changes requires at least one finding",
                remediate=(
                    "pass findings=[{file, severity, expected, actual}, ...] "
                    "(issues=['...'] is still accepted this release, deprecated)"
                ),
            )
        if cap := findings_lib.findings_count_guard(raw):
            return [], cap
        try:
            validated = validate_findings(raw)
        except ContentValidationError as exc:
            return [], Envelope.invalid_state(
                message=f"malformed finding: {exc.field} — {exc.reason}",
                remediate=(
                    "each finding needs expected + actual (file/line/severity/"
                    "criterion/fix/evidence optional)"
                ),
            )
        if unknown := findings_lib.unknown_finding_criteria(t, validated):
            return [], findings_lib.criterion_mismatch_rejection(t, unknown)
        return validated, None

    async def _attach_request_changes_findings(
        self, t: Any, agent: Any, role_str: str, validated: list[Any]
    ) -> str:
        """Insert request_changes's findings into the ledger, write the
        ``PmReviewContent`` note (``pm_notes``), and return the id-prefixed
        rendering used for the a2a body to the new owner."""
        # GatewayAgentView carries no slug field — falls back to the role
        # string (mirrors _post_gate_review's reviewer_slug fallback).
        actor_slug = (
            getattr(agent, "slug", None) or role_str if agent is not None else role_str
        )
        _, summary = await findings_lib.insert_and_render(
            self.task.session,
            task_id=t.id,
            origin="pm",
            round=findings_lib.next_round(t),
            author_slug=actor_slug,
            findings=validated,
        )
        try:
            apply_structured_note(
                t,
                "pm_review",
                {
                    "summary": summary,
                    "findings": [f.model_dump(mode="json") for f in validated],
                },
            )
        except ContentValidationError:
            logger.warning(
                "request_changes pm_review note skipped (invalid content)",
                task_id=str(t.id),
            )
        return summary

    async def request_changes(
        self,
        pm_agent_id: UUID,
        task_id: UUID,
        issues: list[str] | None = None,
        findings: list[dict[str, Any]] | None = None,
    ) -> Envelope:
        """PM rejects the merge review with concrete findings; → needs_revision.

        The merge-level reject at awaiting_pm_review (the PM's only other
        verbs there are complete/escalate — an AC violation caught at merge
        review used to loop block→escalate). ``findings`` is the structured
        revision-findings ledger entry (wire Pattern A); ``issues`` (free
        text) is still accepted for one release and shimmed into file-less
        findings (deprecated). Spec gate enforces role + source status; the
        findings are inserted into the ledger and written as a structured
        ``PmReviewContent`` note (``pm_notes``) BEFORE the composed
        ``request_changes`` atomic routes the revision like a QA fail, then
        the verb body a2a-delivers the same rendering to the new owner so
        the reject reason is never stranded.
        """
        t = await self.task.get(task_id)
        briefing = await self._briefing_for(pm_agent_id, task_id, task=t)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="request_changes",
            )
        agent = await self.task.agent_for(pm_agent_id)
        role_str = str(agent.role) if agent is not None else "cell_pm"
        validated, bad = self._validate_request_changes_findings(t, issues, findings)
        if bad is not None:
            bad.context_briefing = briefing
            return await self._emit_rejection(
                bad.with_introspection(task=t, role=role_str),
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="request_changes",
            )
        # Placeholder (id-less) rendering drives the spec gate's free-text
        # check — the ledger ids don't exist until after insert, below.
        notes = findings_lib.render_findings_summary([(None, f) for f in validated])
        rejection, spec_gate = await self._request_changes_spec_gate(
            pm_agent_id=pm_agent_id,
            task_id=task_id,
            t=t,
            agent=agent,
            role_str=role_str,
            notes=notes,
            issues=[],
        )
        if rejection is not None:
            return rejection
        _role, spec_ctx = spec_gate
        summary = await self._attach_request_changes_findings(
            t, agent, role_str, validated
        )
        runner = self._verb_runner()
        try:
            t = await runner.run_intent("request_changes", t, agent, spec_ctx)
        except Exception as exc:
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message=f"verb runner failed: {exc}",
                    remediate="re-fetch the task and retry; if persistent, escalate",
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="request_changes",
            )
        # Close the signal loop — the reject reason must reach whoever owns
        # the revision (mirrors fail_review / the pr_fail loop-closer).
        warning = await self._notify_request_changes_owner(
            pm_agent_id, task_id, t, summary
        )
        env = Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=spec_module._INTENT_VERBS["request_changes"].next_hint(t),
            context_briefing=briefing,
        ).with_introspection(task=t, role=role_str)
        hint = findings_lib.findings_count_hint(validated)
        if warning:
            env.warning = f"{warning} {hint}" if hint else warning
        elif hint:
            env.warning = hint
        return env

    async def _notify_request_changes_owner(
        self, pm_agent_id: UUID, task_id: UUID, t: Any, summary: str
    ) -> str | None:
        """Best-effort a2a the reject rendering to the revision owner.

        Returns a warning string when the notification failed (the
        needs_revision transition plus the ledger rows and the pm_notes note
        are already committed at this point), else None. Pulled out of
        ``request_changes`` to keep it under the cyclomatic bound. A2AService
        .send raises on policy denial, unknown recipients, and transient DB
        errors — a failure here must degrade to a warning in the unchanged
        success envelope, never reject the verb or roll the bounce back.
        """
        if t.assigned_to is None or t.assigned_to == pm_agent_id:
            return None
        try:
            # Savepoint: a2a.send's flush would otherwise poison the shared
            # session on a mid-flush failure — the response commit
            # (DbCommitMiddleware) reuses it right after this returns, and
            # it must still carry the needs_revision transition, the
            # findings-ledger rows, and the pm_notes note already written
            # earlier this request.
            async with self.task.session.begin_nested():
                await self.a2a.send(
                    from_agent=pm_agent_id,
                    to_agent=t.assigned_to,
                    skill="code_review",
                    task_id=task_id,
                    body=f"PM merge review needs changes.\n{summary}",
                )
        except Exception as exc:
            # The savepoint rollback here can also expire attributes on `t`
            # (mutated earlier this request by request_changes' own
            # transition) — refresh before the caller reads t.status /
            # with_introspection(t) building the envelope right after this
            # returns. A refresh failure means the DB is genuinely broken;
            # let it raise rather than silently return a warning off a task
            # object that will itself blow up on read.
            await self.task.session.refresh(t)
            logger.warning(
                "request_changes a2a to revision owner failed — "
                "transition committed, notification did not fire",
                task_id=str(task_id),
                recipient=str(t.assigned_to),
                error=repr(exc),
            )
            return (
                f"request_changes transition committed but the a2a "
                f"notification to the revision owner ({t.assigned_to}) "
                f"failed ({exc!r}). The reject reason is on the task's "
                f"findings ledger — re-issue it via dm."
            )
        return None

    async def _request_changes_spec_gate(
        self,
        *,
        pm_agent_id: UUID,
        task_id: UUID,
        t: Any,
        agent: Any,
        role_str: str,
        notes: str,
        issues: list[str],
    ) -> tuple[Envelope | None, Any]:
        """Role + spec + free-text gates for request_changes.

        Returns ``(rejection, None)`` or ``(None, (role, spec_ctx))``.
        """
        try:
            role = spec_module.Role(role_str)
        except ValueError:
            return (
                await self._emit_rejection(
                    Envelope.not_authorized(
                        message=f"unknown role '{role_str}'",
                        remediate="role is not declared in the lifecycle spec",
                        context_briefing=await self._briefing_for(pm_agent_id, task_id),
                    ).with_introspection(task=t, role=role_str),
                    agent_id=pm_agent_id,
                    task_id=task_id,
                    verb="request_changes",
                ),
                None,
            )
        spec_ctx = spec_module.Context(
            actor_id=pm_agent_id,
            actor_slug=getattr(agent, "slug", None) if agent is not None else None,
            agent_team=str(agent.team) if agent is not None and agent.team else None,
            original_developer_slug=_extract_original_developer(t),
            notes=notes,
            issues=tuple(issues),
        )
        decision = spec_module.can_invoke_intent(role, "request_changes", t, spec_ctx)
        if not decision.allowed:
            briefing = await self._briefing_for(pm_agent_id, task_id, task=t)
            return (
                await self._emit_rejection(
                    Envelope.from_decision(
                        decision, briefing=briefing
                    ).with_introspection(task=t, role=role_str),
                    agent_id=pm_agent_id,
                    task_id=task_id,
                    verb="request_changes",
                ),
                None,
            )
        if soup := await self._guard_free_text(
            checks=(("issues", issues, 8),),
            task=t,
            agent_id=pm_agent_id,
            role_str=role_str,
            verb="request_changes",
        ):
            return (soup, None)
        return (None, (role, spec_ctx))


class CompleteMixin(_Base):
    """``complete``, ``cell_pm_complete`` and ``main_pm_complete``: merging a
    child up, the merge-conflict and CEO-only routes, advancing the parent,
    and escalating the root to the CEO."""

    async def _cell_pm_complete_guard(
        self, pm_agent_id: UUID, task_id: UUID, t: Any, notes: str
    ) -> Envelope | None:
        """Return a rejection Envelope if pre-merge guards fail; else None.

        Tracing gates (journal:decision, journal:reflect, notes>=min) are
        evaluated by ``_check_complete_gates`` which consumes
        ``VERB_REQUIREMENTS["complete"]``. The
        ``_subtasks_not_terminal_envelope`` call is kept inline because
        its remediation enumerates the non-terminal subtask ids — strictly
        richer than the foundation hint.
        """
        if t.assigned_to != pm_agent_id:
            return Envelope.not_authorized(
                message="not assigned to you",
                remediate=(
                    "claim the task or wait for it to be assigned."
                    + await self._own_review_hint(pm_agent_id, task_id)
                ),
                context_briefing=await self._briefing_for(pm_agent_id, task_id),
            )
        if str(t.status) != "awaiting_pm_review":
            # An in_progress cell task must enter the in-path gate first:
            # submit_up opens the cell→root PR and moves it to
            # awaiting_pr_review, then the cell reviewer pr_passes it to
            # awaiting_pm_review where complete merges. Name the verb so the
            # cell PM isn't left guessing (the parallel of the main PM's
            # submit_root steer).
            gate_hint = (
                "open the cell→root PR and enter review first:"
                " submit_up(task_id, notes='...'). After the cell reviewer"
                " pr_passes it, complete merges the cell→root PR."
                if str(t.status) == "in_progress"
                else "this task is not ready for completion."
            )
            return Envelope.invalid_state(
                message=(
                    f"task {task_id} is in {t.status}, expected awaiting_pm_review"
                ),
                remediate=(
                    gate_hint + await self._own_review_hint(pm_agent_id, task_id)
                ),
                context_briefing=await self._briefing_for(pm_agent_id, task_id),
            )
        # Write-then-gate: the PM's merge rationale (notes) becomes the
        # journal:decision the gate requires — no separate note() call needed.
        pm_decision_outcome = await self._ensure_pm_decision(
            pm_agent_id, task_id, notes
        )
        if env := await self._check_complete_gates(
            pm_agent_id, task_id, notes, pm_decision_outcome=pm_decision_outcome
        ):
            return env
        if env := (
            await self._subtasks_not_terminal_envelope(
                pm_agent_id, task_id, context_phrase="completing parent"
            )
            or await self._parent_acs_covered_envelope(
                pm_agent_id, task_id, context_phrase="completing parent"
            )
        ):
            return env
        # A PR-waived task (zero commits relative to its parent — report-only
        # work the verb runner skipped create_pr for) legitimately has no PR
        # to merge; every other task must have one by this point.
        if t.pr_number is None and not markers.is_pr_waived(t):
            return Envelope.invalid_state(
                message="task has no PR; cannot merge",
                remediate=(
                    "this state should not occur post-Phase-1;"
                    " investigate dev's i_am_done path"
                ),
                context_briefing=await self._briefing_for(pm_agent_id, task_id),
            )
        return None

    async def _stamp_pm_findings_verified_or_rejection(
        self, t: Any, *, agent_id: UUID, task_id: UUID, role_str: str, verb: str
    ) -> Envelope | None:
        """PM merge's same-transaction verified-stamp for pm-origin addressed
        findings — parity with ``QAMixin.pass_review`` / ``PRGateMixin.pr_pass``:
        the merge IS the confirmation that a PM's own ``request_changes``
        findings on this root were resolved. Not best-effort — a repo error
        here fails the merge cleanly (returned as a rejection) rather than
        landing a completed task against a stale ledger.
        """
        try:
            # Savepoint: without it, a mid-flush failure poisons the session,
            # so the rejection built below would itself blow up instead of
            # cleanly reaching the PM.
            async with self.task.session.begin_nested():
                await findings_lib.stamp_addressed_verified(
                    self.task.session,
                    t.id,
                    origin="pm",
                    verified_by=slug_for_uuid_or_none(agent_id),
                )
        except Exception as exc:
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message=f"verified-stamp failed: {exc}",
                    remediate="retry complete; if persistent, escalate to the CEO",
                    context_briefing=await self._briefing_for(agent_id, task_id),
                ).with_introspection(task=t, role=role_str),
                agent_id=agent_id,
                task_id=task_id,
                verb=verb,
            )
        return None

    async def _maybe_route_head_branch_to_ceo(
        self,
        pm_agent_id: UUID,
        task_id: UUID,
        t: Any,
        target: str,
    ) -> Envelope | None:
        """Route a CEO-only head-branch leaf merge to CEO approval, or None.

        A leaf whose parent is a branchless coordination root resolves its
        merge target to the project's head env branch (slave/master, via
        resolve_parent_branch's branchless-parent fallback). pr_merge is
        CEO-only for the head branch: no agent may merge into it, so calling
        it raises UnauthorizedError(CEO_ONLY) and the cell PM would
        escalate_up -> main-pm (which also can't merge) and bounce BLOCKED
        until the CEO intervenes by hand. Route such a leaf to
        awaiting_ceo_approval (the CEO merge turn) instead; the closure
        dispatcher skips awaiting_ceo_approval, so this is quiet, not a
        re-spawn loop. Returns None when target is not the head branch (the
        normal cell-PM merge proceeds). Live incidents this closes:
        5612b225/PR 856 (blocked 3x), 5b780794/PR 840.
        """
        if not t.pr_number:
            return None
        head_branch = await self.task.project_default_branch_for_task(t)
        if head_branch is None or target != head_branch:
            return None
        escalated = await self.task.escalate_to_ceo(
            cast("UUID", t.id),
            agent_role="cell_pm",
            notes=(
                f"Leaf PR #{t.pr_number} targets the CEO-only head branch"
                f" '{target}' (parent is a branchless coordination root). The"
                f" cell PM cannot merge into the head branch; routed to the"
                f" CEO for approve-and-merge."
            ),
            actor_agent_id=pm_agent_id,
            allow_subtask_ceo_merge=True,
        )
        if escalated is not None:
            return Envelope.ok(
                status=str(escalated.status),
                task_id=str(task_id),
                next=(
                    "routed to CEO approval (PR targets the CEO-only head"
                    " branch); idle until the CEO approves and merges"
                ),
                context_briefing=await self._briefing_for(pm_agent_id, task_id),
            ).with_introspection(task=escalated, role="cell_pm")
        return Envelope.invalid_state(
            message=(
                f"could not route PR #{t.pr_number} to CEO approval for the"
                f" CEO-only head branch '{target}'"
            ),
            remediate=(
                "the CEO must approve-and-merge this PR into the head branch"
                f" '{target}' manually"
            ),
            context_briefing=await self._briefing_for(pm_agent_id, task_id),
        ).with_introspection(task=t, role="cell_pm")

    async def _handle_ceo_only_merge_refusal(
        self,
        exc: UnauthorizedError,
        pm_agent_id: UUID,
        task_id: UUID,
        t: Any,
        target: str,
    ) -> Envelope:
        """Route a CEO_ONLY pr_merge refusal to CEO approval, or re-raise.

        pr_merge raises CEO_ONLY when the merge target is the project's head
        env branch (reserved for the CEO via approve-&-merge). A leaf whose
        parent is a branchless coordination root resolves its target there
        (resolve_parent_branch's branchless-parent fallback). Route such a
        leaf to awaiting_ceo_approval (the CEO merge turn) instead of
        bouncing the cell PM escalate_up -> main-pm (which also can't merge)
        into a BLOCKED loop. Keyed on the actual refusal, not a
        target==head proxy, so a stub merge that doesn't model CEO_ONLY
        proceeds normally. Any other UnauthorizedError is re-raised unchanged.
        Live incidents this closes: 5612b225/PR 856, 5b780794/PR 840.
        """
        if not (exc.reason and "CEO_ONLY" in exc.reason):
            raise exc
        return await self._maybe_route_head_branch_to_ceo(
            pm_agent_id, task_id, t, target
        ) or Envelope.invalid_state(
            message=(
                f"PR #{t.pr_number} targets the CEO-only head branch"
                f" '{target}' and could not be routed to CEO approval"
            ),
            remediate=(
                "the CEO must approve-and-merge this PR into the head"
                f" branch '{target}' manually"
            ),
            context_briefing=await self._briefing_for(pm_agent_id, task_id),
        ).with_introspection(task=t, role="cell_pm")

    async def cell_pm_complete(
        self, pm_agent_id: UUID, task_id: UUID, notes: str
    ) -> Envelope:
        """Cell PM completes a task — auto-merges leaf PR into parent branch.

        Idempotent: if the PR is already merged to the target on GitHub,
        skip the merge call and proceed straight to the transition. This
        closes the H7 respawn loop where a re-issue after a None-complete
        re-called pr_merge on an already-merged PR (405) and the remediate
        hint told the agent to "re-issue pr_merge" which could never
        succeed.
        """
        t = await self.task.get(task_id)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="cell_pm_complete",
            )
        guard = await self._cell_pm_complete_guard(pm_agent_id, task_id, t, notes)
        if guard is not None:
            guard.with_introspection(task=t, role="cell_pm")
            return await self._emit_rejection(
                guard,
                agent_id=pm_agent_id,
                task_id=task_id,
                verb="cell_pm_complete",
            )
        if stamp_rejection := await self._stamp_pm_findings_verified_or_rejection(
            t,
            agent_id=pm_agent_id,
            task_id=task_id,
            role_str="cell_pm",
            verb="cell_pm_complete",
        ):
            return stamp_rejection
        # Resolve the merge target from the PARENT task's real
        # branch_name. For a leaf this is the cell branch (same team — no
        # change); for a cell task it is the root branch (feature/main_pm/…),
        # which parent_branch_for would have mis-derived as feature/<cellteam>/…
        target = await resolve_parent_branch(t, self.task)
        # H7: idempotent pre-check. If the PR is already merged to the
        # target on GitHub, skip the GitHub merge call — a re-issue after
        # a None-complete would otherwise 405 on the already-merged PR and
        # respawn the PM forever against a remediate hint that can't succeed.
        merge_commit: str | None = None
        if t.pr_number:
            already_merged = await self.git.is_pr_merged_for_task(task_id)
            if not already_merged:
                try:
                    merge_result = await self.git.pr_merge(
                        t.pr_number,
                        target=target,
                        project_id=cast("UUID", t.project_id),
                        actor_agent_id=pm_agent_id,
                    )
                    merge_commit = merge_result.get("merge_commit_sha")
                except MergeConflictError as exc:
                    # A sibling landed overlapping work first, so this PR can't
                    # merge. Resolve it (rebase / close-superseded / escalate)
                    # instead of letting the failure re-block the task and
                    # respawn the PM forever.
                    return await self._resolve_merge_conflict_on_complete(
                        pm_agent_id=pm_agent_id,
                        task_id=task_id,
                        t=t,
                        target=target,
                        notes=notes,
                        exc=exc,
                    )
                except UnauthorizedError as exc:
                    return await self._handle_ceo_only_merge_refusal(
                        exc, pm_agent_id, task_id, t, target
                    )
        return await self._finalize_cell_complete(
            pm_agent_id, task_id, t, notes, merge_commit
        )

    async def _finalize_cell_complete(
        self,
        pm_agent_id: UUID,
        task_id: UUID,
        t: Any,
        notes: str,
        merge_commit: str | None,
    ) -> Envelope:
        """Mark the leaf completed and propagate the completion to its parent."""
        leaf_parent_id = t.parent_task_id
        leaf_team = t.team
        t = await self.task.cell_pm_complete(
            pm_agent_id,
            task_id,
            notes,
            merge_commit=merge_commit,
        )
        # `complete()` returns None when its prerequisites fail — most
        # notably the PR-merged guard (`work_session.pr_status == "merged"`),
        # which is exactly what breaks when the merge recorded against the
        # WRONG task's work session (the cross-repo pr_number collision that
        # `pr_merge`'s project_id scoping now prevents). Fail closed into a
        # clean invalid_state envelope instead of dereferencing None and
        # 500-ing, which left the be-pm thrashing escalate<->blocked.
        if t is None:
            return Envelope.invalid_state(
                message=(
                    "task could not be completed: its PR is not recorded as "
                    "merged against this task's work session, or it has "
                    "incomplete subtasks / an invalid completion status"
                ),
                remediate=(
                    "re-issue pr_merge for this task's PR, then complete; if "
                    "the PR is genuinely merged on GitHub but the work session "
                    "still shows open, escalate to the CEO to reconcile the "
                    "session and complete manually"
                ),
                context_briefing=await self._briefing_for(pm_agent_id, task_id),
            )
        # Now that the leaf is completed, propagate the completion up to the
        # parent task: if the parent's subtasks are all terminal, hand the
        # parent off to the cell_pm for that team so it gets respawned for
        # the next stage (cell-PM PR merge or main-PM hand-off).
        warning: str | None = None
        if leaf_parent_id is not None:
            try:
                await self._maybe_advance_parent_to_pm_review(leaf_parent_id, leaf_team)
            except Exception as exc:
                logger.warning(
                    "cell_pm_complete side-effect failed - leaf completed, "
                    "parent advance did not fire",
                    task_id=str(task_id),
                    parent_task_id=str(leaf_parent_id),
                    error=str(exc),
                )
                warning = (
                    f"Leaf completed but advancing the parent to PM review "
                    f"failed ({exc}). The parent will be re-advanced on the "
                    f"next dispatch tick."
                )
        env = Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=spec_module._INTENT_VERBS["complete"].next_hint(t),
            context_briefing=await self._briefing_for(pm_agent_id, task_id),
        ).with_introspection(task=t, role="cell_pm")
        if warning:
            env.warning = warning
        return env

    async def _resolve_merge_conflict_on_complete(
        self,
        *,
        pm_agent_id: UUID,
        task_id: UUID,
        t: Any,
        target: str,
        notes: str,
        exc: MergeConflictError,
    ) -> Envelope:
        """Resolve a leaf PR that couldn't merge because a sibling landed first.

        Rebase the branch onto the current base and act on the outcome rather
        than failing (which re-blocks the task and respawns the PM forever):

        - ``rebased``     — the branch now integrates cleanly; retry the merge.
        - ``superseded``  — every change is already in the base via the sibling;
          close the dead PR and complete the task without a redundant merge.
        - ``conflicts`` / ``unknown`` — a human must resolve; escalate to the
          CEO (``awaiting_ceo_approval``) so the task leaves the agent loop.
        """
        rebase = await self.git.rebase_pr_for_task(
            t.pr_number,
            project_id=cast("UUID", t.project_id),
            actor_agent_id=pm_agent_id,
        )
        status = rebase.get("status")
        if status == "rebased":
            merge_result = await self.git.pr_merge(
                t.pr_number,
                target=target,
                project_id=cast("UUID", t.project_id),
                actor_agent_id=pm_agent_id,
            )
            return await self._finalize_cell_complete(
                pm_agent_id, task_id, t, notes, merge_result.get("merge_commit_sha")
            )
        if status == "superseded":
            await self.git.close_pull_request(
                t.pr_number,
                comment=(
                    "Closed as superseded: every change on this branch is "
                    "already present in the base via a sibling PR that merged "
                    "first. Completing the task without a redundant merge."
                ),
                # Preserve the superseded branch (audit / reference), matching
                # the orchestrator supersede path. close_pull_request defaults
                # to non-destructive now; explicit here so the two paths agree.
                delete_branch=False,
                actor_agent_id=pm_agent_id,
                project_id=cast("UUID", t.project_id),
            )
            return await self._finalize_cell_complete(
                pm_agent_id, task_id, t, notes, None
            )
        return await self._escalate_merge_conflict_to_ceo(
            pm_agent_id, task_id, t, rebase, exc
        )

    async def _escalate_merge_conflict_to_ceo(
        self,
        pm_agent_id: UUID,
        task_id: UUID,
        t: Any,
        rebase: dict[str, Any],
        exc: MergeConflictError,
    ) -> Envelope:
        """Route an unresolvable PR conflict to the CEO; never loop on it.

        Moves the task to ``awaiting_ceo_approval`` (admin override — the leaf
        has no in-band edge there) so it leaves agent dispatch, and best-effort
        alerts the CEO with the conflicting files.
        """
        from roboco.models.base import TaskStatus

        files = rebase.get("files") or []
        logger.info(
            "merge conflict escalated to CEO",
            task_id=str(task_id),
            conflicting_files=len(files),
            rebase_status=rebase.get("status"),
            merge_error=str(exc),
        )
        await self.task.admin_set_status(
            task_id,
            TaskStatus.AWAITING_CEO_APPROVAL,
            actor_id=pm_agent_id,
            actor_role="cell_pm",
        )
        await self._notify_ceo_merge_conflict(task_id, files, pm_agent_id)
        t = await self.task.get(task_id)
        detail = f" ({len(files)} conflicting file(s))" if files else ""
        return Envelope.ok(
            status=str(t.status),
            task_id=str(task_id),
            next=(
                "the PR has merge conflicts that could not be resolved "
                f"automatically{detail}; escalated to the CEO. A developer can "
                "rebase the branch, or the CEO can close the PR if superseded."
            ),
            context_briefing=await self._briefing_for(pm_agent_id, task_id),
        ).with_introspection(task=t, role="cell_pm")

    async def _notify_ceo_merge_conflict(
        self, task_id: UUID, files: list[str], pm_agent_id: UUID
    ) -> None:
        """Best-effort CEO alert for a wedged merge conflict; never raises."""
        from roboco.services.notification import NotificationService
        from roboco.services.notification_text import agent_display

        try:
            pm_slug = await agent_display(pm_agent_id, self.task.session)
            await NotificationService().send_stuck_agent_notification(
                task_id=str(task_id),
                agent_slug=pm_slug or str(pm_agent_id),
                task_status="awaiting_ceo_approval",
                to_agent="ceo",
            )
        except Exception:
            logger.warning(
                "failed to send CEO merge-conflict notification",
                task_id=str(task_id),
                conflicting_files=len(files),
            )

    async def _maybe_advance_parent_to_pm_review(
        self, parent_task_id: UUID, leaf_team: Any
    ) -> None:
        """Promote a parent task to awaiting_pm_review once all subtasks finish.

        Walks up from the just-completed leaf. If every direct subtask of
        the parent is terminal, set the parent's ``assigned_to`` to the cell
        PM for the parent's team so the orchestrator spawns the right PM
        for the next stage (review/merge/escalate). Status itself is left
        untouched here — the cell PM transitions it via ``complete``.
        """
        parent = await self.task.get(parent_task_id)
        if parent is None:
            return
        all_terminal = await self.task.all_subtasks_terminal(parent_task_id)
        if not all_terminal:
            return
        team = parent.team or leaf_team
        if team is None:
            return
        pm_agent = await self.task.cell_pm_for_team(team)
        if pm_agent is None:
            return
        await self.task.reassign(parent_task_id, pm_agent.id)

    async def _main_pm_complete_guard(
        self, main_pm_agent_id: UUID, root_task_id: UUID, t: Any, notes: str
    ) -> Envelope | None:
        """Return a rejection Envelope if pre-escalation guards fail; else None.

        Tracing gates (journal:decision, journal:reflect, notes>=min) are
        evaluated by ``_check_complete_gates`` which consumes
        ``VERB_REQUIREMENTS["complete"]``. The
        ``_subtasks_not_terminal_envelope`` call is kept inline because
        its remediation enumerates the non-terminal subtask ids — strictly
        richer than the foundation hint.
        """
        if t.assigned_to != main_pm_agent_id:
            return Envelope.not_authorized(
                message="not assigned to you",
                remediate=(
                    "wait for assignment or claim."
                    + await self._own_review_hint(main_pm_agent_id, root_task_id)
                ),
                context_briefing=await self._briefing_for(
                    main_pm_agent_id, root_task_id
                ),
            )
        # A branch-bearing root must pass the in-path PR-review gate first:
        # submit_root opens the root→master PR and moves it in_progress →
        # awaiting_pr_review, then the main reviewer pr_passes it to
        # awaiting_pm_review. So complete accepts only awaiting_pm_review for a
        # branch-bearing root (a Main-PM root-subtask is planning-typed, never
        # code, but it still assembles the cells' merged work into a real PR).
        # A branchless coordination root (product fan-out, no repo/PR) skips the
        # gate, so it may still be walked from in_progress here. The split is
        # branch-keyed, not task_type-keyed.
        root_is_branchless = not bool(t.branch_name)
        allowed_statuses = (
            ("awaiting_pm_review", "in_progress")
            if root_is_branchless
            else ("awaiting_pm_review",)
        )
        if str(t.status) not in allowed_statuses:
            gate_hint = (
                "this task is not ready for main-PM completion."
                if root_is_branchless
                else (
                    "open the root→master PR and enter review first:"
                    " submit_root(task_id, notes='...'). After the main reviewer"
                    " pr_passes it, complete escalates to the CEO."
                )
            )
            return Envelope.invalid_state(
                message=(
                    f"task {root_task_id} is in {t.status}, expected"
                    f" {' or '.join(allowed_statuses)}"
                ),
                remediate=(
                    gate_hint
                    + await self._own_review_hint(main_pm_agent_id, root_task_id)
                ),
                context_briefing=await self._briefing_for(
                    main_pm_agent_id, root_task_id
                ),
            )
        # A MegaTask root-subtask IS parented (the umbrella) yet carries its
        # own project/branch/PR and behaves as a root for git/CEO purposes
        # (is_batch_root_subtask) — only a genuine non-root subtask is refused
        # here; the umbrella's exemption elsewhere (is_batch_umbrella) is a
        # different, non-overlapping shape (batch_id set, no parent).
        if t.parent_task_id is not None and not is_batch_root_subtask(
            batch_id=t.batch_id, parent_task_id=t.parent_task_id
        ):
            return Envelope.invalid_state(
                message=(
                    "main_pm complete only operates on root tasks (no parent_task_id)"
                ),
                remediate=(
                    "cell PM should complete this task; main PM only"
                    " completes root tasks."
                    + await self._own_review_hint(main_pm_agent_id, root_task_id)
                ),
                context_briefing=await self._briefing_for(
                    main_pm_agent_id, root_task_id
                ),
            )
        # Write-then-gate: the Main PM's root-close rationale (notes) becomes
        # the journal:decision the gate requires.
        pm_decision_outcome = await self._ensure_pm_decision(
            main_pm_agent_id, root_task_id, notes
        )
        if env := await self._check_complete_gates(
            main_pm_agent_id,
            root_task_id,
            notes,
            pm_decision_outcome=pm_decision_outcome,
        ):
            return env
        if env := (
            await self._subtasks_not_terminal_envelope(
                main_pm_agent_id, root_task_id, context_phrase="escalating to CEO"
            )
            or await self._parent_acs_covered_envelope(
                main_pm_agent_id, root_task_id, context_phrase="escalating to CEO"
            )
        ):
            return env
        return None

    async def main_pm_complete(
        self, main_pm_agent_id: UUID, root_task_id: UUID, notes: str
    ) -> Envelope:
        """Main PM completes a root task; opens master PR + escalates to CEO."""
        t = await self.task.get(root_task_id)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {root_task_id} not found"),
                agent_id=main_pm_agent_id,
                task_id=root_task_id,
                verb="main_pm_complete",
            )
        guard = await self._main_pm_complete_guard(
            main_pm_agent_id, root_task_id, t, notes
        )
        if guard is not None:
            guard.with_introspection(task=t, role="main_pm")
            return await self._emit_rejection(
                guard,
                agent_id=main_pm_agent_id,
                task_id=root_task_id,
                verb="main_pm_complete",
            )
        if stamp_rejection := await self._stamp_pm_findings_verified_or_rejection(
            t,
            agent_id=main_pm_agent_id,
            task_id=root_task_id,
            role_str="main_pm",
            verb="main_pm_complete",
        ):
            return stamp_rejection

        # A branchless coordination root (product fan-out, no repo/PR) skips the
        # in-path gate: it never went through submit_root / pr_pass, so walk it
        # in_progress → awaiting_pm_review here, exactly as before. A code root
        # reaches this point already in awaiting_pm_review — submit_root opened
        # the root→master PR and the main reviewer pr_passed it — so no PR
        # creation or status walk is needed.
        if not bool(t.branch_name):
            refreshed = await self.task.get(root_task_id)
            if refreshed is not None and str(refreshed.status) == "in_progress":
                advanced = await self.task.submit_pm_review(
                    main_pm_agent_id, root_task_id, notes
                )
                if advanced is None:
                    return await self._emit_rejection(
                        Envelope.invalid_state(
                            message=(
                                "could not move coordination root to"
                                " awaiting_pm_review for CEO escalation"
                            ),
                            remediate=(
                                "ensure all subtasks are terminal, then retry complete"
                            ),
                            context_briefing=await self._briefing_for(
                                main_pm_agent_id, root_task_id
                            ),
                        ).with_introspection(task=refreshed, role="main_pm"),
                        agent_id=main_pm_agent_id,
                        task_id=root_task_id,
                        verb="main_pm_complete",
                    )

        # Use kwargs — service signature is (task_id, agent_role="cell_pm",
        # notes=None). Positional was passing agent_id as task_id and the
        # actual task_id as agent_role. Forward the main PM's UUID so the
        # awaiting_ceo_approval audit row attributes to the specific PM.
        t = await self.task.escalate_to_ceo(
            task_id=root_task_id,
            agent_role="main_pm",
            notes=notes,
            actor_agent_id=main_pm_agent_id,
        )
        # Defense-in-depth: escalate_to_ceo returns None when it refuses (e.g. a
        # transition guard rejects). Surface that as a clean rejection instead of
        # dereferencing None below.
        if t is None:
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message="escalate_to_ceo did not apply",
                    remediate=(
                        "ensure the root is in awaiting_pm_review with all"
                        " subtasks terminal, then retry complete"
                    ),
                    context_briefing=await self._briefing_for(
                        main_pm_agent_id, root_task_id
                    ),
                ),
                agent_id=main_pm_agent_id,
                task_id=root_task_id,
                verb="main_pm_complete",
            )
        # CEO acts via the UI, not as an agent the orchestrator spawns. Clear
        # ``assigned_to`` so no agent gets respawned to chase this task while
        # it sits in awaiting_ceo_approval.
        await self.task.reassign(root_task_id, None)
        return Envelope.ok(
            status=str(t.status),
            task_id=str(root_task_id),
            next=spec_module._INTENT_VERBS["complete"].next_hint(t),
            context_briefing=await self._briefing_for(main_pm_agent_id, root_task_id),
        ).with_introspection(task=t, role="main_pm")

    @staticmethod
    def _is_umbrella_in_progress(t: Any, role_str: str) -> bool:
        """A MegaTask umbrella sits in_progress branchless (no submit_root/pr_pass),
        so the complete spec gate (AWAITING_PM_REVIEW only) would reject it before
        main_pm_complete's branchless-aware guard runs — this lets it fall through
        to main_pm_complete (CEO merges the root PR; no agent touches master)."""
        return (
            role_str == "main_pm"
            and str(t.status) == "in_progress"
            and is_batch_umbrella(batch_id=t.batch_id, parent_task_id=t.parent_task_id)
        )

    async def complete(self, agent_id: UUID, task_id: UUID, notes: str) -> Envelope:
        """Dispatch to cell_pm_complete or main_pm_complete based on agent role.

        Spec gate runs first (role membership + composed ``complete``
        action's source-status constraint, AWAITING_PM_REVIEW only).
        Both rejections (role not in spec._PM_ROLES, status not awaiting_pm_review)
        flow through ``spec.can_invoke_intent`` and surface as the
        spec-supplied rejection_kind. After the gate accepts, the
        verb body owns dispatch — ``complete`` has two divergent
        runtime paths (Cell PM merges leaf into parent branch; Main PM
        opens master PR + escalates to CEO) that can't be expressed as
        a single VerbRunner composition. Each lower-level method keeps
        its own pre-flight guards (``_cell_pm_complete_guard`` /
        ``_main_pm_complete_guard``) — those model journal:decision
        presence, subtasks-terminal, and PR-mergeability checks the
        spec doesn't model yet.
        """
        t = await self.task.get(task_id)
        briefing = await self._briefing_for(agent_id, task_id, task=t)
        if t is None:
            return await self._emit_rejection(
                Envelope.not_found(message=f"task {task_id} not found"),
                agent_id=agent_id,
                task_id=task_id,
                verb="complete",
            )
        agent = await self.task.agent_for(agent_id)
        role_str = str(agent.role) if agent is not None else "developer"
        try:
            role = spec_module.Role(role_str)
        except ValueError:
            return await self._emit_rejection(
                Envelope.not_authorized(
                    message=f"unknown role '{role_str}'",
                    remediate="role is not declared in the lifecycle spec",
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
                agent_id=agent_id,
                task_id=task_id,
                verb="complete",
            )
        actor_slug, agent_team = actor_context_fields(agent)
        spec_ctx = spec_module.Context(
            actor_id=agent_id,
            actor_slug=actor_slug,
            agent_team=agent_team,
            original_developer_slug=_extract_original_developer(t),
        )
        if soup := await self._guard_free_text(
            checks=(("notes", notes, 10),),
            task=t,
            agent_id=agent_id,
            role_str=role_str,
            verb="complete",
        ):
            return soup
        # An in_progress batch umbrella is branchless and skips the complete spec
        # gate (AWAITING_PM_REVIEW only) to fall through to main_pm_complete —
        # see _is_umbrella_in_progress. Role membership is preserved; main_pm_complete
        # re-checks assignment, subtasks-terminal, and the journal:decision gate.
        umbrella_in_progress = self._is_umbrella_in_progress(t, role_str)
        if not umbrella_in_progress:
            decision = spec_module.can_invoke_intent(role, "complete", t, spec_ctx)
            if not decision.allowed:
                return await self._emit_rejection(
                    Envelope.from_decision(
                        decision, briefing=briefing
                    ).with_introspection(task=t, role=role_str),
                    agent_id=agent_id,
                    task_id=task_id,
                    verb="complete",
                )
        # Spec gate passed (or waived for an in_progress batch umbrella) —
        # role is CELL_PM or MAIN_PM. Verb body owns dispatch from here.
        if role_str == "cell_pm":
            return await self.cell_pm_complete(agent_id, task_id, notes)
        # role_str == "main_pm" — spec._PM_ROLES has only these two members.
        return await self.main_pm_complete(agent_id, task_id, notes)
