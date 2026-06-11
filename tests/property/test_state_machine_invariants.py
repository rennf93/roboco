"""State machine invariant checks.

Originally specced as hypothesis-driven, but hypothesis isn't a project
dependency, so the same invariants are asserted via deterministic
exhaustive enumeration plus a bounded random walk. The intent is identical:
produce a structural sweep that catches the audit's identified risks
(orphan states, transitions writing fields outside the lifecycle module,
terminal states being mistakenly listed as escape points).

Invariants checked:
  1. Every state declared in ``VALID_TRANSITIONS`` appears as either a
     source or a target — no entries that nothing transitions into and
     nothing transitions out of.
  2. Every non-terminal state has at least one outgoing transition.
  3. Terminal states (``completed``, ``cancelled``) have no outgoing
     transitions.
  4. ``is_terminal_state`` is consistent with the empty-transition list
     in ``VALID_TRANSITIONS``.
  5. Every state is reachable from the initial state ``backlog`` (BFS).
  6. Bounded random walk from ``backlog``: any sequence of valid
     transitions stays within the declared state set; never crosses into
     undeclared states; terminal states absorb (no further transitions).
"""

from __future__ import annotations

import random

from roboco.enforcement.task_lifecycle import (
    VALID_TRANSITIONS,
    get_valid_transitions,
    is_terminal_state,
)

_INITIAL_STATE = "backlog"
_TERMINAL_STATES = {"completed", "cancelled"}
_DECLARED_STATES = set(VALID_TRANSITIONS.keys())


def test_no_orphan_states() -> None:
    """Invariant 1 — every state is reachable + has an exit if non-terminal."""
    targets: set[str] = set()
    for outgoing in VALID_TRANSITIONS.values():
        targets.update(outgoing)
    targets.add(_INITIAL_STATE)  # initial state has no inbound by convention

    sources = {state for state, outs in VALID_TRANSITIONS.items() if outs}

    # Every declared state must appear as a target or be terminal.
    orphan_targets = _DECLARED_STATES - targets
    assert not orphan_targets, f"states with no inbound transition: {orphan_targets}"

    # Every non-terminal declared state must have outbound transitions.
    orphan_sources = (_DECLARED_STATES - sources) - _TERMINAL_STATES
    assert not orphan_sources, (
        f"non-terminal states with no outbound transitions: {orphan_sources}"
    )


def test_terminal_states_have_no_exits() -> None:
    """Invariant 3 — terminal states must not list any outgoing transitions."""
    for state in _TERMINAL_STATES:
        assert state in VALID_TRANSITIONS, f"terminal state {state} not declared"
        assert VALID_TRANSITIONS[state] == [], (
            f"terminal state {state} declares outgoing transitions: "
            f"{VALID_TRANSITIONS[state]}"
        )


def test_is_terminal_state_consistent_with_transitions() -> None:
    """Invariant 4 — is_terminal_state(s) iff VALID_TRANSITIONS[s] is empty."""
    for state, outgoing in VALID_TRANSITIONS.items():
        assert is_terminal_state(state) == (len(outgoing) == 0), (
            f"is_terminal_state({state!r})={is_terminal_state(state)} "
            f"but outgoing transitions = {outgoing}"
        )


def test_every_state_reachable_from_initial() -> None:
    """Invariant 5 — BFS from backlog covers every declared state."""
    visited: set[str] = set()
    frontier: list[str] = [_INITIAL_STATE]
    while frontier:
        state = frontier.pop()
        if state in visited:
            continue
        visited.add(state)
        for nxt in VALID_TRANSITIONS.get(state, []):
            if nxt not in visited:
                frontier.append(nxt)
    unreachable = _DECLARED_STATES - visited
    assert not unreachable, f"states unreachable from {_INITIAL_STATE!r}: {unreachable}"


def test_random_walks_stay_within_declared_states() -> None:
    """Invariant 6 — bounded random walks from backlog never leave the declared set."""
    rng = random.Random(20260504)
    walks = 100
    max_steps = 50

    for _ in range(walks):
        state = _INITIAL_STATE
        for _ in range(max_steps):
            assert state in _DECLARED_STATES, f"walked into undeclared state {state!r}"
            outs = get_valid_transitions(state)
            if not outs:
                # Terminal — walk stops.
                assert is_terminal_state(state), (
                    f"non-terminal state {state!r} has no transitions"
                )
                break
            state = rng.choice(outs)


def test_no_self_loops() -> None:
    """Bonus invariant — no state may transition to itself."""
    for state, outs in VALID_TRANSITIONS.items():
        assert state not in outs, (
            f"state {state!r} has a self-loop in VALID_TRANSITIONS"
        )
