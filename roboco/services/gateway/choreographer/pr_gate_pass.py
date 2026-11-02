"""What only ``pr_pass`` runs at the in-path PR-review gate.

Split out of ``pr_gate.py``: the guards a pass must clear (a broken toolchain,
a block-level convention violation, non-green CI on the assembled PR's head
commit), the DevOps infra-review precondition, the same-transaction
verified-stamp for addressed ``pr_gate`` findings, and the advisory
cross-vendor second review. A CodeQL failure outside the task's declared
``intends_to_touch`` scope is not the task's to fix and is dropped, after the
drift guard checks the declared scope against the files the diff touched.
"""

from __future__ import annotations

from fnmatch import fnmatch
from typing import TYPE_CHECKING, Any

import structlog

from roboco.foundation.identity import slug_for_uuid_or_none
from roboco.foundation.policy import lifecycle as spec_module
from roboco.foundation.policy.content import Finding, Severity
from roboco.services.gateway.choreographer import devops_gate as devops_gate_lib
from roboco.services.gateway.choreographer import findings as findings_lib
from roboco.services.gateway.choreographer.collision import _drift
from roboco.services.gateway.choreographer.pr_gate_evidence import PRGateEvidenceMixin
from roboco.services.gateway.choreographer.pr_gate_verdicts import PRGateVerdictMixin
from roboco.services.gateway.choreographer.second_review_gate import (
    insert_second_review_findings,
    run_second_review_for_gate,
)
from roboco.services.gateway.envelope import Envelope

if TYPE_CHECKING:
    from uuid import UUID

    from roboco.models.base import ModelProvider

logger = structlog.get_logger()

# CodeQL scans by LANGUAGE, not by diff — the check name maps to the same
# `paths:` filter that gates that language's workflow (code-ql.yml /
# codeql-js-ts.yml), i.e. the scope CodeQL actually analyzed. A task whose
# declared `intends_to_touch` scope never overlaps that filter can still see
# the check fail on a pre-existing alert elsewhere in the codebase — that
# failure is not this task's to fix. The checks are named `code-ql` and
# `codeql-js-ts`, the names master's ruleset requires; `Analyze (<language>)`
# is what they were called before, still reported by push runs on branches
# cut before the rename.
_CODEQL_PYTHON_SCOPE = (
    "roboco/**",
    "agents/**",
    "alembic/**",
    "scripts/**",
    "tests/**",
    ".github/scripts/**",
    "docker/decisions/**",
    "docker/docker-gate/**",
    "vulture_whitelist.py",
    "pyproject.toml",
)
_CODEQL_JS_TS_SCOPE = (
    "panel/**",
    "motion/**",
    "video-renderer/**",
    "docs-redirects/**",
)
_CODEQL_CHECK_SCOPES: dict[str, tuple[str, ...]] = {
    "code-ql": _CODEQL_PYTHON_SCOPE,
    "codeql-js-ts": _CODEQL_JS_TS_SCOPE,
    "Analyze (python)": _CODEQL_PYTHON_SCOPE,
    "Analyze (javascript-typescript)": _CODEQL_JS_TS_SCOPE,
}


def _codeql_glob_overlap(a: str, b: str) -> bool:
    """Mirrors ``collision._glob_overlaps`` — kept local rather than imported
    to avoid a private-module dependency (the same reasoning ``collision.py``
    itself documents against ``SequencingService._globs_overlap``)."""
    if a == b or fnmatch(a, b) or fnmatch(b, a):
        return True
    return a.startswith(b.rstrip("/") + "/") or b.startswith(a.rstrip("/") + "/")


def _codeql_checks_out_of_scope(
    failing_checks: list[str], intends_to_touch: list[str]
) -> list[str]:
    """Failing check names that are CodeQL checks whose analyzed-language
    scope doesn't overlap the task's declared ``intends_to_touch`` globs.

    Only fires when the task actually declares a scope — an undeclared scope
    can't prove a finding is out of bounds, so every failing check still
    blocks (the pre-existing, conservative behavior).
    """
    if not intends_to_touch:
        return []
    return [
        name
        for name in failing_checks
        if (scope := _CODEQL_CHECK_SCOPES.get(name)) is not None
        and not any(_codeql_glob_overlap(g, s) for g in intends_to_touch for s in scope)
    ]


