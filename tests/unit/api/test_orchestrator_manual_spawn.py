"""Manual (panel) spawn: task-aware prompt helper + already-running signaling.

Covers the CEO-facing spawn-refusal / double-fire triage: a task-aware
initial prompt built server-side for a manual spawn (mirroring
``_build_pr_review_prompt``'s tone), an ``AgentReadinessError`` refusal
mapped to 409 (not an opaque 500) so the panel can show the real reason, and
an ``already_running`` marker so a no-op spawn (agent already active) is
distinguishable from a genuine new spawn.
"""

from __future__ import annotations

from datetime import UTC, datetime
from http import HTTPStatus
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio
import roboco.api.routes.orchestrator as orch_route
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from roboco.agents_config import AGENT_UUIDS
from roboco.api.deps import _ServiceHolder, set_orchestrator
from roboco.api.routes.orchestrator import (
    _build_manual_spawn_prompt,
    _resolve_manual_spawn_prompt,
    _validated_agent_id,
)
from roboco.api.routes.orchestrator import (
    router as orch_router,
)
from roboco.runtime.orchestrator import AgentReadinessError, AgentState

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from roboco.db.tables import TaskTable

_HDR = {"X-Agent-ID": str(uuid4()), "X-Agent-Role": "ceo"}


def _fake_task(status_value: str = "pending") -> TaskTable:
    # SimpleNamespace duck-types TaskTable's 3 fields the helper reads
    # (id/title/status.value) without a real ORM row.
    return cast(
        "TaskTable",
        SimpleNamespace(
            id="task-123",
            title="Fix the thing",
            status=SimpleNamespace(value=status_value),
        ),
    )


class _FakeDbCtx:
    async def __aenter__(self) -> str:
        return "fake-db"

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakeTaskService:
    def __init__(self, task: object | None = None, error: Exception | None = None):
        self._task = task
        self._error = error

    async def get(self, _task_id: object) -> object | None:
        if self._error:
            raise self._error
        return self._task


# ---------------------------------------------------------------------------
# _build_manual_spawn_prompt — pure formatting
# ---------------------------------------------------------------------------


def test_build_manual_spawn_prompt_includes_task_fields() -> None:
    prompt = _build_manual_spawn_prompt(_fake_task("awaiting_qa"), None)
    assert "TASK ID: task-123" in prompt
    assert "TITLE: Fix the thing" in prompt
    assert "STATUS: awaiting_qa" in prompt
    assert "claim verb" in prompt.lower()
    assert "CEO NOTE" not in prompt


def test_build_manual_spawn_prompt_appends_ceo_note() -> None:
    prompt = _build_manual_spawn_prompt(_fake_task(), "Please prioritize this.")
    assert "== CEO NOTE ==" in prompt
    assert "Please prioritize this." in prompt
    # CEO note comes after the task framing, not instead of it.
    assert prompt.index("TASK ID") < prompt.index("CEO NOTE")


# ---------------------------------------------------------------------------
# _resolve_manual_spawn_prompt — best-effort enrichment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_prompt_no_task_id_returns_message_unchanged() -> None:
    result = await _resolve_manual_spawn_prompt(None, "hello")
    assert result == "hello"


@pytest.mark.asyncio
async def test_resolve_prompt_enriches_when_task_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(orch_route, "get_db_context", _FakeDbCtx)
    monkeypatch.setattr(
        orch_route,
        "get_task_service",
        lambda _db: _FakeTaskService(task=_fake_task("verifying")),
    )
    result = await _resolve_manual_spawn_prompt(str(uuid4()), "Ship it")
    assert result is not None
    assert "STATUS: verifying" in result
    assert "Ship it" in result


@pytest.mark.asyncio
async def test_resolve_prompt_falls_back_when_task_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(orch_route, "get_db_context", _FakeDbCtx)
    monkeypatch.setattr(
        orch_route, "get_task_service", lambda _db: _FakeTaskService(task=None)
    )
    result = await _resolve_manual_spawn_prompt(str(uuid4()), "hello")
    assert result == "hello"


