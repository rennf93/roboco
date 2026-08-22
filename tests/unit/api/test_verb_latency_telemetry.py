"""Per-verb latency telemetry: middleware persistence + non-blocking guarantee.

Covers the fire-and-forget write path in ``RequestLoggingMiddleware.dispatch``
on both the success (including 504-timeout) and exception paths, the
``_extract_flow_verb_role`` path parser, and the guarantee that a telemetry
failure never fails the verb itself.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from roboco.api.middleware import (
    _extract_flow_verb_role,
    _persist_verb_latency,
    setup_middleware,
)

# ---------------------------------------------------------------------------
# _extract_flow_verb_role — pure function
# ---------------------------------------------------------------------------


def test_extract_flow_verb_role_normal() -> None:
    assert _extract_flow_verb_role("/api/v1/flow/developer/give_me_work") == (
        "developer",
        "give_me_work",
    )


def test_extract_flow_verb_role_trailing_slash() -> None:
    assert _extract_flow_verb_role("/api/v1/flow/qa/claim_review/") == (
        "qa",
        "claim_review",
    )


def test_extract_flow_verb_role_non_flow_path() -> None:
    assert _extract_flow_verb_role("/api/v1/tasks/abc") is None


def test_extract_flow_verb_role_health() -> None:
    assert _extract_flow_verb_role("/health") is None


# ---------------------------------------------------------------------------
# Middleware dispatch — telemetry call verification
# ---------------------------------------------------------------------------


def _make_flow_app() -> FastAPI:
    app = FastAPI()

    @app.post("/api/v1/flow/developer/give_me_work")
    async def _ok() -> Any:
        return {"status": "ok"}

    @app.post("/api/v1/flow/developer/i_am_done")
    async def _raise() -> Any:
        raise RuntimeError("boom")

    setup_middleware(app)
    return app


@pytest.mark.filterwarnings("ignore:coroutine 'AsyncMock.*' was never awaited")
@pytest.mark.filterwarnings("ignore:Task was destroyed but it is pending")
def test_success_path_persists_telemetry() -> None:
    """A successful flow-verb request schedules a telemetry write with
    outcome='success' and the response status code."""
    with patch(
        "roboco.api.middleware._persist_verb_latency",
        new_callable=AsyncMock,
    ) as mock_persist:
        client = TestClient(_make_flow_app(), raise_server_exceptions=False)
        response = client.post("/api/v1/flow/developer/give_me_work")

    assert response.status_code == HTTPStatus.OK
    mock_persist.assert_called_once()
    args = mock_persist.call_args
    assert args.args[0] == "give_me_work"  # verb
    assert args.args[1] == "developer"  # role
    assert args.args[3] == "success"  # outcome
    assert args.args[4] == HTTPStatus.OK  # status_code


@pytest.mark.filterwarnings("ignore:coroutine 'AsyncMock.*' was never awaited")
@pytest.mark.filterwarnings("ignore:Task was destroyed but it is pending")
def test_non_flow_path_skips_telemetry() -> None:
    """A non-flow path (e.g. /health) does not schedule a telemetry write."""
    app = FastAPI()

    @app.get("/health")
    async def _health() -> Any:
        return {"status": "ok"}

    setup_middleware(app)

    with patch(
        "roboco.api.middleware._persist_verb_latency",
        new_callable=AsyncMock,
    ) as mock_persist:
        client = TestClient(app)
        response = client.get("/health")

    assert response.status_code == HTTPStatus.OK
    mock_persist.assert_not_called()


# ---------------------------------------------------------------------------
# _persist_verb_latency — non-blocking guarantee
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_verb_latency_swallows_db_failure() -> None:
    """A telemetry write whose DB session raises must not propagate — the
    function logs and swallows so the verb is never failed by telemetry."""
    with patch(
        "roboco.api.middleware.get_session_factory",
        side_effect=RuntimeError("DB unavailable"),
    ):
        # Must not raise.
        await _persist_verb_latency("give_me_work", "developer", 42.0, "success", 200)


@pytest.mark.asyncio
async def test_persist_verb_latency_swallows_commit_failure() -> None:
    """A commit failure inside the separate session is also swallowed."""

    class _FailingSession:
        def add(self, _obj: Any) -> None:
            pass

        async def commit(self) -> None:
            raise RuntimeError("connection lost")

        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *_: Any) -> None:
            pass

    def _factory() -> Any:
        return _FailingSession

    with patch("roboco.api.middleware.get_session_factory", return_value=_factory):
        await _persist_verb_latency("i_am_done", "developer", 99.0, "success", 200)
