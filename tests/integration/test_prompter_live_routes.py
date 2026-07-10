"""Integration tests for the live intake chat routes (start/stop + relay + msg).

The SSE stream generator itself is unit-tested at the service layer
(``test_prompter_live``); here we exercise the HTTP contracts against an
injected registry whose container deliveries hit a mocked transport, and the
start/stop routes against a fake orchestrator.
"""

from __future__ import annotations

from datetime import UTC, datetime
from http import HTTPStatus
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api import deps
from roboco.api.deps import get_agent_context
from roboco.api.routes.prompter_live import router
from roboco.db.base import get_db
from roboco.db.tables import ProjectTable, TaskTable
from roboco.models.base import AgentRole, TaskStatus, Team
from roboco.services import prompter_live
from roboco.services.base import ValidationError
from roboco.services.permissions import AgentContext

from tests.unit.services.test_prompter import (
    _confirm_board_batch,
    _seed_project_and_ceo,
    _seed_second_project,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest_asyncio.fixture
async def live_client() -> AsyncIterator[dict[str, Any]]:
    def container_handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(container_handler))
    registry = prompter_live.PrompterLiveRegistry(http_client=mock_client)
    prompter_live._RegistryHolder.instance = registry  # inject the singleton

    app = FastAPI()
    app.include_router(router, prefix="/api/prompter")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "registry": registry}

    prompter_live._RegistryHolder.instance = None
    await mock_client.aclose()


@pytest.mark.asyncio
async def test_relay_event_pushes_to_live_session(live_client: dict) -> None:
    client, registry = live_client["client"], live_client["registry"]
    registry.open("s1", "intake-1")

    resp = await client.post(
        "/api/prompter/live/s1/events", json={"kind": "text", "text": "hi"}
    )
    assert resp.status_code == HTTPStatus.OK
    assert resp.json() == {"pushed": True}

    # The event is now on the session's queue.
    assert registry.get("s1").queue.qsize() == 1


@pytest.mark.asyncio
async def test_relay_event_unknown_session_is_noop(live_client: dict) -> None:
    resp = await live_client["client"].post(
        "/api/prompter/live/nope/events", json={"kind": "text"}
    )
    assert resp.status_code == HTTPStatus.OK
    assert resp.json() == {"pushed": False}


@pytest.mark.asyncio
async def test_status_reports_alive_for_open_session(live_client: dict) -> None:
    client, registry = live_client["client"], live_client["registry"]
    registry.open("s1", "intake-1")
    resp = await client.get("/api/prompter/live/s1/status")
    assert resp.status_code == HTTPStatus.OK
    assert resp.json() == {"alive": True}


@pytest.mark.asyncio
async def test_status_reports_dead_for_unknown_session(live_client: dict) -> None:
    resp = await live_client["client"].get("/api/prompter/live/nope/status")
    assert resp.status_code == HTTPStatus.OK
    assert resp.json() == {"alive": False}


@pytest.mark.asyncio
async def test_send_message_delivers_to_container(live_client: dict) -> None:
    client, registry = live_client["client"], live_client["registry"]
    registry.open("s1", "intake-1")

    resp = await client.post(
        "/api/prompter/live/s1/messages", json={"text": "hello there"}
    )
    assert resp.status_code == HTTPStatus.OK
    assert resp.json() == {"delivered": True}


@pytest.mark.asyncio
async def test_send_message_unknown_session_404(live_client: dict) -> None:
    resp = await live_client["client"].post(
        "/api/prompter/live/nope/messages", json={"text": "hi"}
    )
    assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_send_message_requires_text(live_client: dict) -> None:
    live_client["registry"].open("s1", "intake-1")
    resp = await live_client["client"].post(
        "/api/prompter/live/s1/messages", json={"text": ""}
    )
    assert resp.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


# ---------------------------------------------------------------------------
# start / stop — spawn + reap against a fake orchestrator (no docker).
# ---------------------------------------------------------------------------


