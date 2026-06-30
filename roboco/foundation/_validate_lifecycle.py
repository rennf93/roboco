"""Import-time self-consistency checks for the lifecycle spec.

Lives in its own module (separate from ``foundation/_validate.py``) so
``roboco.foundation.__init__`` can import the identity validators
without dragging the lifecycle spec into its import graph.

Every check below must pass before ``roboco.foundation.policy.lifecycle``
is importable — a failed validator raises LifecycleSpecError and prevents
the orchestrator container from starting. There is no recovery path:
a bad spec is a build error, not a runtime error.

Imports of ``roboco.foundation.policy.lifecycle`` are deliberately
deferred to function bodies. The spec module imports this module at the
bottom of its own definition (`_run_all_lifecycle_validators()`); if any
top-level import here referenced the spec module, Python would loop back
into the partially-initialised spec module and the bottom-call's import
of this module's ``run_all_lifecycle_validators`` would fail. The
deferred-import pattern breaks that cycle: this module loads with no
references to the spec, and only resolves them when the validators
actually run.

The per-file PLC0415 exemption in pyproject.toml exists for this exact
reason.
"""

from __future__ import annotations

from collections import deque
from itertools import pairwise
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from roboco.foundation.policy.lifecycle import Status


class LifecycleSpecError(RuntimeError):
    """Raised at import time when the lifecycle spec is internally inconsistent."""


