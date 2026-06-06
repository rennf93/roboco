"""The spawn readiness gate refuses ANY role onto a dependency-blocked task.

Previously the cross-task dependency check lived only on the dev dispatch
path, so cell-PM, Main-PM and board agents were spawned onto tasks whose
upstream (e.g. the UX/UI design a frontend task waits on) was still open.
Those agents then flailed unblock / escalate / notify against an unfinished
dependency. The gate now lives in ``_readiness_gate`` — the single pre-flight
every ``spawn_agent`` call funnels through — so it covers every role.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import pytest
from roboco.runtime import orchestrator as orchestrator_module
from roboco.runtime.orchestrator import AgentOrchestrator

_TASK_ID = "11111111-1111-1111-1111-111111111111"
_DEP_ID = "22222222-2222-2222-2222-222222222222"


class _FakeResp:
    def __init__(self, status_code: int = 200, payload: dict[str, Any] | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    @property
    def is_success(self) -> bool:
        return self.status_code == HTTPStatus.OK

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    """Async-context HTTP stub routing by URL substring."""

    def __init__(self, routes: dict[str, _FakeResp]):
        self._routes = routes
        self.patches: list[tuple[str, dict[str, Any] | None]] = []

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False

    async def get(self, url: str) -> _FakeResp:
        for key, resp in self._routes.items():
            if key in url:
                return resp
        return _FakeResp(404, {})

    async def patch(self, url: str, json: dict[str, Any] | None = None) -> _FakeResp:
        # The dependency gate auto-blocks via PATCH; record + accept it.
        self.patches.append((url, json))
        return _FakeResp(200, {})


def _orch_with_routes(
    monkeypatch: pytest.MonkeyPatch, routes: dict[str, _FakeResp]
) -> tuple[AgentOrchestrator, _FakeClient]:
    orch = object.__new__(AgentOrchestrator)
    client = _FakeClient(routes)
    # `_api_url` is a property (reads settings); routes match by `/tasks/<id>`
    # substring so the resolved base URL is irrelevant.
    monkeypatch.setattr(
        orchestrator_module.httpx,
        "AsyncClient",
        lambda *_a, **_k: client,
    )
    return orch, client


@pytest.mark.asyncio
async def test_cell_pm_refused_on_nonterminal_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cell PM must NOT be spawned while a cross-cell dependency is open."""
    routes = {
        f"/tasks/{_TASK_ID}": _FakeResp(
            200,
            {
                "id": _TASK_ID,
                "status": "pending",
                "dependency_ids": [_DEP_ID],
                "acceptance_criteria": ["x"],
                "project_id": "r1",
                "project_slug": "roboco",
            },
        ),
        f"/tasks/{_DEP_ID}": _FakeResp(200, {"id": _DEP_ID, "status": "in_progress"}),
    }
    orch, _ = _orch_with_routes(monkeypatch, routes)
    reason = await orch._readiness_gate("be-pm", _TASK_ID)
    assert reason is not None
    assert _DEP_ID in reason


@pytest.mark.asyncio
async def test_board_refused_on_nonterminal_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A board role (product-owner) is gated the same as everyone else."""
    routes = {
        f"/tasks/{_TASK_ID}": _FakeResp(
            200,
            {
                "id": _TASK_ID,
                "status": "pending",
                "dependency_ids": [_DEP_ID],
                "acceptance_criteria": ["x"],
                "product_id": "p1",
                "project_id": None,
            },
        ),
        f"/tasks/{_DEP_ID}": _FakeResp(200, {"id": _DEP_ID, "status": "paused"}),
    }
    orch, _ = _orch_with_routes(monkeypatch, routes)
    reason = await orch._readiness_gate("product-owner", _TASK_ID)
    assert reason is not None
    assert _DEP_ID in reason


@pytest.mark.asyncio
async def test_unreadable_dependency_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dependency we cannot read is treated as unmet — never spawn ahead."""
    routes = {
        f"/tasks/{_TASK_ID}": _FakeResp(
            200,
            {
                "id": _TASK_ID,
                "status": "pending",
                "dependency_ids": [_DEP_ID],
                "acceptance_criteria": ["x"],
                "product_id": "p1",
                "project_id": None,
            },
        ),
        # no route for the dependency → 404 → unreadable
    }
    orch, _ = _orch_with_routes(monkeypatch, routes)
    reason = await orch._readiness_gate("be-pm", _TASK_ID)
    assert reason is not None


@pytest.mark.asyncio
async def test_allowed_when_dependency_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once the dependency is completed, the dependency gate no longer refuses."""
    routes = {
        f"/tasks/{_TASK_ID}": _FakeResp(
            200,
            {
                "id": _TASK_ID,
                "status": "pending",
                "dependency_ids": [_DEP_ID],
                "acceptance_criteria": ["x"],
                # coordination task → project/branch/git-token gates skipped, so a
                # passing dependency gate yields a clean None.
                "product_id": "p1",
                "project_id": None,
            },
        ),
        f"/tasks/{_DEP_ID}": _FakeResp(200, {"id": _DEP_ID, "status": "completed"}),
    }
    orch, _ = _orch_with_routes(monkeypatch, routes)
    reason = await orch._readiness_gate("product-owner", _TASK_ID)
    assert reason is None


@pytest.mark.asyncio
async def test_no_dependencies_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """A task with no dependencies is never refused by this gate."""
    routes = {
        f"/tasks/{_TASK_ID}": _FakeResp(
            200,
            {
                "id": _TASK_ID,
                "status": "pending",
                "dependency_ids": [],
                "acceptance_criteria": ["x"],
                "product_id": "p1",
                "project_id": None,
            },
        ),
    }
    orch, _ = _orch_with_routes(monkeypatch, routes)
    reason = await orch._readiness_gate("main-pm", _TASK_ID)
    assert reason is None


@pytest.mark.asyncio
async def test_dependency_block_auto_blocks_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal auto-blocks the task so it leaves the pending pool.

    A transient (no-block) refusal would re-raise every tick and starve sibling
    tasks in the same dispatcher loop; auto-blocking removes it from the pool
    until the upstream completes.
    """
    routes = {
        f"/tasks/{_TASK_ID}": _FakeResp(
            200,
            {
                "id": _TASK_ID,
                "status": "pending",
                "dependency_ids": [_DEP_ID],
                "acceptance_criteria": ["x"],
                "product_id": "p1",
                "project_id": None,
            },
        ),
        f"/tasks/{_DEP_ID}": _FakeResp(200, {"id": _DEP_ID, "status": "in_progress"}),
    }
    orch, client = _orch_with_routes(monkeypatch, routes)
    reason = await orch._readiness_gate("be-pm", _TASK_ID)
    assert reason is not None
    assert any(
        _TASK_ID in url and (body or {}).get("status") == "blocked"
        for url, body in client.patches
    )