class _FakeOrchestrator:
    """Records spawn/reap calls; stands in for the real orchestrator singleton."""

    def __init__(self) -> None:
        self.spawned: list[dict[str, Any]] = []
        self.reaped: list[str] = []

    async def start_intake_session(
        self,
        session_id: str,
        *,
        project_slug: str | None = None,
        product_id: str | None = None,
        project_ids: list[str] | None = None,
        initial_message: str | None = None,
    ) -> None:
        # The route is non-blocking now: it calls start_intake_session (returns
        # None) which opens the relay + spawns in the background.
        self.spawned.append(
            {
                "session_id": session_id,
                "project_slug": project_slug,
                "product_id": product_id,
                "project_ids": project_ids,
                "initial_message": initial_message,
            }
        )

    async def reap_intake_session(self, session_id: str) -> None:
        self.reaped.append(session_id)


@pytest_asyncio.fixture
async def start_client(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[dict[str, Any]]:
    orch = _FakeOrchestrator()
    # monkeypatch.setattr is untyped (no ignore for the fake) and auto-reverts.
    monkeypatch.setattr(deps._ServiceHolder, "orchestrator", orch)

    async def _fake_db() -> AsyncIterator[object]:
        yield object()  # product-scope start never touches it

    app = FastAPI()
    app.include_router(router, prefix="/api/prompter")
    app.dependency_overrides[get_db] = _fake_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "orch": orch}


@pytest.mark.asyncio
async def test_start_product_scope_spawns_and_returns_session(
    start_client: dict,
) -> None:
    client, orch = start_client["client"], start_client["orch"]
    product_id = str(uuid4())

    resp = await client.post(
        "/api/prompter/live/start",
        json={"product_id": product_id, "initial_message": "build X"},
    )
    assert resp.status_code == HTTPStatus.CREATED
    session_id = resp.json()["session_id"]
    assert session_id
    assert orch.spawned == [
        {
            "session_id": session_id,
            "project_slug": None,
            "product_id": product_id,
            "project_ids": None,
            "initial_message": "build X",
        }
    ]


@pytest.mark.asyncio
async def test_start_megatask_scope_passes_project_ids(start_client: dict) -> None:
    client, orch = start_client["client"], start_client["orch"]
    ids = [str(uuid4()), str(uuid4())]

    resp = await client.post(
        "/api/prompter/live/start",
        json={"project_ids": ids, "initial_message": "three repos"},
    )
    assert resp.status_code == HTTPStatus.CREATED
    assert orch.spawned[0]["project_ids"] == ids
    assert orch.spawned[0]["project_slug"] is None
    assert orch.spawned[0]["product_id"] is None


@pytest.mark.asyncio
async def test_start_project_scope_resolves_slug(start_client: dict) -> None:
    client, orch = start_client["client"], start_client["orch"]
    project_id = uuid4()

    fake_svc = SimpleNamespace(
        get=lambda _pid: _async_return(SimpleNamespace(slug="roboco"))
    )
    with patch("roboco.services.project.get_project_service", lambda _db: fake_svc):
        resp = await client.post(
            "/api/prompter/live/start", json={"project_id": str(project_id)}
        )
    assert resp.status_code == HTTPStatus.CREATED
    assert orch.spawned[0]["project_slug"] == "roboco"
    assert orch.spawned[0]["product_id"] is None


@pytest.mark.asyncio
async def test_start_unknown_project_404(start_client: dict) -> None:
    client = start_client["client"]
    fake_svc = SimpleNamespace(get=lambda _pid: _async_return(None))
    with patch("roboco.services.project.get_project_service", lambda _db: fake_svc):
        resp = await client.post(
            "/api/prompter/live/start", json={"project_id": str(uuid4())}
        )
    assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_start_requires_exactly_one_scope(start_client: dict) -> None:
    client = start_client["client"]
    both = await client.post(
        "/api/prompter/live/start",
        json={"project_id": str(uuid4()), "product_id": str(uuid4())},
    )
    assert both.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    neither = await client.post("/api/prompter/live/start", json={})
    assert neither.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_stop_reaps_session(start_client: dict) -> None:
    client, orch = start_client["client"], start_client["orch"]
    resp = await client.post("/api/prompter/live/sess-9/stop")
    assert resp.status_code == HTTPStatus.OK
    assert resp.json() == {"stopped": True}
    assert orch.reaped == ["sess-9"]