def _task_intends_to_touch(t: Any) -> list[str]:
    """``t.intends_to_touch``, defensively coerced to ``list[str]``.

    A test double or an untouched field can hand back ``None`` or a
    non-list mock attribute; either way an undeclared scope must resolve to
    "no scope gate" (``[]``), not a crash.
    """
    val = getattr(t, "intends_to_touch", None)
    return list(val) if isinstance(val, list) else []


class PRGatePassMixin(PRGateEvidenceMixin, PRGateVerdictMixin):
    """The guards, stamps and second review that only ``pr_pass`` runs."""

    async def _gate_pr_pass_preflight(
        self,
        reviewer_agent_id: UUID,
        task_id: UUID,
        t: Any,
        role_str: str,
        briefing: dict[str, Any],
    ) -> tuple[Envelope | None, str | None]:
        """pr_pass-only preflight: the block/CI guards, then the same-
        transaction verified-stamp. Returns ``(rejection, ci_note)`` —
        ``rejection`` non-None on any guard/stamp failure (``ci_note`` is
        meaningless then), else ``(None, ci_note)`` to proceed. Split out of
        ``_gate_decision`` to keep its own branching count down.
        """
        blocked, ci_note = await self._pr_pass_blocked(
            reviewer_agent_id, task_id, t, role_str, briefing
        )
        if blocked is not None:
            return blocked, None
        devops_rejection = await self._devops_verdict_precondition(
            reviewer_agent_id, task_id, t, role_str, briefing
        )
        if devops_rejection is not None:
            return devops_rejection, None
        stamp_rejection = await self._stamp_gate_findings_verified_or_rejection(
            t,
            reviewer_agent_id=reviewer_agent_id,
            task_id=task_id,
            role_str=role_str,
            briefing=briefing,
        )
        if stamp_rejection is not None:
            return stamp_rejection, None
        return None, ci_note

    async def _devops_verdict_precondition(
        self,
        reviewer_agent_id: UUID,
        task_id: UUID,
        t: Any,
        role_str: str,
        briefing: dict[str, Any],
    ) -> Envelope | None:
        """pr_pass's DevOps infra-review precondition (the Stage-3 gate).

        When the predicate applies (``infra_gate_applies``: flag on + the
        assembled diff intersects the project's effective infra globs + the
        PR is not devops-authored), pr_pass composes only once a devops pass
        verdict exists for the PR's CURRENT head - a ``passed`` record in
        ``notes_structured["devops_review"]`` whose ``head_sha`` matches (the
        re-arm: new commits expire the verdict). The DevOps caller itself is
        exempt - its own pr_pass IS its verdict, so requiring a prior record
        would wedge it in a circular block. Flag off => returns None before
        any git/conventions work (byte-for-byte today).
        """
        from roboco.config import settings as _settings

        if not _settings.devops_enabled:
            return None
        if role_str == spec_module.Role.DEVOPS.value:
            return None
        if not await self._devops_gate_applies(t):
            return None
        record = devops_gate_lib.devops_verdict_record(t)
        if record is not None:
            head_sha = await self._capture_pr_head_sha(t)
            if devops_gate_lib.verdict_satisfies_head(record, head_sha):
                return None
        return await self._emit_rejection(
            Envelope.invalid_state(
                message=(
                    "this assembled PR touches the project's declared infra "
                    "paths and still needs the DevOps infra review"
                ),
                remediate=(
                    "wait for the DevOps agent (devops-1) to review and record "
                    "its verdict for the current PR head - the dispatcher routes "
                    "it to this gate automatically - then call pr_pass again. "
                    "do NOT pr_fail over the missing verdict; a missing review "
                    "is not a diff defect"
                ),
                context_briefing=briefing,
            ).with_introspection(task=t, role=role_str),
            agent_id=reviewer_agent_id,
            task_id=task_id,
            verb="pr_pass",
        )

    async def _devops_gate_applies(self, t: Any) -> bool:
        """The infra-gate predicate for one gate task, fed by the same diff
        base the gate's evidence uses. Best-effort end to end: a changed-file
        fetch failure yields ``[]`` and the predicate fails open."""
        gate_parent = await self._gate_diff_parent(t)
        changed_files = await self._gate_changed_files(t, gate_parent)
        return await devops_gate_lib.infra_gate_applies(
            self.task, t, changed_files=changed_files
        )

    async def _gate_second_review_diff(self, t: Any) -> str:
        """The assembled PR's diff for the second-review pass, same base as
        the primary reviewer's own diff. Best-effort: a fetch failure yields
        ``""`` — the second pass simply has nothing to review that round
        rather than blocking ``pr_pass``."""
        if not t.branch_name:
            return ""
        gate_parent = await self._gate_diff_parent(t)
        try:
            diff = await self.git.diff(
                branch_name=t.branch_name, preferred_parent=gate_parent
            )
        except Exception as exc:
            logger.warning("second_review_diff_skip", task_id=str(t.id), error=str(exc))
            return ""
        return str(diff)

    async def _authoring_providers(self, t: Any) -> list[ModelProvider]:
        """The distinct providers that authored this assembled task's code.

        An assembled cell/root task has no single contributing dev's agent —
        resolve every CODE-type descendant's assignee via the fleet's
        provider-routing seam (``ModelRoutingService.resolve_for_agent``, the
        same seam ``SecondReviewService.resolve_second_reviewer_for_agent``
        wraps) instead of assuming one provider authored everything. When an
        assembled task has more than one authoring agent on different
        providers, every one of them is returned so the caller excludes the
        full set, not just the first. Falls back to ``[ANTHROPIC]`` (the
        fleet's always-enabled baseline) only when no assignee is resolvable
        at all (e.g. every leaf still unclaimed).
        """
        from roboco.db.tables import AgentTable
        from roboco.models.base import ModelProvider, TaskType
        from roboco.services.llm import ModelRoutingService

        descendants = await self.task.get_all_descendants(t.id)
        agent_ids = {
            d.assigned_to
            for d in descendants
            if d.task_type == TaskType.CODE and d.assigned_to is not None
        }
        routing = ModelRoutingService(self.task.session)
        providers: set[ModelProvider] = set()
        for agent_id in agent_ids:
            agent = await self.task.session.get(AgentTable, agent_id)
            slug = getattr(agent, "slug", None)
            if not slug:
                continue
            route = await routing.resolve_for_agent(slug)
            providers.add(route.provider_type)
        return list(providers) or [ModelProvider.ANTHROPIC]

    async def _run_second_review_pass(
        self,
        t: Any,
        agent: Any,
        role_str: str,
        *,
        runner: Any = None,
    ) -> dict[str, Any] | None:
        """The cross-vendor second-review pass, gated by the sibling
        ``is_high_stakes``/provider-resolution service (flag + risk
        threshold). Returns the envelope-facing evidence payload, or
        ``None`` when the task doesn't qualify — callers must attach
        ``evidence`` only when this returns non-``None``, so the flag-off /
        below-threshold ``pr_pass`` output stays byte-for-byte unchanged.

        The risk-threshold check runs BEFORE fetching the diff — a
        flag-off/below-threshold ``pr_pass`` must not pay for a ``git diff``
        call it will throw away, and must not touch the git service at all
        (the regression the flag-off test pins). A resolver skip (single
        provider enabled fleet-wide) never blocks — it is recorded in the
        evidence payload and ``pr_pass`` proceeds exactly as the existing
        single-review path. A resolved second pass's findings insert into
        the existing ``task_review_findings`` ledger under the dedicated
        ``second_review`` origin (best-effort: an insert failure is logged
        and never blocks the pass already in flight). ``runner`` is the
        injectable second-pass check (see ``second_review_gate.py``) —
        ``None`` in production, overridable in tests.
        """
        from roboco.services.second_review import task_is_high_stakes

        if not task_is_high_stakes(t):
            return None
        authoring_providers = await self._authoring_providers(t)
        diff = await self._gate_second_review_diff(t)
        outcome = await run_second_review_for_gate(
            self.task.session,
            t,
            diff,
            authoring_provider=authoring_providers,
            runner=runner,
        )
        if outcome.findings:
            author_slug = getattr(agent, "slug", None) or role_str
            try:
                async with self.task.session.begin_nested():
                    await insert_second_review_findings(
                        self.task.session,
                        task_id=t.id,
                        round=findings_lib.next_round(t),
                        author_slug=author_slug,
                        findings=list(outcome.findings),
                    )
            except Exception as exc:
                logger.warning(
                    "second_review_findings_insert_skip",
                    task_id=str(t.id),
                    error=str(exc),
                )
        return outcome.as_evidence()

    async def _pr_pass_blocked(
        self,
        reviewer_agent_id: UUID,
        task_id: UUID,
        t: Any,
        role_str: str,
        briefing: dict[str, Any],
    ) -> tuple[Envelope | None, str | None]:
        """Refuse pr_pass on a broken toolchain, a block-level violation, or
        non-green CI on the assembled PR's head commit.

        A reviewer must not PASS an assembled PR whose suite can't run in the
        workspace, that carries unresolved architectural-convention
        violations, or whose CI is red/pending/unscheduled/unresolvable; only
        red CI is a code defect pr_fail should act on — an unresolvable CI
        lookup is a platform blip to retry, never a finding. Returns
        ``(rejection, None)`` to block, or ``(None, ci_note)`` to proceed —
        ``ci_note`` is a non-None evidence stamp only when the CI guard
        passed through a project with no CI configured at all. The
        toolchain/conventions guards are inert when their flag is off; the
        CI guard fails open on an unresolvable gate-level slug/PR number
        (``None`` from ``_resolve_ci_status``) and also passes through —
        with an evidence stamp — when ``get_pr_ci_status`` itself classifies
        a missing project/git_url/token or an unreachable/nonexistent repo
        as ``no_ci_configured``, or when every failing check on a ``failure``
        status is a CodeQL check outside the task's declared
        ``intends_to_touch`` scope (see ``_scope_gate_codeql_failure``).
        """
        from roboco.config import settings as _settings

        # Only the conventions guard consumes the parent — skip the lookup
        # entirely (and its failure surface) while the flag is off.
        parent = (
            await self._gate_diff_parent(t) if _settings.conventions_enabled else None
        )
        guards = (
            lambda: self._toolchain_broken_guard(reviewer_agent_id, t, reviewer=True),
            lambda: self._conventions_guard(
                reviewer_agent_id, t, briefing, preferred_parent=parent
            ),
        )
        for guard in guards:
            rejection = await guard()
            if rejection is not None:
                return (
                    await self._emit_rejection(
                        rejection.with_introspection(task=t, role=role_str),
                        agent_id=reviewer_agent_id,
                        task_id=task_id,
                        verb="pr_pass",
                    ),
                    None,
                )
        return await self._ci_status_guard(
            reviewer_agent_id, task_id, t, role_str, briefing
        )

    async def _stamp_gate_findings_verified_or_rejection(
        self,
        t: Any,
        *,
        reviewer_agent_id: UUID,
        task_id: UUID,
        role_str: str,
        briefing: dict[str, Any],
    ) -> Envelope | None:
        """pr_pass's same-transaction verified-stamp for pr_gate-origin
        addressed findings (parity with QAMixin's pass_review stamp).

        Not best-effort — the ledger's integrity is the point — but a repo
        error must not corrupt the pass: returned as a clean rejection
        (mirrors ``_run_gate_verb``'s runner-exception rejection) BEFORE the
        transition is attempted, so a stamping failure fails pr_pass cleanly
        instead of landing a passed gate against a stale ledger.
        """
        try:
            # Savepoint: without it, a mid-flush failure poisons the session,
            # so the rejection built below would itself blow up instead of
            # cleanly reaching the reviewer.
            async with self.task.session.begin_nested():
                await findings_lib.stamp_addressed_verified(
                    self.task.session,
                    t.id,
                    origin="pr_gate",
                    verified_by=slug_for_uuid_or_none(reviewer_agent_id),
                )
                # The devops_review ledger rows (a devops pr_fail's findings)
                # bind the author exactly like gate findings; verify their
                # addressed rows in the same stamp on the way through. Rows of
                # that origin cannot exist with the devops flag off, so the
                # extra bulk-verify is a no-op then - gated anyway to keep the
                # flag-off stamp call byte-for-byte singular.
                from roboco.config import settings as _settings

                if _settings.devops_enabled:
                    await findings_lib.stamp_addressed_verified(
                        self.task.session,
                        t.id,
                        origin=devops_gate_lib.DEVOPS_REVIEW_ORIGIN,
                        verified_by=slug_for_uuid_or_none(reviewer_agent_id),
                    )
        except Exception as exc:
            return await self._emit_rejection(
                Envelope.invalid_state(
                    message=f"verified-stamp failed: {exc}",
                    remediate=(
                        "retry pr_pass; if persistent, unclaim and notify the CEO"
                    ),
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
                agent_id=reviewer_agent_id,
                task_id=task_id,
                verb="pr_pass",
            )
        return None

    async def _resolve_ci_status(self, task_id: UUID, t: Any) -> dict[str, Any] | None:
        """Best-effort CI-status lookup for the assembled PR's head commit.

        Returns ``None`` on ANY configuration gap or lookup failure (no
        resolvable slug/PR number, a raised exception, or a caller returning
        something other than the documented ``dict[str, Any]`` shape) so
        ``_ci_status_guard`` fails open on every one of them uniformly.
        """
        pr_number = getattr(t, "pr_number", None)
        try:
            slug = await self._project_slug_for(t)
        except Exception:
            logger.exception(
                "ci status guard: slug resolve failed", task_id=str(task_id)
            )
            return None
        if not slug or not pr_number:
            return None
        try:
            status = await self.git.get_pr_ci_status(slug, int(pr_number))
        except Exception:
            logger.exception(
                "ci status guard: get_pr_ci_status raised", task_id=str(task_id)
            )
            return None
        return status if isinstance(status, dict) else None

    async def _ci_status_guard(
        self,
        reviewer_agent_id: UUID,
        task_id: UUID,
        t: Any,
        role_str: str,
        briefing: dict[str, Any],
    ) -> tuple[Envelope | None, str | None]:
        """Refuse pr_pass unless CI on the assembled PR's head commit is green.

        Failing, pending, unscheduled, or unresolvable-via-API CI states all
        block, but only ``failure`` remediates via ``pr_fail`` (a reviewer
        has no ``i_am_blocked``); pending/unscheduled/error are framed as
        retryable (wait and call pr_pass again), never as a defect to route
        back to the dev — a GitHub API lookup error is a platform blip, not
        a finding. A project with no CI configured at all passes through
        cleanly, returning an evidence note so the caller can stamp the
        verdict with why the guard did not block.

        On a ``failure`` state, any failing CodeQL check whose analyzed
        language falls outside the task's declared ``intends_to_touch`` scope
        is scope-gated out first (``_codeql_checks_out_of_scope``) — CodeQL
        scans the whole language, not the diff, so a pre-existing alert on
        code this task never declared it would touch is not this task's to
        fix. If every failing check was scope-gated out, the guard passes
        through with an evidence note (parity with ``no_ci_configured``); if
        some remain, they alone drive the block message.
        """
        status = await self._resolve_ci_status(task_id, t)
        if status is None:
            # A configuration gap or lookup failure — never mistaken for a CI
            # signal, so the guard fails open rather than blocking.
            return None, None
        state = status.get("state")
        if state == "failure":
            status, scope_note = self._scope_gate_codeql_failure(t, status)
            state = status.get("state")
            if scope_note is not None:
                drift_env = await self._scope_relaxation_drift_guard(
                    reviewer_agent_id, task_id, t, role_str, briefing
                )
                if drift_env is not None:
                    return drift_env, None
                return None, scope_note
        if state == "success":
            return None, None
        if state == "no_ci_configured":
            return None, "no CI configured on this project"
        message, remediate = self._ci_status_block_message(state, status)
        return (
            await self._emit_rejection(
                Envelope.invalid_state(
                    message=message,
                    remediate=remediate,
                    context_briefing=briefing,
                ).with_introspection(task=t, role=role_str),
                agent_id=reviewer_agent_id,
                task_id=task_id,
                verb="pr_pass",
            ),
            None,
        )

    @staticmethod
    def _scope_gate_codeql_failure(
        t: Any, status: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        """Drop out-of-scope CodeQL checks from a ``failure`` status.

        Returns ``(status, None)`` unchanged when nothing is scope-gated out
        (no declared scope, or every failing check is either non-CodeQL or
        genuinely in scope) — the caller's existing failure handling applies
        as before. When at least one failing check is scope-gated out:
        - some checks remain failing -> a NEW status dict carrying only the
          remaining ``failing_checks`` (so ``_ci_status_block_message`` names
          only the checks this task can actually act on), ``ci_note=None``.
        - none remain -> ``({"state": "success", ...}, ci_note)`` so the
          caller passes through cleanly with an evidence note, exactly like
          the ``no_ci_configured`` case.
        """
        failing = list(status.get("failing_checks") or [])
        out_of_scope = _codeql_checks_out_of_scope(failing, _task_intends_to_touch(t))
        if not out_of_scope:
            return status, None
        remaining = [c for c in failing if c not in out_of_scope]
        names = ", ".join(out_of_scope)
        if remaining:
            return {**status, "failing_checks": remaining}, None
        return (
            {"state": "success", "head_sha": status.get("head_sha")},
            f"CodeQL finding(s) outside declared scope did not block: {names}",
        )

    async def _scope_relaxation_drift_guard(
        self,
        reviewer_agent_id: UUID,
        task_id: UUID,
        t: Any,
        role_str: str,
        briefing: dict[str, Any],
    ) -> Envelope | None:
        """Verify a CodeQL scope relaxation against the actual diff.

        Called only when ``_scope_gate_codeql_failure`` granted a relaxation
        (dropped at least one out-of-scope CodeQL check). Fetches the PR's real
        touched files and runs ``collision._drift`` directly — not through
        ``build_collision_context`` (which is sibling-conditioned and returns
        ``None`` when no colliding sibling exists). An under-declared scope that
        bought a security-scan relaxation must not pass unverified.

        Returns ``None`` when every touched file falls inside the declared
        globs (or when the file fetch fails — fail-open, matching the
        best-effort posture of ``_gate_changed_files``). Returns an
        ``invalid_state`` envelope naming each out-of-scope file when drift is
        detected, after recording each as a findings-ledger row so Pest Control
        and Sentinel can see scope drift accumulate.
        """
        task_intends = _task_intends_to_touch(t)
        gate_parent = await self._gate_diff_parent(t)
        actual_files = await self._gate_changed_files(t, gate_parent)
        if not actual_files:
            return None
        drifted = _drift(task_intends, actual_files)
        if not drifted:
            return None
        findings = [
            Finding(
                file=f,
                line=None,
                severity=Severity.MAJOR,
                criterion=None,
                expected="file falls inside the task's declared intends_to_touch scope",
                actual=f"{f} is outside every declared glob "
                "— scope relaxation granted on an "
                "under-declared scope",
                fix="Add the file to intends_to_touch, or remove it from the PR",
                evidence=f"CodeQL scope relaxation was granted "
                "because at least one failing CodeQL check was "
                "outside the declared scope, but the diff "
                f"touches {f} which no declared glob covers.",
            )
            for f in drifted
        ]
        author_slug = role_str
        try:
            async with self.task.session.begin_nested():
                await findings_lib.insert_and_render(
                    self.task.session,
                    task_id=task_id,
                    origin="pr_gate",
                    round=findings_lib.next_round(t),
                    author_slug=author_slug,
                    findings=findings,
                )
        except Exception as exc:
            logger.warning(
                "scope_relaxation_drift_findings_skip",
                task_id=str(task_id),
                error=str(exc),
            )
        names = ", ".join(drifted)
        return await self._emit_rejection(
            Envelope.invalid_state(
                message=(
                    f"CodeQL scope relaxation granted but the diff touches files "
                    f"outside the declared intends_to_touch scope: {names}. "
                    f"Add these files to the task's scope or remove them from the PR."
                ),
                remediate=(
                    "pr_fail the PR with the out-of-scope files as findings, or "
                    "ask the developer to widen intends_to_touch / narrow the diff"
                ),
                context_briefing=briefing,
            ).with_introspection(task=t, role=role_str),
            agent_id=reviewer_agent_id,
            task_id=task_id,
            verb="pr_pass",
        )

    @staticmethod
    def _ci_status_block_message(
        state: str | None, status: dict[str, Any]
    ) -> tuple[str, str]:
        """(message, remediate) for a blocking CI state — failure / pending /
        pending_not_scheduled / error (the non-terminal, non-green states).

        pending/unscheduled/error are framed as retryable (wait and call
        pr_pass again), never as a defect to route back to the dev.
        """
        if state == "failure":
            names = (
                ", ".join(status.get("failing_checks") or []) or "one or more checks"
            )
            return (
                f"CI is failing on the assembled PR's head commit — {names}",
                f"call pr_fail(issues=['CI failing: {names}']) so the PR returns "
                "to needs_revision and the dev fixes the failing check(s) — do "
                "NOT pr_pass on red CI",
            )
        if state == "pending":
            return (
                "CI is still running on the assembled PR's head commit",
                "wait for CI to finish and call pr_pass again once it's green "
                "— do NOT pr_pass while checks are still running",
            )
        if state == "pending_not_scheduled":
            return (
                "CI has not started running on the assembled PR's head commit yet",
                "wait for CI to be scheduled and call pr_pass again once it's "
                "green — do NOT pr_pass before any check has run",
            )
        # state == "error" (or an unrecognized value) — a genuine GitHub API
        # lookup failure, never treat this as a property of the PR itself.
        # Live incident: a reviewer converted this transient blip into an
        # unwaivable blocker finding whose own fix text said "no code change
        # required" — a platform hiccup is not a diff defect.
        return (
            "GitHub API error resolving CI status for the assembled PR — "
            "this is a transient lookup failure, not a property of the PR",
            "wait a few minutes and retry pr_pass — do NOT pr_fail over a "
            "CI-status lookup error; a platform blip is not a code finding, "
            "findings are for defects in the diff",
        )
