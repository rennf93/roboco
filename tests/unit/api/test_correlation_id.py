"""End-to-end correlation_id propagation: header -> envelope -> audit row.

Audit F20 surfaced that ``X-Correlation-ID`` was bound to structlog by the
``CorrelationIdMiddleware`` but never travelled past the API boundary:

* The MCP shims (``flow_server`` / ``do_server``) didn't forward it, so
  every MCP -> API hop got a fresh server-generated UUID.
* The Envelope returned to the agent had no slot to carry the id back.
* The ``audit_log`` rows the choreographer writes had no correlation_id
  field, so post-mortem joins across logs and audit trail were impossible.

These tests pin the contract:

1. Envelope holds an optional ``correlation_id`` and round-trips it via
   ``as_dict()``.
2. The v2 flow route reads ``request.state.correlation_id`` (set by
   ``CorrelationIdMiddleware``) and stamps it onto the envelope before
   returning.
3. Both MCP shims attach an ``X-Correlation-ID`` header on every POST,
   mirroring how they attach ``X-Agent-ID`` / ``X-Agent-Role``.
4. The choreographer's ``_emit_rejection`` audit writer pulls the
   correlation_id from the structlog contextvars (where the middleware
   binds it) and stuffs it into the audit row's ``details`` dict.
"""

from __future__ import annotations

import asyncio
import importlib
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient
from roboco.api.deps import get_choreographer
from roboco.api.middleware import CorrelationIdMiddleware
from roboco.api.routes.v2.flow_dev import router as flow_dev_router
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps
from roboco.services.gateway.envelope import Envelope

if TYPE_CHECKING:
    from types import ModuleType

_HTTP_200 = 200
_DEV_AGENT_HEADERS = {
    "X-Agent-ID": "00000000-0000-0000-0000-000000000001",
    "X-Agent-Role": "developer",
}


# --- Envelope ----------------------------------------------------------------


def test_envelope_ok_carries_correlation_id_when_stamped() -> None:
    """correlation_id is set post-construction by the transport layer."""
    env = Envelope.ok(status="idle", next="call i_am_idle()")
    env.correlation_id = "test-id-123"
    assert env.correlation_id == "test-id-123"
    assert env.as_dict()["correlation_id"] == "test-id-123"


def test_envelope_error_carries_correlation_id_when_stamped() -> None:
    env = Envelope.invalid_state(message="bad state", remediate="do X first")
    env.correlation_id = "abc"
    assert env.correlation_id == "abc"
    assert env.as_dict()["correlation_id"] == "abc"


def test_envelope_correlation_id_defaults_to_none() -> None:
    env = Envelope.ok(status="idle", next="call i_am_idle()")
    assert env.correlation_id is None
    # When None we still emit the key so consumers don't have to special-case.
    assert env.as_dict()["correlation_id"] is None


# --- Route -> Envelope wiring ------------------------------------------------


def _build_app() -> tuple[FastAPI, MagicMock]:
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(flow_dev_router)

    mock_chore = MagicMock()
    mock_envelope = Envelope.ok(status="idle", next="...")
    mock_chore.give_me_work = AsyncMock(return_value=mock_envelope)
    app.dependency_overrides[get_choreographer] = lambda: mock_chore
    return app, mock_chore


def test_route_stamps_request_correlation_id_onto_envelope() -> None:
    app, _ = _build_app()
    client = TestClient(app)
    r = client.post(
        "/api/v2/flow/dev/give_me_work",
        json={},
        headers={**_DEV_AGENT_HEADERS, "X-Correlation-ID": "trace-xyz"},
    )
    assert r.status_code == _HTTP_200
    assert r.json()["correlation_id"] == "trace-xyz"
    # Middleware also echoes the header back, so ops can grep for it.
    assert r.headers["X-Correlation-ID"] == "trace-xyz"


def test_route_stamps_generated_correlation_id_when_header_missing() -> None:
    app, _ = _build_app()
    client = TestClient(app)
    r = client.post(
        "/api/v2/flow/dev/give_me_work",
        json={},
        headers=_DEV_AGENT_HEADERS,
    )
    assert r.status_code == _HTTP_200
    body_id = r.json()["correlation_id"]
    header_id = r.headers["X-Correlation-ID"]
    # Middleware generates a UUID and binds it. The route must read the
    # SAME id back from request.state and stamp it onto the envelope.
    assert body_id is not None
    assert body_id == header_id