def _async_return(value: Any) -> Any:
    """Wrap a value in an awaitable so a lambda can stand in for an async method."""

    async def _coro() -> Any:
        return value

    return _coro()


# ---------------------------------------------------------------------------
# confirm — draft → task + reap-on-confirm (service mocked; route wiring only).
# ---------------------------------------------------------------------------


class _FakeDb:
    async def commit(self) -> None:
        return None


@pytest_asyncio.fixture
async def confirm_client(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[dict[str, Any]]:
    orch = _FakeOrchestrator()
    monkeypatch.setattr(deps._ServiceHolder, "orchestrator", orch)

    async def _fake_db() -> AsyncIterator[_FakeDb]:
        yield _FakeDb()

    ceo = AgentContext(agent_id=uuid4(), role=AgentRole.CEO, team=None, slug="ceo")

    # A real (un-mocked) registry so a board-route confirm can actually park —
    # a test that wants park-success must first ``registry.open(session_id, …)``.
    registry = prompter_live.PrompterLiveRegistry()
    prompter_live._RegistryHolder.instance = registry

    app = FastAPI()
    app.include_router(router, prefix="/api/prompter")
    app.dependency_overrides[get_db] = _fake_db
    app.dependency_overrides[get_agent_context] = lambda: ceo
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "orch": orch, "registry": registry}

    prompter_live._RegistryHolder.instance = None


@pytest.mark.asyncio
async def test_confirm_creates_task_and_reaps(confirm_client: dict) -> None:
    client, orch = confirm_client["client"], confirm_client["orch"]
    task_id = uuid4()

    class _FakeService:
        async def confirm_live_draft(self, _draft: Any, _agent: Any, **_kw: Any) -> Any:
            return task_id

    with patch(
        "roboco.api.routes.prompter_live.get_prompter_service",
        lambda _db: _FakeService(),
    ):
        resp = await client.post(
            "/api/prompter/live/s1/confirm",
            json={
                "project_id": str(uuid4()),
                "draft": {"title": "x", "acceptance_criteria": ["a"]},
            },
        )
    assert resp.status_code == HTTPStatus.CREATED
    assert resp.json() == {"task_id": str(task_id)}
    assert orch.reaped == ["s1"]  # reap-on-confirm


@pytest.mark.asyncio
async def test_confirm_requires_exactly_one_target(confirm_client: dict) -> None:
    client = confirm_client["client"]
    both = await client.post(
        "/api/prompter/live/s1/confirm",
        json={
            "project_id": str(uuid4()),
            "product_id": str(uuid4()),
            "draft": {"title": "x"},
        },
    )
    assert both.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_confirm_validation_error_is_translated_and_not_reaped(
    confirm_client: dict,
) -> None:
    client, orch = confirm_client["client"], confirm_client["orch"]

    class _FakeService:
        async def confirm_live_draft(self, _draft: Any, _agent: Any, **_kw: Any) -> Any:
            raise ValidationError(message="bad draft", field="title")

    with patch(
        "roboco.api.routes.prompter_live.get_prompter_service",
        lambda _db: _FakeService(),
    ):
        resp = await client.post(
            "/api/prompter/live/s1/confirm",
            json={"project_id": str(uuid4()), "draft": {"title": "x"}},
        )
    assert resp.status_code == HTTPStatus.BAD_REQUEST
    assert orch.reaped == []  # a failed confirm must NOT reap the session


# ---------------------------------------------------------------------------
# MegaTask batch routes — confirm-batch (terminal, reaps) + preview-batch (pure).
# ---------------------------------------------------------------------------


def _batch_body() -> dict[str, Any]:
    return {
        "title": "MegaTask",
        "drafts": [
            {"title": "A", "acceptance_criteria": ["a"], "project_id": str(uuid4())},
            {"title": "B", "acceptance_criteria": ["b"], "project_id": str(uuid4())},
        ],
        "project_ids": [str(uuid4()), str(uuid4())],
        "route": "main_pm",
    }


def _batch_result(*, n: int = 2) -> dict[str, Any]:
    return {
        "umbrella_task_id": str(uuid4()),
        "root_subtask_ids": [str(uuid4()) for _ in range(n)],
        "waves": [[i] for i in range(n)],
        "warnings": [],
    }