def reachable_from(start: Status) -> set[Status]:
    """All statuses reachable from `start` via STATUS_GRAPH (BFS)."""
    from roboco.foundation.policy.lifecycle import STATUS_GRAPH

    seen: set[Status] = {start}
    queue: deque[Status] = deque([start])
    while queue:
        node = queue.popleft()
        for nxt in STATUS_GRAPH.get(node, frozenset()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def _check_status_enum_coverage() -> None:
    """Every Status is reachable in the transition set, bidirectionally.

    The old check (`set(Status) - set(STATUS_GRAPH)`) was a tautology:
    ``_build_status_graph`` seeds ``{s: set() for s in Status}``, so every
    Status is a key by construction and the check could never raise. It gave
    false assurance while an orphan state (no transition references it) or a
    stray-string target slipped past. This checks the real coverage:

    (a) every non-terminal Status is the SOURCE of at least one transition —
        an orphan state with no outgoing edge is caught;
    (b) every source/target referenced in _STATUS_TRANSITIONS is a real Status
        member — a stray-string target (e.g. a typo or a removed status) is
        caught.
    """
    from roboco.foundation.policy.lifecycle import _STATUS_TRANSITIONS, Status

    all_statuses = set(Status)
    terminals = {Status.COMPLETED, Status.CANCELLED}
    sources = {t.source for t in _STATUS_TRANSITIONS}
    targets = {t.target for t in _STATUS_TRANSITIONS}
    referenced = sources | targets

    orphan_sources = {s for s in all_statuses - terminals if s not in sources}
    stray = referenced - all_statuses

    problems: list[str] = []
    if orphan_sources:
        problems.append(
            "non-terminal statuses with no outgoing transition: "
            + sorted(s.value for s in orphan_sources).__repr__()
        )
    if stray:
        stray_repr = sorted(repr(s) for s in stray)
        problems.append(f"transitions reference non-Status values: {stray_repr}")
    if problems:
        raise LifecycleSpecError("; ".join(problems))


def _check_status_enum_parity() -> None:
    """The spec's Status enum must match models.base.TaskStatus exactly.

    TaskType has long had this guard (test_task_type_enum_matches_models); Status
    did not. The two StrEnums are textually identical today, but the seam was
    unguarded: a status added to the ORM column type (TaskStatus) but not the
    lifecycle map (Status) drifts silently — TaskService writes it to the DB,
    get_valid_transitions returns [] for the orphan, and the task looks terminal
    with no valid exits. Fail the build at import before any test runs.
    """
    from roboco.foundation.policy.lifecycle import Status
    from roboco.models.base import TaskStatus

    spec_values = {s.value for s in Status}
    model_values = {s.value for s in TaskStatus}
    if spec_values != model_values:
        raise LifecycleSpecError(
            f"Status enum drift between lifecycle.spec and models.base: "
            f"{spec_values ^ model_values}"
        )


def _check_status_reachability() -> None:
    """Every Status except BACKLOG (pre-PENDING stash) reachable from PENDING."""
    from roboco.foundation.policy.lifecycle import Status

    reachable = reachable_from(Status.PENDING)
    expected = set(Status) - {Status.BACKLOG}
    unreachable = expected - reachable
    if unreachable:
        unreachable_values = sorted(s.value for s in unreachable)
        raise LifecycleSpecError(
            f"Statuses unreachable from PENDING: {unreachable_values}"
        )


def _check_terminal_exits() -> None:
    """Every non-terminal status reaches COMPLETED and can be CANCELLED.

    The cancel fan-out generates a ``cancel`` edge from every non-terminal
    status to CANCELLED, so the old ``reachable & {COMPLETED, CANCELLED}``
    check was structurally trivial — a status whose sole exit was cancel passed
    the guard with no real forward completion path. Split the check so a
    state-machine hole that traps work behind a cancel-only exit is caught at
    import.
    """
    from roboco.foundation.policy.lifecycle import Status

    terminals = {Status.COMPLETED, Status.CANCELLED}
    non_terminal = set(Status) - terminals
    for s in non_terminal:
        reachable = reachable_from(s)
        if Status.COMPLETED not in reachable:
            raise LifecycleSpecError(f"Status '{s.value}' has no path to COMPLETED")
        if Status.CANCELLED not in reachable:
            raise LifecycleSpecError(f"Status '{s.value}' has no cancel exit")


def _check_intent_compositions() -> None:
    """Every IntentSpec.composes references existing ActionSpec names.

    Empty composes is allowed: a verb may be purely imperative (e.g.
    `unclaim`, `escalate_up`) and dispatch to the service layer rather
    than composing atomic actions. There are no action references to
    check in that case.
    """
    from roboco.foundation.policy.lifecycle import _ATOMIC_ACTIONS, _INTENT_VERBS

    for name, iv in _INTENT_VERBS.items():
        for action_name in iv.composes:
            if action_name not in _ATOMIC_ACTIONS:
                raise LifecycleSpecError(
                    f"Intent '{name}' composes unknown action '{action_name}'"
                )


def _check_intent_chains() -> None:
    """For multi-step intents, the source/target statuses chain.

    For each adjacent pair (act_n, act_{n+1}) in composes, every status
    where act_n is allowed must transition to a status where act_{n+1}
    is allowed.
    """
    from roboco.foundation.policy.lifecycle import _ATOMIC_ACTIONS, _INTENT_VERBS

    for name, iv in _INTENT_VERBS.items():
        for prev, nxt in pairwise(iv.composes):
            prev_spec = _ATOMIC_ACTIONS[prev]
            nxt_spec = _ATOMIC_ACTIONS[nxt]
            if prev_spec.target_status is None:
                continue  # no transition — chaining doesn't apply
            if prev_spec.target_status not in nxt_spec.source_statuses:
                required = sorted(s.value for s in nxt_spec.source_statuses)
                raise LifecycleSpecError(
                    f"Intent '{name}': action '{prev}' targets"
                    f" '{prev_spec.target_status.value}' but next action"
                    f" '{nxt}' requires {required}"
                )


def _check_claim_rules_role_coverage() -> None:
    """Every Role in CLAIM_RULES exists in the Role enum."""
    from roboco.foundation.policy.lifecycle import CLAIM_RULES, Role

    unknown_roles = set(CLAIM_RULES) - set(Role)
    if unknown_roles:
        raise LifecycleSpecError(
            f"CLAIM_RULES has unknown roles: {sorted(r for r in unknown_roles)}"
        )


def _check_claim_rules_status_coverage() -> None:
    """Every Status in CLAIM_RULES.values() exists and is non-terminal."""
    from roboco.foundation.policy.lifecycle import CLAIM_RULES, Status

    terminals = {Status.COMPLETED, Status.CANCELLED}
    for role, statuses in CLAIM_RULES.items():
        bad = statuses & frozenset(terminals)
        if bad:
            raise LifecycleSpecError(
                f"CLAIM_RULES[{role.value}] includes terminal status:"
                f" {sorted(s.value for s in bad)}"
            )


def _check_self_review_symmetry() -> None:
    """Review actions must agree on self_review_block.

    qa_pass / qa_fail / docs_complete and the in-path PR-review gate's
    pr_pass / pr_fail are all reviewer sign-offs — they must all block
    self-review identically so no path lets an author approve their own work.
    """
    from roboco.foundation.policy.lifecycle import _ATOMIC_ACTIONS

    flags = {
        name: _ATOMIC_ACTIONS[name].self_review_block
        for name in ("qa_pass", "qa_fail", "docs_complete", "pr_pass", "pr_fail")
    }
    if len(set(flags.values())) != 1:
        raise LifecycleSpecError(f"self_review_block asymmetry: {flags}")


def _check_role_team_rules_slugs() -> None:
    """Every slug in ROLE_TEAM_RULES exists in seeded AGENT_UUIDS."""
    from roboco.foundation.policy.lifecycle import ROLE_TEAM_RULES
    from roboco.seeds.initial_data import AGENT_UUIDS

    unknown = set(ROLE_TEAM_RULES) - set(AGENT_UUIDS)
    if unknown:
        raise LifecycleSpecError(
            f"ROLE_TEAM_RULES references unseeded slugs: {sorted(unknown)}"
        )


def _check_status_transitions_actions() -> None:
    """Every StatusTransition.triggered_by_action references a real ActionSpec."""
    from roboco.foundation.policy.lifecycle import _ATOMIC_ACTIONS, _STATUS_TRANSITIONS

    for t in _STATUS_TRANSITIONS:
        if t.triggered_by_action not in _ATOMIC_ACTIONS:
            raise LifecycleSpecError(
                f"StatusTransition {t.source.value}→{t.target.value} triggered by"
                f" unknown action '{t.triggered_by_action}'"
            )


def _check_action_target_reachable_from_source() -> None:
    """For every ActionSpec with target_status set, the target must be in
    STATUS_GRAPH[source] for every source in source_statuses.

    Catches the case where an ActionSpec declares a transition the
    state-machine graph doesn't actually support — e.g. action says
    `pending → cancelled` but STATUS_GRAPH[pending] doesn't include
    cancelled. Without this, a misconfigured ActionSpec would silently
    fail at runtime when TaskService.transition() rejects the move.
    """
    from roboco.foundation.policy.lifecycle import _ATOMIC_ACTIONS, STATUS_GRAPH

    for action_name, spec_action in _ATOMIC_ACTIONS.items():
        if spec_action.target_status is None:
            continue
        for source in spec_action.source_statuses:
            if spec_action.target_status not in STATUS_GRAPH.get(source, frozenset()):
                raise LifecycleSpecError(
                    f"Action '{action_name}': transition"
                    f" {source.value}→{spec_action.target_status.value}"
                    f" not in STATUS_GRAPH"
                )


def _check_role_team_rules_team_match() -> None:
    """For each slug in ROLE_TEAM_RULES with a non-None team, that team
    must match the seed agent record's team. None entries (cross-cell
    roles like main-pm, board members, auditor, CEO) are intentionally
    exempt — None means 'skip team-match enforcement for this slug',
    not 'this slug has no team in the org chart'. The two tables encode
    different concepts (enforcement vs descriptive); they only need to
    agree on the cell-bound rows.
    """
    from roboco.foundation.policy.lifecycle import ROLE_TEAM_RULES
    from roboco.seeds.initial_data import DEFAULT_AGENTS

    seed_team: dict[str, str | None] = {}
    for agent in DEFAULT_AGENTS:
        slug = agent.get("slug")
        if slug is None:
            continue
        seed_team[slug] = agent.get("team")
    for slug, declared_team in ROLE_TEAM_RULES.items():
        if declared_team is None:
            continue  # cross-cell exemption — no agreement required
        seed = seed_team.get(slug)
        if seed != declared_team:
            raise LifecycleSpecError(
                f"ROLE_TEAM_RULES[{slug!r}]={declared_team!r} disagrees"
                f" with seed team={seed!r}"
            )


def _check_unmigrated_is_subset() -> None:
    """UNMIGRATED must be a strict subset of _KNOWN_UNMIGRATED_CONSUMERS.

    New entries not in the known set fail import — prevents silently
    extending the known-debt set without documentation.
    """
    from roboco.foundation.policy.lifecycle import (
        _KNOWN_UNMIGRATED_CONSUMERS,
        UNMIGRATED,
    )

    extras = UNMIGRATED - _KNOWN_UNMIGRATED_CONSUMERS
    if extras:
        raise LifecycleSpecError(
            f"UNMIGRATED contains unknown entries: {sorted(extras)}"
        )


_LIFECYCLE_VALIDATORS = (
    _check_status_enum_coverage,
    _check_status_enum_parity,
    _check_status_reachability,
    _check_terminal_exits,
    _check_intent_compositions,
    _check_intent_chains,
    _check_claim_rules_role_coverage,
    _check_claim_rules_status_coverage,
    _check_self_review_symmetry,
    _check_role_team_rules_slugs,
    _check_role_team_rules_team_match,
    _check_status_transitions_actions,
    _check_action_target_reachable_from_source,
    _check_unmigrated_is_subset,
)


def run_all_lifecycle_validators() -> None:
    """Run every lifecycle validator. First failure raises; the rest are skipped.

    Called from ``roboco.foundation.policy.lifecycle`` at module-load
    time so the spec is validated at import.
    """
    for validator in _LIFECYCLE_VALIDATORS:
        validator()
