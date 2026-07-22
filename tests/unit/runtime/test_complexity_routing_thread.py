"""Task complexity threads into `_resolve_agent_route` -> `resolve_for_agent`.

Cost-tiered routing (roboco/services/llm.py) reads a task's
`estimated_complexity` to try a compound ROLE(":"complexity) row before
falling to the plain ROLE row. The orchestrator owns the one indexed Task
lookup and threads the lowercase complexity string through. This is the pure
wiring test (`_resolve_agent_route` -> `resolve_for_agent`); the precedence
logic itself is covered in tests/integration/test_llm_routing.py.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import roboco.db.base as db_base
import roboco.services.llm as llm_module
from roboco.models.base import Complexity, ModelProvider
from roboco.runtime.orchestrator import AgentOrchestrator
from roboco.services.llm import AgentRoute

# Sentinel route the mocked resolve_for_agent returns — a real AgentRoute
# instance (not a bare string) so `result is _SENTINEL_ROUTE` type-checks
# cleanly against `_resolve_agent_route`'s declared AgentRoute return type.
_SENTINEL_ROUTE = AgentRoute(
    provider_id=None,
    provider_type=ModelProvider.ANTHROPIC,
    base_url=None,
    auth_token=None,
    model_name="sentinel",
)


class _ScalarResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _FakeSession:
    """Minimal async-context-manager session returning a fixed complexity."""

    def __init__(self, complexity_value: Any) -> None:
        self._complexity_value = complexity_value

    def __call__(self) -> _FakeSession:
        return self

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, _stmt: Any) -> _ScalarResult:
        return _ScalarResult(self._complexity_value)


class _BoomSession(_FakeSession):
    """A session whose `execute` always raises — models a task-lookup failure
    (bad/unresolvable task id) distinct from a genuine DB/session outage."""

    async def execute(self, _stmt: Any) -> _ScalarResult:
        raise RuntimeError("bad task id")


def _orch() -> AgentOrchestrator:
    # __new__ + skip __init__: avoid all constructor I/O — this method is pure
    # w.r.t. instance state (it only touches module-level imports + args).
    return AgentOrchestrator.__new__(AgentOrchestrator)


def _wire(monkeypatch: pytest.MonkeyPatch, fake_session: Any) -> AsyncMock:
    """Patch get_session_factory + get_model_routing_service; return the
    resolve_for_agent mock so the test can assert on its call."""
    monkeypatch.setattr(
        db_base,
        "get_session_factory",
        lambda: MagicMock(return_value=fake_session),
    )
    resolve_mock = AsyncMock(return_value=_SENTINEL_ROUTE)
    fake_router = MagicMock(resolve_for_agent=resolve_mock)
    monkeypatch.setattr(
        llm_module, "get_model_routing_service", lambda _db: fake_router
    )
    return resolve_mock


@pytest.mark.asyncio
async def test_task_with_high_complexity_threads_lowercase_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A task with estimated_complexity=HIGH resolves the compound
    'role:high' row — i.e. resolve_for_agent is called with complexity='high'
    (lowercased from the Complexity enum's value)."""
    resolve_mock = _wire(monkeypatch, _FakeSession(Complexity.HIGH))

    orch = _orch()
    result = await orch._resolve_agent_route("be-dev-1", "task-123")

    assert result is _SENTINEL_ROUTE
    resolve_mock.assert_awaited_once_with("be-dev-1", complexity="high")


@pytest.mark.asyncio
async def test_task_with_low_complexity_threads_lowercase_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolve_mock = _wire(monkeypatch, _FakeSession(Complexity.LOW))

    orch = _orch()
    await orch._resolve_agent_route("be-dev-1", "task-456")

    resolve_mock.assert_awaited_once_with("be-dev-1", complexity="low")


@pytest.mark.asyncio
async def test_taskless_spawn_threads_none_complexity_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A no-task spawn (idle PM bootstrap, Intake/Secretary chats, ...) never
    even attempts a task lookup — complexity=None, byte-identical to the
    pre-cost-tiering call shape."""
    resolve_mock = _wire(monkeypatch, _FakeSession(Complexity.HIGH))

    orch = _orch()
    result = await orch._resolve_agent_route("be-dev-1", None)

    assert result is _SENTINEL_ROUTE
    resolve_mock.assert_awaited_once_with("be-dev-1", complexity=None)


@pytest.mark.asyncio
async def test_missing_task_row_degrades_to_none_complexity_silently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """scalar_one_or_none() returning None (task not found / deleted) is not
    an error — complexity falls back to None and routing still proceeds
    through the router (not the hardcoded legacy path)."""
    resolve_mock = _wire(monkeypatch, _FakeSession(None))

    orch = _orch()
    result = await orch._resolve_agent_route("be-dev-1", "ghost-task-id")

    assert result is _SENTINEL_ROUTE
    resolve_mock.assert_awaited_once_with("be-dev-1", complexity=None)


@pytest.mark.asyncio
async def test_task_lookup_failure_degrades_silently_not_to_full_legacy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A task-lookup-specific failure (bad id, transient query error) must
    NOT escalate to the full DB-failure downgrade (hardcoded ROLE_MODEL_MAP,
    bypassing model_assignments entirely) — only the complexity lookup is
    skipped; AGENT_SLUG/ROLE/GLOBAL resolution still runs via the router."""
    resolve_mock = _wire(monkeypatch, _BoomSession(None))

    orch = _orch()
    result = await orch._resolve_agent_route("be-dev-1", "bad-task-id")

    assert result is _SENTINEL_ROUTE
    resolve_mock.assert_awaited_once_with("be-dev-1", complexity=None)
