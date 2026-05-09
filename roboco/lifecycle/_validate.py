"""Import-time self-consistency checks for the lifecycle spec.

Every check below must pass before roboco.lifecycle is importable —
a failed validator raises LifecycleSpecError and prevents the
orchestrator container from starting. There is no recovery path:
a bad spec is a build error, not a runtime error.

The checks are exhaustive enough that any drift between predecessor
canon (PERMISSIONS.md, STATUS_TRANSITIONS.md), spec.py tables, and
consumer wiring is caught at import.
"""

from __future__ import annotations

from collections import deque
from itertools import pairwise

from roboco.lifecycle.spec import (
    _ATOMIC_ACTIONS,
    _INTENT_VERBS,
    _STATUS_TRANSITIONS,
    CLAIM_RULES,
    ROLE_TEAM_RULES,
    STATUS_GRAPH,
    Role,
    Status,
)
from roboco.seeds.initial_data import AGENT_UUIDS, DEFAULT_AGENTS


class LifecycleSpecError(RuntimeError):
    """Raised at import time when the spec is internally inconsistent."""


def reachable_from(start: Status) -> set[Status]:
    """All statuses reachable from `start` via STATUS_GRAPH (BFS)."""
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
    """Every Status appears in STATUS_GRAPH (as key)."""
    missing = set(Status) - set(STATUS_GRAPH)
    if missing:
        missing_values = sorted(s.value for s in missing)
        raise LifecycleSpecError(
            f"Statuses missing from STATUS_GRAPH keys: {missing_values}"
        )


def _check_status_reachability() -> None:
    """Every Status except BACKLOG (pre-PENDING stash) reachable from PENDING."""
    reachable = reachable_from(Status.PENDING)
    expected = set(Status) - {Status.BACKLOG}
    unreachable = expected - reachable
    if unreachable:
        unreachable_values = sorted(s.value for s in unreachable)
        raise LifecycleSpecError(
            f"Statuses unreachable from PENDING: {unreachable_values}"
        )


def _check_terminal_exits() -> None:
    """Every non-terminal status exits to either COMPLETED or CANCELLED."""
    terminals = {Status.COMPLETED, Status.CANCELLED}
    non_terminal = set(Status) - terminals
    for s in non_terminal:
        reachable = reachable_from(s)
        if not (reachable & terminals):
            raise LifecycleSpecError(
                f"Status '{s.value}' has no path to COMPLETED or CANCELLED"
            )


def _check_intent_compositions() -> None:
    """Every IntentSpec.composes references existing ActionSpec names.

    Empty composes is allowed: a verb may be purely imperative (e.g.
    `unclaim`, `escalate_up`) and dispatch to the service layer rather
    than composing atomic actions. There are no action references to
    check in that case.
    """
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
    unknown_roles = set(CLAIM_RULES) - set(Role)
    if unknown_roles:
        raise LifecycleSpecError(
            f"CLAIM_RULES has unknown roles: {sorted(r for r in unknown_roles)}"
        )


def _check_claim_rules_status_coverage() -> None:
    """Every Status in CLAIM_RULES.values() exists and is non-terminal."""
    terminals = {Status.COMPLETED, Status.CANCELLED}
    for role, statuses in CLAIM_RULES.items():
        bad = statuses & frozenset(terminals)
        if bad:
            raise LifecycleSpecError(
                f"CLAIM_RULES[{role.value}] includes terminal status:"
                f" {sorted(s.value for s in bad)}"
            )


def _check_self_review_symmetry() -> None:
    """qa_pass / qa_fail / docs_complete must agree on self_review_block."""
    qp = _ATOMIC_ACTIONS["qa_pass"].self_review_block
    qf = _ATOMIC_ACTIONS["qa_fail"].self_review_block
    dc = _ATOMIC_ACTIONS["docs_complete"].self_review_block
    if not (qp == qf == dc):
        raise LifecycleSpecError(
            f"self_review_block asymmetry:"
            f" qa_pass={qp}, qa_fail={qf}, docs_complete={dc}"
        )


def _check_role_team_rules_slugs() -> None:
    """Every slug in ROLE_TEAM_RULES exists in seeded AGENT_UUIDS."""
    unknown = set(ROLE_TEAM_RULES) - set(AGENT_UUIDS)
    if unknown:
        raise LifecycleSpecError(
            f"ROLE_TEAM_RULES references unseeded slugs: {sorted(unknown)}"
        )


def _check_status_transitions_actions() -> None:
    """Every StatusTransition.triggered_by_action references a real ActionSpec."""
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


_VALIDATORS = (
    _check_status_enum_coverage,
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
)


def run_all_validators() -> None:
    """Run every validator. First failure raises; the rest are skipped.

    Called from __init__.py so the spec is validated at import time.
    """
    for validator in _VALIDATORS:
        validator()
