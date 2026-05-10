"""Tier 4 - smoke replay. Pin the 9+2 known-bug shapes from the
2026-05-08 / 2026-05-09 traces.

Each record in `tests/fixtures/2026-05-08-smoke-trace.json` documents
one bug observed in the audit-log trace and pins the post-fix shape:
verb, role, task setup, expected envelope error (often None for
fixed allow-paths, sometimes a specific rejection_kind for fixed
rejection-with-clear-message paths). If a future spec change
re-introduces one of the 11 bugs, the corresponding parametrized
case here fails with a pointer to the bug id.

Replay-kind taxonomy (drives which assertions a record gets):
  * spec_decision           - call spec.can_invoke_intent(...) on a
                              stub task with the fixture's setup;
                              assert Decision matches the expected
                              shape.
  * spec_decision_role_only - same, but the spec gate is role-only
                              (composes=()) so we assert the role
                              gate allows; verb-body guards are
                              tested elsewhere.
  * spec_decision_then_verb_body - spec gate must allow the verb
                              body to RUN so it can surface a
                              verb-specific invalid_state. We
                              assert the spec gate allows; the
                              verb-body fix is documented but not
                              re-asserted here (covered by
                              test_choreographer_pm_extras).
  * spec_introspection      - assert structural properties of
                              spec._INTENT_VERBS or
                              spec.intents_for_role(...).
  * schema_only_skip        - bug enforced at HTTP/Pydantic layer;
                              no spec-level Decision to assert.
                              Skipped with a documented reason.
  * audit_only_skip         - audit-layer fix below the spec.
                              Skipped with a documented reason.
  * behavioral_skip         - Choreographer-level behavioral
                              concern (e.g. idempotent re-entry)
                              the spec intentionally does NOT
                              model. Skipped with a documented
                              reason.

The fixture itself is a SYNTHESIS of the bug list documented in
prior commit messages (504b553, 1e4c7a8, dfbcb3e, a5d358d, 7a2d4e3,
e9d53fb, 5a10ae9, 81ffc16, e51ba30) - the original /tmp/audit-trace.txt
on the NAS was wiped during cleanup before this session. The
synthesis is faithful to the analysis but is NOT a verbatim event
replay; the goal is to pin the BEHAVIORAL SHAPE of each fix so
regressions surface here.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from roboco.lifecycle import spec

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "2026-05-08-smoke-trace.json"

# 9 bugs from the 2026-05-08 trace + 2 from the 2026-05-09 follow-up.
_EXPECTED_BUG_COUNT = 11


def _load_fixture() -> dict[str, Any]:
    return json.loads(_FIXTURE.read_text())


def _bug_records() -> list[dict[str, Any]]:
    return list(_load_fixture()["known_bugs"])


def _id_for(record: dict[str, Any]) -> str:
    return str(record["id"])


def _stub_task(record: dict[str, Any]) -> Any:
    """Build a minimal task stub matching the fixture record's setup.

    Spec preconditions only read a handful of attributes; we expose
    them via SimpleNamespace so the spec can introspect without
    needing a full SQLAlchemy Task.
    """
    ctx = record.get("context", {}) or {}
    actor_id = uuid4()
    other_id = uuid4()
    owns = bool(ctx.get("owns_task")) or bool(ctx.get("actor_is_owner"))
    assigned_to = (
        actor_id if owns else (other_id if ctx.get("task_was_reassigned") else None)
    )
    commits_count = int(ctx.get("commits") or 0)
    return SimpleNamespace(
        id=uuid4(),
        status=record["task_status"],
        task_type=record["task_type"],
        assigned_to=assigned_to,
        plan="some plan" if record["task_status"] == "in_progress" else None,
        commits=[{"sha": f"abc{i}"} for i in range(commits_count)],
        pr_number=ctx.get("pr_number"),
        branch_name="feature/backend/abc",
        parent_task_id=None,
        sequence=0,
        team="backend",
        title="t",
        quick_context=None,
    ), actor_id


def _build_context(record: dict[str, Any], actor_id: Any) -> spec.Context:
    ctx = record.get("context", {}) or {}
    return spec.Context(
        actor_id=actor_id,
        plan=ctx.get("plan"),
    )


# ---------------------------------------------------------------------------
# Top-level fixture-shape sanity
# ---------------------------------------------------------------------------


def test_fixture_loads_and_has_eleven_records() -> None:
    """The fixture must enumerate all 9 + 2 = 11 known bugs."""
    payload = _load_fixture()
    assert payload["schema_version"] == 1
    assert payload["trace_date"] == "2026-05-08"
    assert payload["follow_up_trace_date"] == "2026-05-09"
    records = payload["known_bugs"]
    assert len(records) == _EXPECTED_BUG_COUNT, (
        f"Expected 9 (2026-05-08) + 2 (2026-05-09) = {_EXPECTED_BUG_COUNT} "
        f"bug records; got {len(records)}. If a bug was added or removed, "
        f"update _EXPECTED_BUG_COUNT and document the change in the fixture."
    )
    ids = [r["id"] for r in records]
    assert len(ids) == len(set(ids)), f"duplicate bug ids: {ids}"
    # Every record must declare a replay_kind so the test can dispatch.
    valid_kinds = {
        "spec_decision",
        "spec_decision_role_only",
        "spec_decision_then_verb_body",
        "spec_introspection",
        "schema_only_skip",
        "audit_only_skip",
        "behavioral_skip",
    }
    for r in records:
        assert r["replay_kind"] in valid_kinds, (
            f"bug {r['id']}: unknown replay_kind {r['replay_kind']!r}"
        )


# ---------------------------------------------------------------------------
# Replay - parametrized over every known-bug record
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("record", _bug_records(), ids=_id_for)
def test_known_bug_does_not_recur(record: dict[str, Any]) -> None:
    """For each documented bug, assert the post-fix behavior holds.

    Dispatch on `replay_kind`; skip-records carry a `skip_reason`
    that documents why the bug is not directly assertable at the
    spec layer (the fix lives elsewhere - the schema, the audit
    service, or a verb-body behavioral guard).
    """
    kind = record["replay_kind"]

    if kind in ("schema_only_skip", "audit_only_skip", "behavioral_skip"):
        pytest.skip(
            f"bug {record['id']}: {record['skip_reason']} "
            f"(fix commit: {record['fix_commit']})"
        )

    if kind == "spec_introspection":
        _assert_spec_introspection(record)
        return

    if kind == "spec_decision":
        _assert_spec_decision(record)
        return

    if kind == "spec_decision_role_only":
        _assert_spec_decision_role_only(record)
        return

    if kind == "spec_decision_then_verb_body":
        _assert_spec_decision_allows_verb_body(record)
        return

    pytest.fail(f"bug {record['id']}: unhandled replay_kind {kind!r}")


# ---------------------------------------------------------------------------
# Per-replay-kind assertion helpers
# ---------------------------------------------------------------------------


def _assert_spec_decision(record: dict[str, Any]) -> None:
    """Full spec.can_invoke_intent assertion: allow OR specific rejection."""
    task, actor_id = _stub_task(record)
    ctx = _build_context(record, actor_id)
    role = spec.Role(record["role"])
    decision = spec.can_invoke_intent(role, record["verb"], task, ctx)

    expected_decision = record["expected_post_fix_decision"]

    if expected_decision == "allow":
        assert decision.allowed, (
            f"bug {record['id']} regressed: spec.can_invoke_intent rejected "
            f"role={role.value} verb={record['verb']} status={record['task_status']} "
            f"task_type={record['task_type']}: {decision.rejection_kind} - "
            f"{decision.message}. Fix commit was {record['fix_commit']}; "
            f"spec invariant: {record['spec_invariant']}"
        )
    elif expected_decision == "tracing_gap":
        assert not decision.allowed, (
            f"bug {record['id']} regressed: spec allowed but should reject "
            f"with tracing_gap. Fix commit was {record['fix_commit']}."
        )
        assert decision.rejection_kind == "tracing_gap", (
            f"bug {record['id']} regressed: expected tracing_gap, got "
            f"{decision.rejection_kind!r}. Fix commit: {record['fix_commit']}; "
            f"spec invariant: {record['spec_invariant']}"
        )
        expected_missing = record.get("expected_missing", [])
        for token in expected_missing:
            assert token in decision.missing, (
                f"bug {record['id']} regressed: missing list "
                f"{decision.missing!r} should contain {token!r}. Fix commit: "
                f"{record['fix_commit']}"
            )
    else:
        pytest.fail(
            f"bug {record['id']}: unhandled expected_post_fix_decision "
            f"{expected_decision!r} for replay_kind=spec_decision"
        )


def _assert_spec_decision_role_only(record: dict[str, Any]) -> None:
    """For verbs whose spec gate is role-only (composes=()).

    The spec must allow the role; verb-body guards (e.g.
    current_owner-hinted reassignment rejections) are pinned by
    separate unit tests. The point here is: role authority must
    not regress, otherwise the verb-body guard never gets a chance
    to fire.
    """
    task, actor_id = _stub_task(record)
    ctx = _build_context(record, actor_id)
    role = spec.Role(record["role"])
    decision = spec.can_invoke_intent(role, record["verb"], task, ctx)

    assert decision.allowed, (
        f"bug {record['id']} regressed: spec.can_invoke_intent rejected "
        f"role={role.value} verb={record['verb']} at the role gate; the "
        f"verb-body's current_owner-hinted reassignment rejection can never "
        f"fire if the role gate rejects first. Fix commit: "
        f"{record['fix_commit']}; spec invariant: {record['spec_invariant']}"
    )


def _assert_spec_decision_allows_verb_body(record: dict[str, Any]) -> None:
    """Spec gate must allow so the verb-body can surface its rejection.

    Bug B (cell-pm-vs-task-type) is enforced in the verb body
    (_delegate_static_guards), not the spec. The spec must allow
    main_pm to call delegate from in_progress so the verb body's
    invalid_state on Cell-PM-assigned-code-typed-subtask can fire.
    """
    task, actor_id = _stub_task(record)
    ctx = _build_context(record, actor_id)
    role = spec.Role(record["role"])
    decision = spec.can_invoke_intent(role, record["verb"], task, ctx)

    assert decision.allowed, (
        f"bug {record['id']} regressed: spec.can_invoke_intent rejected "
        f"role={role.value} verb={record['verb']} status={record['task_status']} "
        f"task_type={record['task_type']} at the spec layer; the verb-body's "
        f"Cell-PM-vs-task-type guard can never fire if the spec gate rejects "
        f"first. Fix commit: {record['fix_commit']}; spec invariant: "
        f"{record['spec_invariant']}"
    )


def _assert_spec_introspection(record: dict[str, Any]) -> None:
    """Structural assertions on the spec's verb table.

    Bug 3 / Bug 7: open_pr exists in _INTENT_VERBS and submit_for_qa
    does not. Bug 4: spec.intents_for_role is the single source of
    truth — the verb set is non-empty for every active role and
    every verb a role has access to is declared in the spec.
    """
    bug_id = record["id"]

    if bug_id in (
        "bug-3-silent-partial-submit-for-qa",
        "bug-7-plan-sdk-vs-gate-disagreement",
    ):
        # The rename submit_for_qa -> open_pr must hold.
        assert "open_pr" in spec._INTENT_VERBS, (
            f"bug {bug_id} regressed: open_pr is no longer declared in "
            f"_INTENT_VERBS. Fix commit: {record['fix_commit']}; spec "
            f"invariant: {record['spec_invariant']}"
        )
        assert "submit_for_qa" not in spec._INTENT_VERBS, (
            f"bug {bug_id} regressed: submit_for_qa was re-introduced into "
            f"_INTENT_VERBS. The rename to open_pr must stick. Fix commit: "
            f"{record['fix_commit']}"
        )
        # And the open_pr IntentSpec carries the atomic preconditions
        # so push_branch / create_pr cannot run before commits / no-prior-PR.
        open_pr_spec = spec._INTENT_VERBS["open_pr"]
        precond_keys = {p.key for p in open_pr_spec.extra_preconditions}
        for required in ("owns_task", "commits>=1", "no_prior_pr"):
            assert required in precond_keys, (
                f"bug {bug_id} regressed: open_pr.extra_preconditions is "
                f"missing {required!r} (got {precond_keys}). Fix commit: "
                f"{record['fix_commit']}"
            )
        # Developer's spec-derived verb list contains open_pr (and not
        # submit_for_qa) - the agent-facing surface is uniform.
        dev_verbs = set(spec.intents_for_role(spec.Role.DEVELOPER))
        assert "open_pr" in dev_verbs
        assert "submit_for_qa" not in dev_verbs
        return

    if bug_id == "bug-4-scattered-role-checks-disagreed":
        # Single source of truth: every role active in the org has a
        # non-empty verb list, and every verb is reachable from at
        # least one role. (verb_gates.py has been deleted in commit
        # bdeedd8; if anyone re-introduces a parallel role-x-state
        # table this test won't catch them, but the import-time
        # validators will - this assertion just pins that the spec
        # itself is internally complete.)
        # SYSTEM is a sentinel role (orchestrator-generated rows) — it has
        # no verbs by design. AUDITOR and CEO are excluded because the
        # original bug was about active developer/qa/pm/doc roles.
        active_roles = [
            r
            for r in spec.Role
            if r not in (spec.Role.AUDITOR, spec.Role.CEO, spec.Role.SYSTEM)
        ]
        for r in active_roles:
            verbs = spec.intents_for_role(r)
            assert verbs, (
                f"bug {bug_id} regressed: role {r.value} has no verbs in "
                f"the spec. Fix commit: {record['fix_commit']}"
            )
        all_assigned_verbs: set[str] = set()
        for r in spec.Role:
            all_assigned_verbs.update(spec.intents_for_role(r))
        for verb_name in spec._INTENT_VERBS:
            assert verb_name in all_assigned_verbs, (
                f"bug {bug_id} regressed: verb {verb_name!r} is declared in "
                f"_INTENT_VERBS but not reachable from any role. Fix commit: "
                f"{record['fix_commit']}"
            )
        return

    pytest.fail(
        f"bug {bug_id}: unhandled spec_introspection record. Add a branch "
        f"to _assert_spec_introspection."
    )
