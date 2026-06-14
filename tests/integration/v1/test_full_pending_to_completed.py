"""End-to-end test: task pending -> awaiting_ceo_approval through the gateway.

Uses a minimal FastAPI app that includes all six v1 routers (no lifespan / no DB).
Choreographer and ContentActions are replaced by stateful async mocks that track
the simulated task lifecycle through every role hand-off.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator
from fastapi import FastAPI
from fastapi.testclient import TestClient
from roboco.api.deps import get_choreographer, get_content_actions
from roboco.api.routes.v1 import do as do_module
from roboco.api.routes.v1 import flow_cell_pm as flow_cell_pm_module
from roboco.api.routes.v1 import flow_dev as flow_dev_module
from roboco.api.routes.v1 import flow_doc as flow_doc_module
from roboco.api.routes.v1 import flow_main_pm as flow_main_pm_module
from roboco.api.routes.v1 import flow_qa as flow_qa_module
from roboco.services.gateway.envelope import Envelope

_HTTP_200 = 200
_PR_NUMBER = 8

# Shared mutable state threaded through all mock methods.
_State = dict[str, object]


def _build_app() -> FastAPI:
    """Minimal FastAPI app with all v1 routers — no lifespan, no DB."""
    app = FastAPI()
    app.include_router(flow_dev_module.router)
    app.include_router(flow_qa_module.router)
    app.include_router(flow_doc_module.router)
    app.include_router(flow_cell_pm_module.router)
    app.include_router(flow_main_pm_module.router)
    app.include_router(do_module.router)
    return app


class _MockChoreographer:
    """Stateful stub for Choreographer; methods advance shared lifecycle state."""

    def __init__(self, state: _State) -> None:
        self._state = state

    async def give_me_work(self, _agent_id: object) -> Envelope:
        return Envelope.ok(
            status=str(self._state["task_status"]),
            task_id=str(uuid4()),
            next="claim it",
        )

    async def i_will_work_on(
        self,
        _agent_id: object,
        _task_id: object,
        _plan: object = None,
        **_kwargs: object,
    ) -> Envelope:
        self._state["task_status"] = "in_progress"
        return Envelope.ok(
            status="in_progress",
            task_id=str(uuid4()),
            next="commit + i_am_done",
        )

    async def i_am_done(
        self,
        _agent_id: object,
        _task_id: object,
        _notes: object,
    ) -> Envelope:
        self._state["task_status"] = "awaiting_qa"
        return Envelope.ok(
            status="awaiting_qa",
            task_id=str(uuid4()),
            next="idle until QA",
            evidence={"pr_url": "https://x/pr/8", "pr_number": _PR_NUMBER},
        )

    async def claim_review(
        self,
        _agent_id: object,
        _task_id: object,
    ) -> Envelope:
        self._state["task_status"] = "claimed"
        return Envelope.ok(
            status="claimed",
            task_id=str(uuid4()),
            next="review then pass",
            evidence={"pr_url": "https://x/pr/8", "pr_number": _PR_NUMBER},
        )

    async def pass_review(
        self,
        _agent_id: object,
        _task_id: object,
        _notes: object,
        _ac_verdicts: object = None,
    ) -> Envelope:
        self._state["task_status"] = "awaiting_documentation"
        return Envelope.ok(
            status="awaiting_documentation",
            task_id=str(uuid4()),
            next="idle",
        )

    async def claim_doc_task(
        self,
        _agent_id: object,
        _task_id: object,
    ) -> Envelope:
        self._state["task_status"] = "claimed"
        return Envelope.ok(
            status="claimed",
            task_id=str(uuid4()),
            next="document then i_documented",
        )

    async def i_documented(
        self,
        _agent_id: object,
        _task_id: object,
        _notes: object,
        _files: object,
    ) -> Envelope:
        self._state["task_status"] = "awaiting_pm_review"
        return Envelope.ok(
            status="awaiting_pm_review",
            task_id=str(uuid4()),
            next="idle until PM completes",
        )

    async def complete(
        self,
        _agent_id: object,
        _task_id: object,
        _notes: object,
    ) -> Envelope:
        self._state["task_status"] = "completed"
        return Envelope.ok(
            status="completed",
            task_id=str(uuid4()),
            next="done",
        )

    async def main_pm_complete(
        self,
        _agent_id: object,
        _root_task_id: object,
        _notes: object,
    ) -> Envelope:
        self._state["task_status"] = "awaiting_ceo_approval"
        return Envelope.ok(
            status="awaiting_ceo_approval",
            task_id=str(uuid4()),
            next="idle until CEO",
        )


class _MockContentActions:
    """Stateful stub for ContentActions; only note() is exercised here."""

    async def note(
        self,
        *,
        agent_id: object,
        text: object,
        scope: str = "note",
        task_id: object = None,
        structured: object = None,
    ) -> Envelope:
        # `structured` mirrors the Wave 2 G4 production signature (panel
        # decision/reflect fields). The mock ignores it — the test asserts
        # lifecycle transitions, not journal-entry rendering.
        _ = agent_id
        _ = text
        _ = scope
        _ = task_id
        _ = structured
        return Envelope.ok(status="noted", task_id=None, next="continue")


@pytest.fixture
def stateful_app() -> Iterator[tuple[FastAPI, _State]]:
    """FastAPI app with stateful mocks (Choreographer + ContentActions)."""
    app = _build_app()
    state: _State = {"task_status": "pending"}
    chor = _MockChoreographer(state)
    actions = _MockContentActions()
    app.dependency_overrides[get_choreographer] = lambda: chor
    app.dependency_overrides[get_content_actions] = lambda: actions
    yield app, state
    app.dependency_overrides.clear()


async def test_pending_to_awaiting_ceo_approval(stateful_app: tuple) -> None:
    """Drive task pending -> awaiting_ceo_approval through all gateway roles."""
    app, _state = stateful_app
    client = TestClient(app)
    dev_id = str(uuid4())
    qa_id = str(uuid4())
    doc_id = str(uuid4())
    cell_pm_id = str(uuid4())
    main_pm_id = str(uuid4())

    # ------------------------------------------------------------------
    # 1. Dev: give_me_work -> i_will_work_on -> note -> i_am_done
    # ------------------------------------------------------------------
    r = client.post(
        "/api/v1/flow/developer/give_me_work",
        headers={"X-Agent-ID": dev_id, "X-Agent-Role": "developer"},
        json={},
    )
    assert r.status_code == _HTTP_200

    r = client.post(
        "/api/v1/flow/developer/i_will_work_on",
        headers={"X-Agent-ID": dev_id, "X-Agent-Role": "developer"},
        json={"task_id": str(uuid4()), "plan": "edit x then y"},
    )
    assert r.json()["status"] == "in_progress"

    r = client.post(
        "/api/v1/do/note",
        headers={"X-Agent-ID": dev_id, "X-Agent-Role": "developer"},
        json={"text": "Reflected: did the work as planned.", "scope": "reflect"},
    )
    assert r.json()["error"] is None

    r = client.post(
        "/api/v1/flow/developer/i_am_done",
        headers={"X-Agent-ID": dev_id, "X-Agent-Role": "developer"},
        json={"task_id": str(uuid4()), "notes": "all done"},
    )
    assert r.json()["status"] == "awaiting_qa"
    # Inline evidence in the envelope kills issue #15 (QA false-failing "no PR")
    assert r.json()["evidence"]["pr_number"] == _PR_NUMBER

    # ------------------------------------------------------------------
    # 2. QA: claim_review -> note -> pass
    # ------------------------------------------------------------------
    r = client.post(
        "/api/v1/flow/qa/claim_review",
        headers={"X-Agent-ID": qa_id, "X-Agent-Role": "qa"},
        json={"task_id": str(uuid4())},
    )
    assert r.json()["evidence"]["pr_number"] == _PR_NUMBER

    r = client.post(
        "/api/v1/do/note",
        headers={"X-Agent-ID": qa_id, "X-Agent-Role": "developer"},
        json={
            "text": "Reviewed; all acceptance criteria addressed.",
            "scope": "learning",
        },
    )
    assert r.json()["error"] is None

    long_notes = (
        "Reviewed PR #8 carefully. Branch convention correct. Commit message "
        "includes task ID. README diff matches spec. No security concerns."
    )
    r = client.post(
        "/api/v1/flow/qa/pass",
        headers={"X-Agent-ID": qa_id, "X-Agent-Role": "qa"},
        json={"task_id": str(uuid4()), "notes": long_notes},
    )
    assert r.json()["status"] == "awaiting_documentation"

    # ------------------------------------------------------------------
    # 3. Doc: claim_doc_task -> i_documented
    # ------------------------------------------------------------------
    r = client.post(
        "/api/v1/flow/documenter/claim_doc_task",
        headers={"X-Agent-ID": doc_id, "X-Agent-Role": "documenter"},
        json={"task_id": str(uuid4())},
    )
    assert r.status_code == _HTTP_200

    r = client.post(
        "/api/v1/flow/documenter/i_documented",
        headers={"X-Agent-ID": doc_id, "X-Agent-Role": "documenter"},
        json={
            "task_id": str(uuid4()),
            "notes": "Wrote backend/guides/feature-x.md covering usage and config.",
            "files": ["backend/guides/feature-x.md"],
        },
    )
    assert r.json()["status"] == "awaiting_pm_review"

    # ------------------------------------------------------------------
    # 4. Cell PM: note -> complete (auto-merges leaf PR)
    # ------------------------------------------------------------------
    r = client.post(
        "/api/v1/do/note",
        headers={"X-Agent-ID": cell_pm_id, "X-Agent-Role": "developer"},
        json={"text": "Decision: approve and merge.", "scope": "decision"},
    )
    assert r.json()["error"] is None

    r = client.post(
        "/api/v1/flow/cell_pm/complete",
        headers={"X-Agent-ID": cell_pm_id, "X-Agent-Role": "cell_pm"},
        json={"task_id": str(uuid4()), "notes": "Approved and merged"},
    )
    assert r.json()["status"] == "completed"

    # ------------------------------------------------------------------
    # 5. Main PM: note -> complete root (opens master PR + escalates to CEO)
    # ------------------------------------------------------------------
    r = client.post(
        "/api/v1/do/note",
        headers={"X-Agent-ID": main_pm_id, "X-Agent-Role": "developer"},
        json={"text": "Root task ready for prod.", "scope": "decision"},
    )
    assert r.json()["error"] is None

    r = client.post(
        "/api/v1/flow/main_pm/complete",
        headers={"X-Agent-ID": main_pm_id, "X-Agent-Role": "main_pm"},
        json={"task_id": str(uuid4()), "notes": "Ready for prod"},
    )
    assert r.json()["status"] == "awaiting_ceo_approval"
