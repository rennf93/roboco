"""Per-verb circuit breaker — agent_sdk denies after N retries in 60s.

Phase 3 Task 14. The 2026-05-10 smoke run showed `i_am_done` retried 5+
times in 2 minutes (each rejection was a `tracing_gap`; the agent kept
calling). This module verifies the runtime tracker added to
`agent_sdk.server` enforces the per-verb cap from
`foundation.policy.agent_loop.retry_limit_for`.

The tracker is keyed on `(verb, task_id)` and operates over a 60s sliding
window. Tests poke time.monotonic via patch to drive window decay
deterministically — wallclock sleeps would slow the suite.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
import roboco.agent_sdk.server as srv
from fastapi.testclient import TestClient
from roboco.foundation.policy.agent_loop import VERB_RETRY_LIMITS, retry_limit_for
from roboco.services.gateway.envelope import Envelope

if TYPE_CHECKING:
    from collections.abc import Iterator


# Named constants — ruff PLR2004 forbids magic comparisons. Mirror the
# foundation cap for i_am_done so the tests remain readable.
_OK = 200
_I_AM_DONE_CAP = 3  # foundation.VERB_RETRY_LIMITS["i_am_done"]


@pytest.fixture(autouse=True)
def _reset_state() -> Iterator[None]:
    """Wipe the SDK session state before every test.

    Helpers share a module-level `_state` singleton, so leakage between
    tests would silently corrupt counts.
    """
    srv._state.reset()
    yield
    srv._state.reset()


# ---------------------------------------------------------------------------
# Envelope.circuit_open
# ---------------------------------------------------------------------------


def test_envelope_circuit_open_kind() -> None:
    """Envelope.circuit_open is a distinct error kind."""
    env = Envelope.circuit_open(
        verb="i_am_done",
        attempts=4,
        window_seconds=60,
        remediate="call i_am_blocked",
    )
    body = env.as_dict()
    assert body["error"] == "circuit_open"
    assert body["remediate"] == "call i_am_blocked"
    assert body["message"] is not None
    assert "i_am_done" in body["message"]
    assert "60" in body["message"]
    assert "4" in body["message"]


def test_envelope_circuit_open_carries_briefing() -> None:
    """Optional context_briefing is preserved when caller supplies it."""
    env = Envelope.circuit_open(
        verb="i_am_done",
        attempts=4,
        window_seconds=60,
        remediate="x",
        context_briefing={"hint": "blocked"},
    )
    body = env.as_dict()
    assert body["context_briefing"] == {"hint": "blocked"}


def test_envelope_circuit_open_default_briefing_empty() -> None:
    """Omitted context_briefing defaults to an empty dict (not None)."""
    env = Envelope.circuit_open(
        verb="i_am_done", attempts=3, window_seconds=60, remediate="x"
    )
    body = env.as_dict()
    assert body["context_briefing"] == {}


# ---------------------------------------------------------------------------
# Tracker — record + count
# ---------------------------------------------------------------------------


def test_record_attempt_increments_count_for_same_key() -> None:
    """Three records for the same (verb, task_id) yield count == 3."""
    expected = 3
    for _ in range(expected):
        srv._record_verb_attempt("i_am_done", "task-A")
    assert srv._verb_attempt_count("i_am_done", "task-A") == expected


def test_tracker_keys_per_verb_task_pair() -> None:
    """The tracker keys on (verb, task_id), not just verb.

    Records for task-A are independent of records for task-B even when the
    verb is identical. Without this guarantee, an agent juggling two tasks
    would trip the breaker on the wrong one.
    """
    a_count = 3
    b_count = 1
    for _ in range(a_count):
        srv._record_verb_attempt("i_am_done", "task-A")
    for _ in range(b_count):
        srv._record_verb_attempt("i_am_done", "task-B")

    assert srv._verb_attempt_count("i_am_done", "task-A") == a_count
    assert srv._verb_attempt_count("i_am_done", "task-B") == b_count


def test_tracker_keys_per_verb_independent_of_task() -> None:
    """Different verbs on the same task are tracked independently."""
    done_count = 2
    submit_count = 1
    for _ in range(done_count):
        srv._record_verb_attempt("i_am_done", "task-A")
    for _ in range(submit_count):
        srv._record_verb_attempt("submit_up", "task-A")

    assert srv._verb_attempt_count("i_am_done", "task-A") == done_count
    assert srv._verb_attempt_count("submit_up", "task-A") == submit_count


def test_tracker_handles_none_task_id() -> None:
    """Verbs without a task_id collapse to (verb, None) — still tracked."""
    expected = 2
    for _ in range(expected):
        srv._record_verb_attempt("triage", None)
    assert srv._verb_attempt_count("triage", None) == expected


def test_count_is_zero_for_unknown_key() -> None:
    """Querying a never-recorded key returns 0 (no KeyError)."""
    assert srv._verb_attempt_count("never_called", "task-X") == 0


# ---------------------------------------------------------------------------
# Tracker — window decay
# ---------------------------------------------------------------------------


def test_window_drops_entries_older_than_60s() -> None:
    """Attempts older than 60s are pruned from the window on next access.

    Drives time.monotonic via patch so the test runs in <1ms instead of
    waiting on wall-clock.
    """
    base = 1000.0
    initial = 3
    after_jump = 1
    with patch("roboco.agent_sdk.server.time.monotonic") as mock_time:
        mock_time.return_value = base
        for _ in range(initial):
            srv._record_verb_attempt("i_am_done", "task-A")
        assert srv._verb_attempt_count("i_am_done", "task-A") == initial

        # Jump past the 60s window — old attempts must be dropped.
        mock_time.return_value = base + 61.0
        srv._record_verb_attempt("i_am_done", "task-A")
        # The 3 old entries are pruned; only the new one remains.
        assert srv._verb_attempt_count("i_am_done", "task-A") == after_jump


def test_window_keeps_entries_within_60s() -> None:
    """Attempts within the window survive — only ones past 60s drop."""
    base = 1000.0
    expected = 2
    with patch("roboco.agent_sdk.server.time.monotonic") as mock_time:
        mock_time.return_value = base
        srv._record_verb_attempt("i_am_done", "task-A")

        mock_time.return_value = base + 30.0
        srv._record_verb_attempt("i_am_done", "task-A")

        mock_time.return_value = base + 59.0
        # Both entries within the 60s window starting at base.
        assert srv._verb_attempt_count("i_am_done", "task-A") == expected


def test_count_prunes_on_read_without_recording() -> None:
    """_verb_attempt_count prunes the window even when not recording.

    A read-only check should still see a fresh count after time advances —
    callers (e.g. _check_verb_circuit) rely on this.
    """
    base = 1000.0
    with patch("roboco.agent_sdk.server.time.monotonic") as mock_time:
        mock_time.return_value = base
        srv._record_verb_attempt("i_am_done", "task-A")
        srv._record_verb_attempt("i_am_done", "task-A")

        mock_time.return_value = base + 61.0
        # Count must drop without us recording anything new.
        assert srv._verb_attempt_count("i_am_done", "task-A") == 0


# ---------------------------------------------------------------------------
# _check_verb_circuit — gating logic
# ---------------------------------------------------------------------------


def test_check_returns_none_when_under_limit() -> None:
    """Below the cap, _check_verb_circuit returns None — call may proceed."""
    srv._record_verb_attempt("i_am_done", "task-A")
    srv._record_verb_attempt("i_am_done", "task-A")
    # i_am_done cap is 3; we recorded 2.
    assert srv._check_verb_circuit("i_am_done", "task-A") is None


def test_check_returns_envelope_at_or_above_limit() -> None:
    """At the cap, _check_verb_circuit returns a circuit_open envelope dict."""
    limit = retry_limit_for("i_am_done")
    assert limit is not None
    for _ in range(limit):
        srv._record_verb_attempt("i_am_done", "task-A")

    result = srv._check_verb_circuit("i_am_done", "task-A")
    assert result is not None
    assert result["error"] == "circuit_open"
    assert "i_am_done" in result["message"]
    assert result["remediate"] is not None
    assert "i_am_blocked" in result["remediate"]


def test_check_returns_none_for_unlimited_retry_verbs() -> None:
    """give_me_work / triage / evidence never trip the breaker."""
    assert retry_limit_for("give_me_work") is None
    # Even after many recorded attempts, no envelope is returned.
    for _ in range(20):
        srv._record_verb_attempt("give_me_work", None)
    assert srv._check_verb_circuit("give_me_work", None) is None


def test_check_uses_default_cap_for_unknown_verb() -> None:
    """Unknown verbs fall back to BudgetPolicy.verb_retry_max_per_minute."""
    limit = retry_limit_for("not_a_real_verb")
    assert limit is not None  # default cap applies
    for _ in range(limit):
        srv._record_verb_attempt("not_a_real_verb", "task-A")
    assert srv._check_verb_circuit("not_a_real_verb", "task-A") is not None


# ---------------------------------------------------------------------------
# /verb/attempted endpoint
# ---------------------------------------------------------------------------


def test_verb_attempted_endpoint_records_rejection() -> None:
    """POST /verb/attempted with a counted rejection_kind increments the window."""
    client = TestClient(srv.app)
    resp = client.post(
        "/verb/attempted",
        json={
            "verb": "i_am_done",
            "task_id": "task-A",
            "rejection_kind": "tracing_gap",
        },
    )
    assert resp.status_code == _OK
    body = resp.json()
    assert body["verb"] == "i_am_done"
    assert body["task_id"] == "task-A"
    assert body["attempts"] == 1
    assert body["limit"] == retry_limit_for("i_am_done")
    assert body["open"] is False
    assert body["circuit_envelope"] is None


def test_verb_attempted_endpoint_ignores_uncounted_kinds() -> None:
    """A non-rejection (e.g. ok-related) kind doesn't move the counter."""
    client = TestClient(srv.app)
    resp = client.post(
        "/verb/attempted",
        json={
            "verb": "i_am_done",
            "task_id": "task-A",
            "rejection_kind": "ok",  # not in the counted set
        },
    )
    assert resp.status_code == _OK
    body = resp.json()
    assert body["attempts"] == 0
    assert body["open"] is False


