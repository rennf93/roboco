"""flow_server wires gateway rejections into the SDK per-verb circuit breaker.

Phase 3 Task 14 added the SDK-side tracker (POST /verb/attempted,
GET /verb/circuit_status, Envelope.circuit_open). This module verifies
the *missing wiring*: rejection envelopes from the gateway must be
forwarded to the SDK, and when the breaker opens the rejection must be
substituted with the circuit_open envelope before reaching the agent.

The tests stub the orchestrator's httpx.Client (path '/api/v1/flow/...')
and the SDK's httpx.Client (path '/verb/attempted') so the helper can
be exercised end-to-end without a real network. We pick which mock to
return by inspecting the URL the code under test is hitting.
"""

from __future__ import annotations

import importlib
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

if TYPE_CHECKING:
    import types
    from pathlib import Path


_FULL_MANIFEST = {
    "agent_id": "00000000-0000-0000-0000-000000000099",
    "role": "developer",
    "team": "backend",
    "workspace_path": "/tmp/test",
    "flow_tools": [
        "give_me_work",
        "i_will_work_on",
        "open_pr",
        "i_am_done",
        "i_am_blocked",
        "unclaim",
        "resume",
        "i_am_idle",
        "claim_review",
        "pass",
        "fail",
        "claim_doc_task",
        "i_documented",
        "triage",
        "triage_all",
        "unblock",
        "complete",
        "escalate_up",
        "i_will_plan",
        "delegate",
        "submit_up",
        "escalate_to_ceo",
    ],
    "do_tools": ["commit", "note", "say", "dm", "evidence"],
    "read_tools": ["Read", "Glob", "Grep"],
    "write_tools": ["Edit", "Write"],
    "bash_allowed": True,
    "subagent_allowed": False,
    "subagent_model": None,
    "env": {},
}


@pytest.fixture()
def flow_module(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> types.ModuleType:
    """Import flow_server with a tmp manifest + known orchestrator/SDK URLs."""
    manifest_path = tmp_path / "tool-manifest.json"
    manifest_path.write_text(json.dumps(_FULL_MANIFEST))

    monkeypatch.setenv("ROBOCO_AGENT_ID", "00000000-0000-0000-0000-000000000099")
    monkeypatch.setenv("ROBOCO_AGENT_ROLE", "developer")
    monkeypatch.setenv("ROBOCO_ORCHESTRATOR_URL", "http://test-orchestrator:8000")
    monkeypatch.setenv("ROBOCO_SDK_URL", "http://test-sdk:9000")
    monkeypatch.setenv("ROBOCO_TOOL_MANIFEST_PATH", str(manifest_path))

    import roboco.mcp.flow_server as srv

    importlib.reload(srv)
    return srv


def _make_client(
    orchestrator_response: dict[str, Any], sdk_response: dict[str, Any] | None
):
    """Build an httpx.Client mock that dispatches by destination URL.

    Calls hitting ``test-orchestrator`` return ``orchestrator_response``;
    calls hitting ``test-sdk`` return ``sdk_response``. The mock also
    records every URL+body it sees so assertions can verify the SDK was
    (or wasn't) called.
    """
    captured: list[tuple[str, dict[str, Any] | None]] = []

    def _client_factory(*_args: Any, **_kwargs: Any) -> MagicMock:
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)

        def _post(url: str, **kwargs: Any) -> MagicMock:
            captured.append((url, kwargs.get("json")))
            resp = MagicMock()
            if "test-sdk" in url:
                if sdk_response is None:
                    raise AssertionError("SDK called unexpectedly")
                resp.json.return_value = sdk_response
            else:
                resp.json.return_value = orchestrator_response
            return resp

        client.post.side_effect = _post
        return client

    return _client_factory, captured


# ---------------------------------------------------------------------------
# Successful envelope — SDK must NOT be called
# ---------------------------------------------------------------------------


def test_ok_envelope_does_not_touch_sdk(flow_module: types.ModuleType) -> None:
    """An envelope with error=None never POSTs to /verb/attempted."""
    factory, captured = _make_client(
        orchestrator_response={
            "status": "awaiting_qa",
            "task_id": "task-A",
            "next": "wait",
            "error": None,
        },
        sdk_response=None,  # blow up if SDK is called
    )

    with patch("httpx.Client", side_effect=factory):
        result = flow_module.i_am_done("task-A", notes="done")

    assert result["status"] == "awaiting_qa"
    assert result["error"] is None
    # Only the orchestrator was contacted.
    assert all("test-sdk" not in url for url, _ in captured)


