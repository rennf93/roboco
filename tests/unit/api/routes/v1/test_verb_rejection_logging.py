"""A rejected envelope must leave a server-side trace.

An error envelope rides a 200, so the access log cannot distinguish a verb an
agent could not satisfy from one that worked. Four Board Programs died that
way on 2026-07-25 with no recoverable reason.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from roboco.api.routes.v1._role_dep import envelope_to_response
from roboco.services.gateway.envelope import Envelope
from structlog.testing import capture_logs


def _request(path: str) -> MagicMock:
    request = MagicMock()
    request.url.path = path
    request.state.correlation_id = "cid-1"
    request.headers = {"X-Agent-ID": "agent-7", "X-Agent-Role": "head_marketing"}
    return request


def _captured(env: Envelope, path: str) -> list[Any]:
    """``capture_logs`` rather than a hand-rolled processor swap — it is
    independent of whatever global structlog config the rest of the suite has
    already installed, which made the swap pass alone and fail in-suite."""
    with capture_logs() as entries:
        envelope_to_response(env=env, request=_request(path))
    return [e for e in entries if e.get("event") == "verb rejected"]


def test_rejection_logs_reason_and_remediation() -> None:
    env = Envelope.invalid_state(
        message="finding 0 is missing 'source_url' — an uncited market claim is noise",
        remediate="provide the http(s) source URL finding 0's claim came from",
        context_briefing={},
    )
    entries = _captured(env, "/api/v1/do/propose_market_brief")

    assert len(entries) == 1, entries
    logged = entries[0]
    assert logged["verb"] == "propose_market_brief"
    assert logged["error"]
    assert "source_url" in str(logged["detail"])
    assert "http(s) source URL" in str(logged["remediate"])
    assert logged["agent_id"] == "agent-7"
    assert logged["agent_role"] == "head_marketing"


def test_success_envelope_logs_nothing() -> None:
    env = Envelope.ok(
        status="market_brief_proposed",
        task_id="t-1",
        next="i_am_idle()",
        context_briefing={},
    )
    assert _captured(env, "/api/v1/do/propose_market_brief") == []