# --- MCP shims ---------------------------------------------------------------


def _reload_mcp_module(monkeypatch: pytest.MonkeyPatch, dotted: str) -> ModuleType:
    """Set the env vars MCP servers expect at import-time and reload the module.

    Both servers read AGENT_ID / AGENT_ROLE / ORCHESTRATOR_URL once at
    import; we have to re-import after monkey-patching so the test sees
    the patched values. The reload itself is the lazy import — keeping
    importlib at the top-level keeps PLC0415 happy.
    """
    monkeypatch.setenv("ROBOCO_AGENT_ID", "00000000-0000-0000-0000-000000000001")
    monkeypatch.setenv("ROBOCO_AGENT_ROLE", "developer")
    monkeypatch.setenv("ROBOCO_ORCHESTRATOR_URL", "http://test-orchestrator:8000")
    module = importlib.import_module(dotted)
    return importlib.reload(module)


@pytest.fixture
def flow_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    return _reload_mcp_module(monkeypatch, "roboco.mcp.flow_server")


@pytest.fixture
def do_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    return _reload_mcp_module(monkeypatch, "roboco.mcp.do_server")


def _fake_client(payload: dict[str, Any]) -> MagicMock:
    fake_response = MagicMock()
    fake_response.json.return_value = payload
    fake_client = MagicMock()
    fake_client.__enter__ = MagicMock(return_value=fake_client)
    fake_client.__exit__ = MagicMock(return_value=False)
    fake_client.post.return_value = fake_response
    return fake_client


def test_flow_server_attaches_correlation_id_header(
    flow_module: ModuleType,
) -> None:
    fake = _fake_client({"status": "idle"})
    with patch("httpx.Client", return_value=fake):
        flow_module.give_me_work()
    _args, kwargs = fake.post.call_args
    headers = kwargs["headers"]
    assert headers["X-Agent-ID"] == "00000000-0000-0000-0000-000000000001"
    assert headers["X-Agent-Role"] == "developer"
    assert "X-Correlation-ID" in headers
    assert headers["X-Correlation-ID"]  # non-empty


def test_flow_server_generates_unique_correlation_id_per_call(
    flow_module: ModuleType,
) -> None:
    fake = _fake_client({"status": "idle"})
    with patch("httpx.Client", return_value=fake):
        flow_module.give_me_work()
        flow_module.give_me_work()
    first = fake.post.call_args_list[0].kwargs["headers"]["X-Correlation-ID"]
    second = fake.post.call_args_list[1].kwargs["headers"]["X-Correlation-ID"]
    assert first != second


def test_do_server_attaches_correlation_id_header(
    do_module: ModuleType,
) -> None:
    fake = _fake_client({"status": "noted"})
    with patch("httpx.Client", return_value=fake):
        do_module.note("hi")
    _args, kwargs = fake.post.call_args
    headers = kwargs["headers"]
    assert headers["X-Agent-ID"] == "00000000-0000-0000-0000-000000000001"
    assert headers["X-Agent-Role"] == "developer"
    assert "X-Correlation-ID" in headers
    assert headers["X-Correlation-ID"]


# --- Audit-row stash ---------------------------------------------------------


def test_choreographer_emit_rejection_includes_correlation_id_in_details() -> None:
    """Audit row's `details` dict carries correlation_id from contextvars."""
    audit = MagicMock()
    audit.log_event = AsyncMock()
    deps = ChoreographerDeps(
        task=MagicMock(),
        work_session=MagicMock(),
        git=MagicMock(),
        a2a=MagicMock(),
        journal=MagicMock(),
        audit=audit,
        evidence_repo=MagicMock(),
    )
    chore = Choreographer(deps)

    bad_env = Envelope.invalid_state(message="oops", remediate="do X")
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(correlation_id="cid-9")
    try:
        asyncio.run(
            chore._emit_rejection(
                bad_env,
                agent_id=UUID("00000000-0000-0000-0000-000000000001"),
                task_id=None,
                verb="i_will_work_on",
            )
        )
    finally:
        structlog.contextvars.clear_contextvars()

    audit.log_event.assert_awaited_once()
    kwargs = audit.log_event.await_args.kwargs
    assert kwargs["details"]["correlation_id"] == "cid-9"