# ---------------------------------------------------------------------------
# Rejection envelope — SDK gets the verb + task_id + rejection_kind
# ---------------------------------------------------------------------------


def test_rejection_forwards_to_sdk(flow_module: types.ModuleType) -> None:
    """A tracing_gap envelope triggers POST /verb/attempted with the right keys."""
    factory, captured = _make_client(
        orchestrator_response={
            "error": "tracing_gap",
            "missing": ["pr_number"],
            "remediate": "open the PR",
        },
        sdk_response={
            "verb": "i_am_done",
            "task_id": "task-A",
            "attempts": 1,
            "limit": 3,
            "window_seconds": 60,
            "open": False,
            "circuit_envelope": None,
        },
    )

    with patch("httpx.Client", side_effect=factory):
        result = flow_module.i_am_done("task-A", notes="done")

    # Original rejection survives — breaker is not yet open.
    assert result["error"] == "tracing_gap"
    # Check the SDK saw the right payload.
    sdk_calls = [(url, body) for url, body in captured if "test-sdk" in url]
    assert len(sdk_calls) == 1
    sdk_url, sdk_body = sdk_calls[0]
    assert sdk_url.endswith("/verb/attempted")
    assert sdk_body == {
        "verb": "i_am_done",
        "task_id": "task-A",
        "rejection_kind": "tracing_gap",
    }


@pytest.mark.parametrize(
    "rejection_kind",
    ["tracing_gap", "invalid_state", "not_authorized", "incomplete_input"],
)
def test_all_counted_rejection_kinds_forwarded(
    flow_module: types.ModuleType, rejection_kind: str
) -> None:
    """All four counted error kinds forward to the SDK."""
    factory, captured = _make_client(
        orchestrator_response={
            "error": rejection_kind,
            "message": "no",
            "remediate": "fix",
        },
        sdk_response={
            "verb": "i_am_done",
            "task_id": "task-A",
            "attempts": 1,
            "limit": 3,
            "window_seconds": 60,
            "open": False,
            "circuit_envelope": None,
        },
    )

    with patch("httpx.Client", side_effect=factory):
        flow_module.i_am_done("task-A")

    sdk_calls = [(url, body) for url, body in captured if "test-sdk" in url]
    assert len(sdk_calls) == 1
    _, sdk_body = sdk_calls[0]
    assert sdk_body["rejection_kind"] == rejection_kind


def test_other_error_kinds_do_not_touch_sdk(flow_module: types.ModuleType) -> None:
    """not_found / transport_error aren't counted — SDK is not called."""
    factory, captured = _make_client(
        orchestrator_response={"error": "not_found", "message": "no task"},
        sdk_response=None,
    )

    with patch("httpx.Client", side_effect=factory):
        result = flow_module.i_am_done("task-A")

    assert result["error"] == "not_found"
    assert all("test-sdk" not in url for url, _ in captured)


# ---------------------------------------------------------------------------
# Breaker open — envelope substituted with circuit_open
# ---------------------------------------------------------------------------


def test_breaker_open_substitutes_envelope(flow_module: types.ModuleType) -> None:
    """When SDK reports open=true, the agent gets the circuit_open envelope."""
    circuit_env = {
        "error": "circuit_open",
        "message": ("verb 'i_am_done' rejected 3 times in last 60s — breaker open"),
        "remediate": "call i_am_blocked or i_am_idle",
        "context_briefing": {},
        "status": None,
        "task_id": None,
        "next": None,
        "evidence": {},
        "correlation_id": None,
        "current_state": None,
        "valid_next_verbs": None,
    }
    factory, _ = _make_client(
        orchestrator_response={"error": "tracing_gap", "remediate": "x"},
        sdk_response={
            "verb": "i_am_done",
            "task_id": "task-A",
            "attempts": 3,
            "limit": 3,
            "window_seconds": 60,
            "open": True,
            "circuit_envelope": circuit_env,
        },
    )

    with patch("httpx.Client", side_effect=factory):
        result = flow_module.i_am_done("task-A")

    # The original tracing_gap is GONE — replaced by circuit_open.
    assert result["error"] == "circuit_open"
    assert "i_am_blocked" in result["remediate"]