def test_verb_attempted_endpoint_opens_circuit_at_threshold() -> None:
    """After `limit` rejections in 60s the endpoint reports open=True with envelope."""
    client = TestClient(srv.app)
    limit = retry_limit_for("i_am_done")
    assert limit is not None

    # Hit the cap.
    last_body: dict[str, object] | None = None
    for _ in range(limit):
        resp = client.post(
            "/verb/attempted",
            json={
                "verb": "i_am_done",
                "task_id": "task-A",
                "rejection_kind": "tracing_gap",
            },
        )
        assert resp.status_code == _OK
        last_body = resp.json()

    assert last_body is not None
    assert last_body["attempts"] == limit
    assert last_body["open"] is True
    env = last_body["circuit_envelope"]
    assert isinstance(env, dict)
    assert env["error"] == "circuit_open"
    assert "i_am_done" in env["message"]


def test_verb_attempted_endpoint_unlimited_verb_never_opens() -> None:
    """give_me_work bypasses the breaker even after many rejections."""
    client = TestClient(srv.app)
    for _ in range(10):
        resp = client.post(
            "/verb/attempted",
            json={
                "verb": "give_me_work",
                "task_id": None,
                "rejection_kind": "tracing_gap",
            },
        )
        assert resp.status_code == _OK

    body = resp.json()
    assert body["limit"] is None
    assert body["open"] is False
    assert body["circuit_envelope"] is None


