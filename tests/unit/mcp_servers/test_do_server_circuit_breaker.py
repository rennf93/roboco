"""do_server wires content-tool rejections into the SDK per-verb circuit breaker.

Smoke-6 (2026-05-14): be-pm's main-pm hit `note(scope='decision')` with
`context: null` 8 times in a row, each returning `incomplete_input`, and
the agent kept retrying. The breaker existed on flow_server but not on
do_server — content tools had no safety net.

These tests mirror test_flow_server_circuit_breaker.py: stub httpx.Client
so the orchestrator URL returns the rejection envelope and the SDK URL
returns the breaker state. The do_server's _post must call /verb/attempted
on rejection kinds (tracing_gap, invalid_state, not_authorized,
incomplete_input) and substitute circuit_open when the SDK reports open.
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
    "flow_tools": [],
    "do_tools": [
        "commit",
        "note",
        "say",
        "dm",
        "evidence",
        "progress",
        "notify",
        "open_session",
        "link_session",
        "notify_list",
        "notify_get",
        "notify_ack",
        "channels",
        "pr_update",
    ],
    "read_tools": ["Read", "Glob", "Grep"],
    "write_tools": ["Edit", "Write"],
    "bash_allowed": True,
    "subagent_allowed": False,
    "subagent_model": None,
    "env": {},
}


@pytest.fixture()
def do_module(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> types.ModuleType:
    """Import do_server with a tmp manifest + known orchestrator/SDK URLs."""
    manifest_path = tmp_path / "tool-manifest.json"
    manifest_path.write_text(json.dumps(_FULL_MANIFEST))

    monkeypatch.setenv("ROBOCO_AGENT_ID", "00000000-0000-0000-0000-000000000099")
    monkeypatch.setenv("ROBOCO_AGENT_ROLE", "developer")
    monkeypatch.setenv("ROBOCO_ORCHESTRATOR_URL", "http://test-orchestrator:8000")
    monkeypatch.setenv("ROBOCO_SDK_URL", "http://test-sdk:9000")
    monkeypatch.setenv("ROBOCO_TOOL_MANIFEST_PATH", str(manifest_path))

    import roboco.mcp.do_server as srv

    importlib.reload(srv)
    return srv


def _make_client(
    orchestrator_response: dict[str, Any], sdk_response: dict[str, Any] | None
) -> Any:
    """Build an httpx.Client mock that dispatches by destination URL."""
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


def test_ok_envelope_does_not_touch_sdk(do_module: types.ModuleType) -> None:
    """An envelope with error=None never POSTs to /verb/attempted."""
    factory, captured = _make_client(
        orchestrator_response={
            "status": "noted",
            "task_id": None,
            "next": "continue",
            "error": None,
        },
        sdk_response=None,
    )
    with patch("httpx.Client", side_effect=factory):
        result = do_module.note(text="hi", scope="note")
    assert result["error"] is None
    assert all("test-sdk" not in url for url, _ in captured)


def test_incomplete_input_forwards_to_sdk(do_module: types.ModuleType) -> None:
    """A note rejection (incomplete_input) triggers POST /verb/attempted."""
    factory, captured = _make_client(
        orchestrator_response={
            "error": "incomplete_input",
            "missing": ["context", "chosen", "rationale"],
            "remediate": "re-issue note(scope='decision', ...)",
        },
        sdk_response={
            "verb": "note",
            "task_id": None,
            "attempts": 1,
            "limit": 3,
            "window_seconds": 60,
            "open": False,
            "circuit_envelope": None,
        },
    )
    with patch("httpx.Client", side_effect=factory):
        result = do_module.note(text="...", scope="decision")
    # Original rejection survives — breaker is not yet open.
    assert result["error"] == "incomplete_input"
    sdk_calls = [(url, body) for url, body in captured if "test-sdk" in url]
    assert len(sdk_calls) == 1
    sdk_url, sdk_body = sdk_calls[0]
    assert sdk_url.endswith("/verb/attempted")
    assert sdk_body == {
        "verb": "note",
        "task_id": None,
        "rejection_kind": "incomplete_input",
    }


@pytest.mark.parametrize(
    "rejection_kind",
    ["tracing_gap", "invalid_state", "not_authorized", "incomplete_input"],
)
def test_all_counted_rejection_kinds_forwarded(
    do_module: types.ModuleType, rejection_kind: str
) -> None:
    """All four counted error kinds forward to the SDK from do_server."""
    factory, captured = _make_client(
        orchestrator_response={
            "error": rejection_kind,
            "message": "no",
            "remediate": "fix",
        },
        sdk_response={
            "verb": "note",
            "task_id": None,
            "attempts": 1,
            "limit": 3,
            "window_seconds": 60,
            "open": False,
            "circuit_envelope": None,
        },
    )
    with patch("httpx.Client", side_effect=factory):
        do_module.note(text="x")
    sdk_calls = [(url, body) for url, body in captured if "test-sdk" in url]
    assert len(sdk_calls) == 1
    assert sdk_calls[0][1]["rejection_kind"] == rejection_kind


def test_breaker_open_substitutes_circuit_open(do_module: types.ModuleType) -> None:
    """When SDK reports open=true, return the circuit_envelope to the agent."""
    circuit_env = {
        "error": "circuit_open",
        "message": "verb 'note' rejected too often (3 in 60s)",
        "remediate": "fix the missing fields once, then retry",
        "missing": [],
    }
    factory, _ = _make_client(
        orchestrator_response={
            "error": "incomplete_input",
            "missing": ["context"],
            "remediate": "fill context",
        },
        sdk_response={
            "verb": "note",
            "task_id": None,
            "attempts": 3,
            "limit": 3,
            "window_seconds": 60,
            "open": True,
            "circuit_envelope": circuit_env,
        },
    )
    with patch("httpx.Client", side_effect=factory):
        result = do_module.note(text="x", scope="decision")
    assert result == circuit_env


def test_sdk_unreachable_falls_open(do_module: types.ModuleType) -> None:
    """If SDK loopback dies, return the original rejection — never break the path."""
    import httpx

    def _client_factory(*_args: Any, **_kwargs: Any) -> MagicMock:
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)

        def _post(url: str, **_kw: Any) -> MagicMock:
            if "test-sdk" in url:
                raise httpx.ConnectError("connection refused")
            resp = MagicMock()
            resp.json.return_value = {
                "error": "incomplete_input",
                "missing": ["context"],
                "remediate": "fill context",
            }
            return resp

        client.post.side_effect = _post
        return client

    with patch("httpx.Client", side_effect=_client_factory):
        result = do_module.note(text="x", scope="decision")
    # Original rejection — never circuit_open when the SDK is unreachable.
    assert result["error"] == "incomplete_input"


def test_verb_extracted_from_path(do_module: types.ModuleType) -> None:
    """commit() reports verb='commit', say() reports verb='say' etc."""
    factory, captured = _make_client(
        orchestrator_response={"error": "invalid_state", "message": "no commits"},
        sdk_response={
            "verb": "commit",
            "task_id": None,
            "attempts": 1,
            "limit": 3,
            "window_seconds": 60,
            "open": False,
            "circuit_envelope": None,
        },
    )
    with patch("httpx.Client", side_effect=factory):
        do_module.commit(message="[abc12345] test commit message under 20 chars")
    sdk_body = next(body for url, body in captured if "test-sdk" in url)
    assert sdk_body["verb"] == "commit"


def test_dict_shaped_error_does_not_crash(do_module: types.ModuleType) -> None:
    """A RobocoError.to_dict()-shaped response must not TypeError the breaker.

    Smoke-7: A2AAccessDeniedError escaped to middleware and was rendered as
    {'error': {'code': ..., 'message': ..., 'details': ...}}. The circuit
    breaker's `error in frozenset` check then crashed with
    `TypeError: unhashable type: 'dict'`.

    F068: a dict-shaped `error` is a retry-storm-worthy rejection (the
    orchestrator's exception handlers all surface this shape on 4xx/5xx),
    so the breaker must COUNT it — mapped to a counted kind by the
    classifier — rather than passing it through silently. The original
    dict payload still reaches the agent (the breaker only substitutes
    when open). No TypeError may be raised either way.
    """
    factory, captured = _make_client(
        orchestrator_response={
            "error": {
                "code": "A2A_ACCESS_DENIED",
                "message": "be-qa cannot A2A with qa-all",
                "details": {},
            }
        },
        sdk_response={
            "verb": "dm",
            "task_id": None,
            "attempts": 1,
            "limit": 3,
            "window_seconds": 60,
            "open": False,
            "circuit_envelope": None,
        },
    )
    # No TypeError; the dict-shaped rejection is forwarded to the SDK.
    with patch("httpx.Client", side_effect=factory):
        result = do_module.dm(recipient="qa-all", text="x")
    # Original dict payload still reaches the agent (breaker not open).
    assert isinstance(result["error"], dict)
    assert result["error"]["code"] == "A2A_ACCESS_DENIED"
    # SDK breaker MUST now be called so a storm of these counts.
    sdk_calls = [(url, body) for url, body in captured if "test-sdk" in url]
    assert len(sdk_calls) == 1
    # ACCESS_DENIED maps to the not_authorized counted kind.
    assert sdk_calls[0][1]["rejection_kind"] == "not_authorized"


def test_422_validation_failure_counts_as_incomplete_input(
    do_module: types.ModuleType,
) -> None:
    """F068: a 422 validation-failure body (`{"detail": [...], "body": ...}`,
    no `error` field) must count toward the breaker — a storm of 422s is
    retry-storm-worthy (the agent keeps re-submitting malformed input).
    Mapped to `incomplete_input` (the agent's input was incomplete/invalid).
    """
    factory, captured = _make_client(
        orchestrator_response={
            "detail": [
                {
                    "loc": ["body", "text"],
                    "msg": "field required",
                    "type": "value_error.missing",
                }
            ],
            "body": None,
        },
        sdk_response={
            "verb": "note",
            "task_id": None,
            "attempts": 1,
            "limit": 3,
            "window_seconds": 60,
            "open": False,
            "circuit_envelope": None,
        },
    )
    with patch("httpx.Client", side_effect=factory):
        do_module.note(text="")
    sdk_calls = [(url, body) for url, body in captured if "test-sdk" in url]
    assert len(sdk_calls) == 1
    assert sdk_calls[0][1]["rejection_kind"] == "incomplete_input"


def test_dict_shaped_internal_error_counts_as_invalid_state(
    do_module: types.ModuleType,
) -> None:
    """F068: a 500 INTERNAL_ERROR dict-shaped response (generic_exception_handler)
    must count toward the breaker as `invalid_state` — a storm of 500s is
    retry-storm-worthy and previously bypassed the breaker entirely.
    """
    factory, captured = _make_client(
        orchestrator_response={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "An internal error occurred",
                "details": {"correlation_id": "abc"},
            }
        },
        sdk_response={
            "verb": "commit",
            "task_id": None,
            "attempts": 1,
            "limit": 3,
            "window_seconds": 60,
            "open": False,
            "circuit_envelope": None,
        },
    )
    with patch("httpx.Client", side_effect=factory):
        do_module.commit(message="[abc12345] a valid commit message here")
    sdk_calls = [(url, body) for url, body in captured if "test-sdk" in url]
    assert len(sdk_calls) == 1
    assert sdk_calls[0][1]["rejection_kind"] == "invalid_state"


def test_dict_shaped_invalid_input_counts_as_incomplete_input(
    do_module: types.ModuleType,
) -> None:
    """F068: a dict-shaped INVALID_INPUT (mapped from 422 by http_exception_handler)
    counts as `incomplete_input` — semantically the agent's input was invalid.
    """
    factory, captured = _make_client(
        orchestrator_response={
            "error": {"code": "INVALID_INPUT", "message": "bad payload"}
        },
        sdk_response={
            "verb": "say",
            "task_id": None,
            "attempts": 1,
            "limit": 3,
            "window_seconds": 60,
            "open": False,
            "circuit_envelope": None,
        },
    )
    with patch("httpx.Client", side_effect=factory):
        do_module.say(channel="backend-cell", text="x")
    sdk_calls = [(url, body) for url, body in captured if "test-sdk" in url]
    assert len(sdk_calls) == 1
    assert sdk_calls[0][1]["rejection_kind"] == "incomplete_input"


# ---------------------------------------------------------------------------
# F069 — a manifest-registered content tool whose route is missing must
# return an envelope rejection (not a raw 404 body) so the breaker counts it.
# ---------------------------------------------------------------------------


def test_missing_route_404_returns_envelope_and_counts(
    do_module: types.ModuleType,
) -> None:
    """F069: a 404 from the orchestrator (manifest-registered tool with no
    route) must surface as a proper `invalid_state` Envelope rejection — not
    FastAPI's raw ``{"detail": "Not Found"}`` body — and the breaker must
    count it. Without this, the agent retries the missing tool forever and
    the breaker never trips. Mirrors flow_server's 404 handling.
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
                resp.json.return_value = {
                    "verb": "evidence",
                    "task_id": None,
                    "attempts": 1,
                    "limit": 3,
                    "window_seconds": 60,
                    "open": False,
                    "circuit_envelope": None,
                }
            else:
                # FastAPI's default 404 for a missing route.
                resp.status_code = 404
                resp.json.return_value = {"detail": "Not Found"}
            return resp

        client.post.side_effect = _post
        return client

    with patch("httpx.Client", side_effect=_client_factory):
        result = do_module.evidence(task_id="some-task")
    # Envelope rejection, not the raw 404 body.
    assert result["error"] == "invalid_state"
    assert "remediate" in result
    assert "detail" not in result  # the raw 404 body was not passed through
    # Breaker was notified so a storm of these trips it.
    sdk_calls = [(url, body) for url, body in captured if "test-sdk" in url]
    assert len(sdk_calls) == 1
    _, sdk_body = sdk_calls[0]
    assert sdk_body is not None
    assert sdk_body["rejection_kind"] == "invalid_state"