def test_fourth_rejection_returns_circuit_open(flow_module: types.ModuleType) -> None:
    """Hitting the cap on the Nth call yields circuit_open on that same call.

    The SDK records the attempt FIRST and reports open=true on the
    response that just pushed it over the threshold — so the call that
    trips the breaker is also the call that sees the substitution.
    """
    # On the trip call the SDK reports open=true with the envelope.
    circuit_env = {
        "error": "circuit_open",
        "message": ("verb 'i_am_done' rejected 3 times in last 60s — breaker open"),
        "remediate": "call i_am_blocked(reason='...') or i_am_idle()",
        "context_briefing": {},
        "status": None,
        "task_id": None,
        "next": None,
        "evidence": {},
        "correlation_id": None,
        "current_state": None,
        "valid_next_verbs": None,
    }
    factory, captured = _make_client(
        orchestrator_response={"error": "tracing_gap", "remediate": "x"},
        sdk_response={
            "verb": "i_am_done",
            "task_id": "task-A",
            "attempts": 3,
            "limit": 3,
            "window_seconds": 60,
            "open": True,
            "circuit_envelope": circuit_env,
        },
    )

    with patch("httpx.Client", side_effect=factory):
        result = flow_module.i_am_done("task-A")

    # Substituted envelope shown to the agent.
    assert result["error"] == "circuit_open"
    # Both calls happened: orchestrator (the verb) then SDK (record).
    urls = [url for url, _ in captured]
    assert any("test-orchestrator" in u for u in urls)
    assert any("test-sdk" in u for u in urls)


# ---------------------------------------------------------------------------
# Fail-open behaviour — SDK down must not break the gateway path
# ---------------------------------------------------------------------------


def test_sdk_unreachable_fails_open(flow_module: types.ModuleType) -> None:
    """If the SDK raises, the agent still sees the original rejection."""
    import httpx

    def _client_factory(*_args: Any, **_kwargs: Any) -> MagicMock:
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)

        def _post(url: str, **_kwargs: Any) -> MagicMock:
            if "test-sdk" in url:
                raise httpx.ConnectError("SDK down")
            resp = MagicMock()
            resp.json.return_value = {
                "error": "tracing_gap",
                "missing": ["pr_number"],
                "remediate": "open the PR first",
            }
            return resp

        client.post.side_effect = _post
        return client

    with patch("httpx.Client", side_effect=_client_factory):
        result = flow_module.i_am_done("task-A")

    # SDK was unreachable — original envelope passes through.
    assert result["error"] == "tracing_gap"
    assert result["remediate"] == "open the PR first"


def test_sdk_returns_malformed_json_fails_open(flow_module: types.ModuleType) -> None:
    """If the SDK responds with un-JSON-able body, the original envelope wins."""

    def _client_factory(*_args: Any, **_kwargs: Any) -> MagicMock:
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)

        def _post(url: str, **_kwargs: Any) -> MagicMock:
            resp = MagicMock()
            if "test-sdk" in url:
                resp.json.side_effect = ValueError("not json")
            else:
                resp.json.return_value = {
                    "error": "invalid_state",
                    "message": "wrong state",
                    "remediate": "transition first",
                }
            return resp

        client.post.side_effect = _post
        return client

    with patch("httpx.Client", side_effect=_client_factory):
        result = flow_module.i_am_done("task-A")

    assert result["error"] == "invalid_state"
    assert result["message"] == "wrong state"


# ---------------------------------------------------------------------------
# Verb extraction
# ---------------------------------------------------------------------------


def test_verb_from_path_extracts_last_segment(flow_module: types.ModuleType) -> None:
    """_verb_from_path strips the role prefix and returns the verb token."""
    assert (
        flow_module._verb_from_path("/api/v1/flow/developer/i_am_done") == "i_am_done"
    )
    assert flow_module._verb_from_path("/api/v1/flow/qa/pass") == "pass"
    assert (
        flow_module._verb_from_path("/api/v1/flow/board/escalate_to_ceo")
        == "escalate_to_ceo"
    )


# ---------------------------------------------------------------------------
# task_id pass-through
# ---------------------------------------------------------------------------


def test_task_id_none_is_forwarded_as_null(flow_module: types.ModuleType) -> None:
    """Verbs without a task_id (e.g. give_me_work) post task_id=None to the SDK."""
    factory, captured = _make_client(
        orchestrator_response={"error": "tracing_gap", "remediate": "x"},
        sdk_response={
            "verb": "give_me_work",
            "task_id": None,
            "attempts": 1,
            "limit": None,  # unlimited verb
            "window_seconds": 60,
            "open": False,
            "circuit_envelope": None,
        },
    )

    with patch("httpx.Client", side_effect=factory):
        flow_module.give_me_work()

    sdk_calls = [(url, body) for url, body in captured if "test-sdk" in url]
    assert len(sdk_calls) == 1
    _, sdk_body = sdk_calls[0]
    assert sdk_body["task_id"] is None
    assert sdk_body["verb"] == "give_me_work"