@pytest.mark.asyncio
async def test_resolve_prompt_falls_back_on_bad_task_id() -> None:
    # Not a valid UUID — must not raise, must fall back unchanged.
    result = await _resolve_manual_spawn_prompt("not-a-uuid", "hello")
    assert result == "hello"


@pytest.mark.asyncio
async def test_resolve_prompt_falls_back_on_db_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(orch_route, "get_db_context", _FakeDbCtx)
    monkeypatch.setattr(
        orch_route,
        "get_task_service",
        lambda _db: _FakeTaskService(error=RuntimeError("db down")),
    )
    result = await _resolve_manual_spawn_prompt(str(uuid4()), "hello")
    assert result == "hello"


@pytest.mark.asyncio
async def test_resolve_prompt_no_message_no_task_returns_none() -> None:
    result = await _resolve_manual_spawn_prompt(None, None)
    assert result is None


# ---------------------------------------------------------------------------
# Route: AgentReadinessError -> 409, already_running signaling
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def orch_client() -> AsyncIterator[tuple[AsyncClient, MagicMock]]:
    app = FastAPI()
    app.include_router(orch_router, prefix="/api/orchestrator")
    orchestrator = MagicMock()
    set_orchestrator(orchestrator)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, orchestrator
    _ServiceHolder.orchestrator = None
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_spawn_readiness_refusal_maps_to_409(
    orch_client: tuple[AsyncClient, MagicMock],
) -> None:
    client, orch = orch_client
    orch.get_instance = MagicMock(return_value=None)
    orch.spawn_agent = AsyncMock(
        side_effect=AgentReadinessError(
            "spawn refused for fe-dev-2 (task=t1): state=awaiting_qa requires "
            "role in {'qa'} but agent fe-dev-2 is 'developer'"
        )
    )
    response = await client.post(
        "/api/orchestrator/agents/fe-dev-2/spawn",
        json={"agent_id": "fe-dev-2", "task_id": "t1"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.CONFLICT
    assert "requires role in" in response.json()["detail"]


@pytest.mark.asyncio
async def test_spawn_new_agent_not_flagged_already_running(
    orch_client: tuple[AsyncClient, MagicMock],
) -> None:
    client, orch = orch_client
    orch.get_instance = MagicMock(return_value=None)
    instance = SimpleNamespace(
        id=uuid4(),
        agent_id="be-dev-1",
        state=AgentState.STARTING,
        current_task_id=None,
        error_count=0,
        started_at=datetime.now(UTC),
    )
    orch.spawn_agent = AsyncMock(return_value=instance)
    response = await client.post(
        "/api/orchestrator/agents/be-dev-1/spawn", headers=_HDR
    )
    assert response.status_code == HTTPStatus.CREATED
    assert response.json()["already_running"] is False


@pytest.mark.asyncio
async def test_spawn_already_running_agent_is_flagged(
    orch_client: tuple[AsyncClient, MagicMock],
) -> None:
    client, orch = orch_client
    shared_id = uuid4()
    existing = SimpleNamespace(
        id=shared_id,
        agent_id="ux-pm",
        state=AgentState.STARTING,
        current_task_id=None,
        error_count=0,
        started_at=datetime.now(UTC),
    )
    orch.get_instance = MagicMock(return_value=existing)
    # spawn_agent's own no-op contract: hands back the SAME instance.
    orch.spawn_agent = AsyncMock(return_value=existing)
    response = await client.post("/api/orchestrator/agents/ux-pm/spawn", headers=_HDR)
    assert response.status_code == HTTPStatus.CREATED
    body = response.json()
    assert body["already_running"] is True
    assert body["state"] == "starting"


@pytest.mark.asyncio
async def test_spawn_offline_agent_not_flagged_already_running(
    orch_client: tuple[AsyncClient, MagicMock],
) -> None:
    """A pre-existing OFFLINE instance is not "running" — a fresh spawn on top
    of it must not be reported as a no-op."""
    client, orch = orch_client
    offline = SimpleNamespace(
        id=uuid4(),
        agent_id="be-dev-1",
        state=AgentState.OFFLINE,
        current_task_id=None,
        error_count=0,
        started_at=datetime.now(UTC),
    )
    orch.get_instance = MagicMock(return_value=offline)
    new_instance = SimpleNamespace(
        id=uuid4(),
        agent_id="be-dev-1",
        state=AgentState.STARTING,
        current_task_id=None,
        error_count=0,
        started_at=datetime.now(UTC),
    )
    orch.spawn_agent = AsyncMock(return_value=new_instance)
    response = await client.post(
        "/api/orchestrator/agents/be-dev-1/spawn", headers=_HDR
    )
    assert response.status_code == HTTPStatus.CREATED
    assert response.json()["already_running"] is False


# ---------------------------------------------------------------------------
# _validated_agent_id — UUID -> slug normalization (root fix: a caller that
# addresses a runtime container/instance by an agent's DB UUID instead of its
# slug, e.g. the panel spawn button, must resolve to the same canonical slug
# the orchestrator's instance registry and container names use).
# ---------------------------------------------------------------------------


def test_validated_agent_id_resolves_known_uuid_to_slug() -> None:
    uuid_str = AGENT_UUIDS["head-marketing"]
    assert _validated_agent_id(uuid_str) == "head-marketing"


def test_validated_agent_id_passes_through_slug_unchanged() -> None:
    assert _validated_agent_id("head-marketing") == "head-marketing"


def test_validated_agent_id_passes_through_unknown_uuid_unchanged() -> None:
    # A uuid4 is never a seeded agent UUID (the seeds are deterministic,
    # low-cardinality values) — genuinely absent from the UUID -> slug map.
    unknown_uuid = str(uuid4())
    assert unknown_uuid not in AGENT_UUIDS.values()
    assert _validated_agent_id(unknown_uuid) == unknown_uuid


def test_validated_agent_id_still_rejects_traversal() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _validated_agent_id("../etc/passwd")
    assert exc_info.value.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_spawn_by_uuid_reaches_orchestrator_by_slug(
    orch_client: tuple[AsyncClient, MagicMock],
) -> None:
    """The panel (or any caller) posting the agent's DB UUID as the path
    param must not produce a container/instance keyed by that UUID — the
    orchestrator only ever sees the canonical slug."""
    client, orch = orch_client
    orch.get_instance = MagicMock(return_value=None)
    instance = SimpleNamespace(
        id=uuid4(),
        agent_id="head-marketing",
        state=AgentState.STARTING,
        current_task_id=None,
        error_count=0,
        started_at=datetime.now(UTC),
    )
    orch.spawn_agent = AsyncMock(return_value=instance)
    uuid_str = AGENT_UUIDS["head-marketing"]
    response = await client.post(
        f"/api/orchestrator/agents/{uuid_str}/spawn", headers=_HDR
    )
    assert response.status_code == HTTPStatus.CREATED
    orch.spawn_agent.assert_awaited_once()
    assert orch.spawn_agent.await_args.kwargs["agent_id"] == "head-marketing"


@pytest.mark.asyncio
async def test_stop_by_uuid_reaches_orchestrator_by_slug(
    orch_client: tuple[AsyncClient, MagicMock],
) -> None:
    client, orch = orch_client
    orch.stop_agent = AsyncMock(return_value=None)
    uuid_str = AGENT_UUIDS["be-dev-1"]
    response = await client.post(
        f"/api/orchestrator/agents/{uuid_str}/stop", headers=_HDR
    )
    assert response.status_code == HTTPStatus.NO_CONTENT
    orch.stop_agent.assert_awaited_once()
    assert orch.stop_agent.await_args.args[0] == "be-dev-1"