@pytest.mark.asyncio
async def test_confirm_batch_main_pm_route_creates_and_reaps(
    confirm_client: dict,
) -> None:
    """A fresh confirm on the "main_pm" route is always terminal → reap."""
    client, orch = confirm_client["client"], confirm_client["orch"]
    result = _batch_result()

    class _FakeService:
        async def confirm_live_batch(self, *_a: Any, **_kw: Any) -> Any:
            return result

    with patch(
        "roboco.api.routes.prompter_live.get_prompter_service",
        lambda _db: _FakeService(),
    ):
        resp = await client.post(
            "/api/prompter/live/s1/confirm-batch", json=_batch_body()
        )
    assert resp.status_code == HTTPStatus.CREATED
    assert resp.json() == result
    assert orch.reaped == ["s1"]  # confirm-batch main_pm route is terminal → reap


@pytest.mark.asyncio
async def test_confirm_batch_board_route_parks_session(confirm_client: dict) -> None:
    """A fresh confirm on the "board" route keeps the intake agent alive,
    parked against the umbrella — the batch-shape mirror of the single-draft
    keep-alive re-draft loop."""
    client, orch, registry = (
        confirm_client["client"],
        confirm_client["orch"],
        confirm_client["registry"],
    )
    registry.open("s1", "intake-1")
    result = _batch_result()

    class _FakeService:
        async def confirm_live_batch(self, *_a: Any, **_kw: Any) -> Any:
            return result

    body = _batch_body()
    body["route"] = "board"
    with patch(
        "roboco.api.routes.prompter_live.get_prompter_service",
        lambda _db: _FakeService(),
    ):
        resp = await client.post("/api/prompter/live/s1/confirm-batch", json=body)
    assert resp.status_code == HTTPStatus.CREATED
    assert resp.json() == result
    assert orch.reaped == []  # parked, not reaped
    assert registry.get("s1") is not None
    assert registry.get("s1").task_id == result["umbrella_task_id"]


@pytest.mark.asyncio
async def test_confirm_batch_redraft_always_reaps(confirm_client: dict) -> None:
    """A redraft confirm (``task_id`` set) always reaps, even on the "board"
    route — parity with a single-draft redraft confirm (never keeps the agent
    alive a second time; the umbrella already exists)."""
    client, orch, registry = (
        confirm_client["client"],
        confirm_client["orch"],
        confirm_client["registry"],
    )
    registry.open("s1", "intake-1")
    task_id = uuid4()
    result = _batch_result(n=1)
    result["umbrella_task_id"] = str(task_id)

    class _FakeService:
        async def update_live_batch(self, *_a: Any, **_kw: Any) -> Any:
            return result

    body = _batch_body()
    body["route"] = "board"
    body["task_id"] = str(task_id)
    with patch(
        "roboco.api.routes.prompter_live.get_prompter_service",
        lambda _db: _FakeService(),
    ):
        resp = await client.post("/api/prompter/live/s1/confirm-batch", json=body)
    assert resp.status_code == HTTPStatus.CREATED
    assert resp.json() == result
    assert orch.reaped == ["s1"]  # redraft confirm is always terminal → reap


@pytest.mark.asyncio
async def test_confirm_batch_validation_error_not_reaped(confirm_client: dict) -> None:
    client, orch = confirm_client["client"], confirm_client["orch"]

    class _FakeService:
        async def confirm_live_batch(self, *_a: Any, **_kw: Any) -> Any:
            raise ValidationError(message="bad batch", field="drafts")

    with patch(
        "roboco.api.routes.prompter_live.get_prompter_service",
        lambda _db: _FakeService(),
    ):
        resp = await client.post(
            "/api/prompter/live/s1/confirm-batch", json=_batch_body()
        )
    assert resp.status_code == HTTPStatus.BAD_REQUEST
    assert orch.reaped == []  # a failed confirm must NOT reap