def test_verb_circuit_status_endpoint_does_not_record() -> None:
    """GET /verb/circuit_status reads state without incrementing."""
    client = TestClient(srv.app)
    # Record one rejection via the POST endpoint.
    client.post(
        "/verb/attempted",
        json={
            "verb": "i_am_done",
            "task_id": "task-A",
            "rejection_kind": "tracing_gap",
        },
    )
    # Now poll status repeatedly — count must stay at 1.
    for _ in range(5):
        resp = client.get(
            "/verb/circuit_status",
            params={"verb": "i_am_done", "task_id": "task-A"},
        )
        assert resp.status_code == _OK
        assert resp.json()["attempts"] == 1


# ---------------------------------------------------------------------------
# Reset semantics
# ---------------------------------------------------------------------------


def test_state_reset_clears_verb_attempts() -> None:
    """_state.reset() wipes the verb tracker (orchestrator calls this on spawn)."""
    expected = 2
    for _ in range(expected):
        srv._record_verb_attempt("i_am_done", "task-A")
    assert srv._verb_attempt_count("i_am_done", "task-A") == expected

    srv._state.reset()
    assert srv._verb_attempt_count("i_am_done", "task-A") == 0


def test_state_reset_via_endpoint_clears_verb_attempts() -> None:
    """POST /budget/reset (called by orchestrator on spawn) clears the tracker too."""
    client = TestClient(srv.app)
    client.post(
        "/verb/attempted",
        json={
            "verb": "i_am_done",
            "task_id": "task-A",
            "rejection_kind": "tracing_gap",
        },
    )
    assert srv._verb_attempt_count("i_am_done", "task-A") == 1

    resp = client.post("/budget/reset")
    assert resp.status_code == _OK
    assert srv._verb_attempt_count("i_am_done", "task-A") == 0


def test_verb_attempts_default_is_empty_deque() -> None:
    """defaultdict yields an empty deque for unseen keys — sanity check."""
    fresh = srv._SessionState()
    assert isinstance(fresh.verb_attempts[("never_seen", None)], deque)
    assert len(fresh.verb_attempts[("never_seen", None)]) == 0


def test_i_am_done_cap_matches_foundation() -> None:
    """The local _I_AM_DONE_CAP constant tracks foundation.

    If foundation changes the cap, this test fails so the test constants
    are updated alongside.
    """
    assert retry_limit_for("i_am_done") == _I_AM_DONE_CAP


def test_qa_handoff_retry_keys_match_mcp_verb_names() -> None:
    """#150: the per-verb retry caps are keyed by the MCP-exposed verb names
    (``pass`` / ``fail`` — what the SDK receives via /verb/attempted, derived
    from the flow URL path), NOT the IntentSpec-internal ``pass_review`` /
    ``fail_review``. A rename on one side without the other would silently drop
    the explicit cap for QA handoffs. Pin that the MCP names are explicit keys
    so the mapping can't drift uncaught.
    """
    assert "pass" in VERB_RETRY_LIMITS
    assert "fail" in VERB_RETRY_LIMITS
    assert retry_limit_for("pass") == VERB_RETRY_LIMITS["pass"]
    assert retry_limit_for("fail") == VERB_RETRY_LIMITS["fail"]
