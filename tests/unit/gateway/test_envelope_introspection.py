"""Envelope must carry current_state + valid_next_verbs after Task 3."""

from __future__ import annotations

from types import SimpleNamespace

from roboco.services.gateway.envelope import Envelope


def test_envelope_ok_carries_introspection_when_task_supplied() -> None:
    task = SimpleNamespace(id="abc", status="in_progress", task_type="code")
    env = Envelope.ok(
        status="in_progress",
        task_id="abc",
        next="commit(message='...')",
    ).with_introspection(task=task, role="developer")
    body = env.as_dict()
    assert body["current_state"] == "in_progress"
    # `valid_next_verbs` lists lifecycle INTENT verbs; `commit` is a
    # content tool (do_server), not an intent, and is intentionally
    # excluded under the canonical spec.
    assert "open_pr" in body["valid_next_verbs"]
    assert "i_am_done" in body["valid_next_verbs"]


def test_envelope_error_carries_introspection_too() -> None:
    task = SimpleNamespace(id="abc", status="claimed", task_type="code")
    env = Envelope.invalid_state(
        message="task is in claimed, expected awaiting_pm_review",
        remediate="wait for QA + docs to complete",
    ).with_introspection(task=task, role="main_pm")
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert body["current_state"] == "claimed"
    # main_pm on a claimed task DOES see `delegate` (legal in claimed),
    # but the lifecycle-spam pattern verbs we want to suppress on
    # `pending` are absent there. Pin the shape rather than the contents.
    assert isinstance(body["valid_next_verbs"], list)


def test_envelope_without_introspection_omits_fields() -> None:
    """Backwards-compat: callers that don't supply a task get None."""
    env = Envelope.ok(status="ok", task_id=None, next="continue")
    body = env.as_dict()
    assert body["current_state"] is None
    assert body["valid_next_verbs"] is None


def test_with_introspection_returns_self_for_chaining() -> None:
    task = SimpleNamespace(id="x", status="pending", task_type="code")
    env = Envelope.ok(status="pending", task_id="x", next="i_will_work_on")
    result = env.with_introspection(task=task, role="developer")
    assert result is env


def test_with_introspection_handles_unknown_role() -> None:
    """Unknown role -> empty list, not None."""
    task = SimpleNamespace(id="x", status="pending", task_type="code")
    env = Envelope.ok(status="pending", task_id="x", next="???")
    env.with_introspection(task=task, role="space_marine")
    body = env.as_dict()
    assert body["valid_next_verbs"] == []
    assert body["current_state"] == "pending"