@pytest.mark.asyncio
async def test_confirm_batch_schema_rejects_too_few_projects(
    confirm_client: dict,
) -> None:
    client = confirm_client["client"]
    body = _batch_body()
    body["project_ids"] = [str(uuid4())]  # < 2 → schema 422
    resp = await client.post("/api/prompter/live/s1/confirm-batch", json=body)
    assert resp.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_preview_batch_returns_waves_and_does_not_reap(
    confirm_client: dict,
) -> None:
    client, orch = confirm_client["client"], confirm_client["orch"]

    class _FakeService:
        def preview_batch(self, _drafts: Any) -> Any:
            return {"waves": [[0, 1]], "warnings": []}

    with patch(
        "roboco.api.routes.prompter_live.get_prompter_service",
        lambda _db: _FakeService(),
    ):
        resp = await client.post(
            "/api/prompter/live/s1/preview-batch",
            json={"drafts": [{"title": "A"}, {"title": "B"}]},
        )
    assert resp.status_code == HTTPStatus.OK
    assert resp.json() == {"waves": [[0, 1]], "warnings": []}
    assert orch.reaped == []  # preview creates nothing and leaves the chat alive


# ---------------------------------------------------------------------------
# re-interview — cold redraft: single-task scope vs. MegaTask umbrella recovery.
# DB-backed (real task/journal services + composer) with a fake orchestrator,
# so the umbrella branch's scope recovery runs for real.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def reinterview_client(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[dict[str, Any]]:
    orch = _FakeOrchestrator()
    monkeypatch.setattr(deps._ServiceHolder, "orchestrator", orch)

    async def _real_db() -> AsyncIterator[Any]:
        yield db_session

    ceo = AgentContext(agent_id=uuid4(), role=AgentRole.CEO, team=None, slug="ceo")
    app = FastAPI()
    app.include_router(router, prefix="/api/prompter")
    app.dependency_overrides[get_db] = _real_db
    app.dependency_overrides[get_agent_context] = lambda: ceo
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "orch": orch, "db": db_session}


def _plain_task(ceo_id: Any, **overrides: Any) -> TaskTable:
    fields: dict[str, Any] = {
        "id": uuid4(),
        "title": "Solo task",
        "description": "A task the board reviewed, up for a redraft round.",
        "acceptance_criteria": ["done"],
        "status": TaskStatus.PENDING,
        "team": Team.BACKEND,
        "created_by": ceo_id,
        **overrides,
    }
    return TaskTable(**fields)


@pytest.mark.asyncio
async def test_re_interview_umbrella_recovers_scope_and_seeds_batch(
    reinterview_client: dict,
) -> None:
    """An umbrella re-interview recovers the multi-repo scope from its
    root-subtasks (single-project child + cell_projects union child), returns it
    to the panel, and seeds the batch composer's redraft message."""
    client, orch, db = (
        reinterview_client["client"],
        reinterview_client["orch"],
        reinterview_client["db"],
    )
    project1, ceo_id = await _seed_project_and_ceo(db)
    project2 = await _seed_second_project(db, ceo_id)
    project3 = await _seed_second_project(db, ceo_id)
    drafts: list[dict[str, Any]] = [
        {
            "title": "Single child",
            "acceptance_criteria": ["a"],
            "team": "backend",
            "project_id": str(project1),
        },
        {
            "title": "Union child",
            "acceptance_criteria": ["b"],
            "the_work": [
                {"team": "backend", "summary": "s", "project_id": str(project2)},
                {"team": "frontend", "summary": "s", "project_id": str(project3)},
            ],
        },
    ]
    result = await _confirm_board_batch(
        db, ceo_id, drafts, [project1, project2, project3]
    )

    resp = await client.post(
        f"/api/prompter/live/re-interview/{result['umbrella_task_id']}", json={}
    )

    assert resp.status_code == HTTPStatus.CREATED
    body = resp.json()
    assert body["session_id"]
    expected = {str(project1), str(project2), str(project3)}
    assert set(body["project_ids"]) == expected
    spawn = orch.spawned[0]
    assert set(spawn["project_ids"]) == expected  # multi-project intake scope
    assert spawn["project_slug"] is None
    assert spawn["product_id"] is None
    msg = spawn["initial_message"]
    assert "propose_batch" in msg  # batch-aware seed, not the single-task one
    assert "Single child" in msg
    assert "Union child" in msg


@pytest.mark.asyncio
async def test_re_interview_umbrella_400_when_no_recoverable_projects(
    reinterview_client: dict,
) -> None:
    """An umbrella with no live project-bearing children cannot re-interview."""
    client, db = reinterview_client["client"], reinterview_client["db"]
    _project1, ceo_id = await _seed_project_and_ceo(db)
    umbrella = _plain_task(
        ceo_id, title="MegaTask: empty", team=Team.BOARD, batch_id=uuid4()
    )
    db.add(umbrella)
    await db.flush()

    resp = await client.post(f"/api/prompter/live/re-interview/{umbrella.id}", json={})

    assert resp.status_code == HTTPStatus.BAD_REQUEST
    assert "no recoverable projects" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_re_interview_single_task_branch_unchanged(
    reinterview_client: dict,
) -> None:
    """A non-batch task still takes the single-task path: project-slug scope and
    the single-draft redraft seed."""
    client, orch, db = (
        reinterview_client["client"],
        reinterview_client["orch"],
        reinterview_client["db"],
    )
    project1, ceo_id = await _seed_project_and_ceo(db)
    task = _plain_task(ceo_id, project_id=project1)
    db.add(task)
    await db.flush()
    slug = (await db.get(ProjectTable, project1)).slug

    resp = await client.post(f"/api/prompter/live/re-interview/{task.id}", json={})

    assert resp.status_code == HTTPStatus.CREATED
    assert resp.json()["project_ids"] is None  # single-task path: no batch scope
    spawn = orch.spawned[0]
    assert spawn["project_slug"] == slug
    assert spawn["product_id"] is None
    assert spawn["project_ids"] is None
    msg = spawn["initial_message"]
    assert "revising an existing task draft" in msg
    assert "Solo task" in msg
    assert "propose_batch" not in msg


@pytest.mark.asyncio
async def test_re_interview_single_task_400_without_scope(
    reinterview_client: dict,
) -> None:
    """A non-batch task with neither project nor product still 400s."""
    client, db = reinterview_client["client"], reinterview_client["db"]
    _project1, ceo_id = await _seed_project_and_ceo(db)
    task = _plain_task(ceo_id)  # no project_id / product_id / batch_id
    db.add(task)
    await db.flush()

    resp = await client.post(f"/api/prompter/live/re-interview/{task.id}", json={})

    assert resp.status_code == HTTPStatus.BAD_REQUEST
    assert "no project/product scope" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# search-tasks — the intake's mid-conversation "have we done this before?" tool.
# ---------------------------------------------------------------------------


def _row(title: str = "Fix login bug") -> MagicMock:
    row = MagicMock()
    row.id = uuid4()
    row.title = title
    row.status = "completed"
    row.team = "backend"
    row.completed_at = datetime.now(UTC)
    row.updated_at = None
    row.created_at = datetime.now(UTC)
    return row


@pytest.mark.asyncio
async def test_search_tasks_returns_compact_rows_for_alive_session(
    live_client: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, registry = live_client["client"], live_client["registry"]
    registry.open("s1", "intake-1")

    task_svc = MagicMock()
    task_svc.search_tasks = AsyncMock(return_value=[_row()])
    monkeypatch.setattr("roboco.services.task.get_task_service", lambda _db: task_svc)

    resp = await client.get("/api/prompter/live/s1/search-tasks", params={"q": "login"})

    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert len(body) == 1
    assert set(body[0].keys()) == {"id", "title", "status", "team", "date"}
    assert body[0]["title"] == "Fix login bug"
    task_svc.search_tasks.assert_awaited_once()


@pytest.mark.asyncio
async def test_search_tasks_unknown_session_404(live_client: dict) -> None:
    resp = await live_client["client"].get(
        "/api/prompter/live/nope/search-tasks", params={"q": "login"}
    )
    assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_search_tasks_query_too_short_422(live_client: dict) -> None:
    live_client["registry"].open("s1", "intake-1")
    resp = await live_client["client"].get(
        "/api/prompter/live/s1/search-tasks", params={"q": "a"}
    )
    assert resp.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_search_tasks_limit_above_max_422(live_client: dict) -> None:
    live_client["registry"].open("s1", "intake-1")
    resp = await live_client["client"].get(
        "/api/prompter/live/s1/search-tasks", params={"q": "login", "limit": 11}
    )
    assert resp.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
