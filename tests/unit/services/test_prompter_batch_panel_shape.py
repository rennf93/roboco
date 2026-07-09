"""Reproduce the panel's confirm-batch payload shape.

The panel's ``rebuildTheWork`` writes each task's project into
``the_work[].project_id`` and sets the top-level ``project_id`` to ``null``
(multi-cell model), whereas the existing MegaTask tests use the legacy
top-level ``project_id``. This drives ``confirm_live_batch`` with the real
panel shape to surface the live 500.
"""

from __future__ import annotations

from typing import Any, Literal, cast
from unittest.mock import patch
from uuid import uuid4

import pytest
from roboco.services.prompter import get_prompter_service
from tests.unit.services.test_prompter import (
    _FakeRedis,
    _seed_project_and_ceo,
    _seed_second_project,
)


@pytest.mark.asyncio
async def test_confirm_live_batch_panel_the_work_shape(db_session: Any) -> None:
    project1, ceo_id = await _seed_project_and_ceo(db_session)
    project2 = await _seed_second_project(db_session, ceo_id)
    service = get_prompter_service(db=db_session)

    # Panel shape: project lives in the_work[].project_id, top-level is null.
    drafts: list[dict[str, Any]] = [
        {
            "title": "A: backend work",
            "acceptance_criteria": ["a"],
            "project_id": None,
            "the_work": [
                {
                    "team": "backend",
                    "summary": "do the thing",
                    "items": ["unit 1"],
                    "project_id": str(project1),
                }
            ],
            "intends_to_touch": ["roboco/services/foo.py"],
        },
        {
            "title": "B: frontend work",
            "acceptance_criteria": ["b"],
            "project_id": None,
            "the_work": [
                {
                    "team": "frontend",
                    "summary": "ui",
                    "items": ["unit 1"],
                    "project_id": str(project2),
                }
            ],
            "intends_to_touch": ["panel/src/widget.tsx"],
        },
    ]
    with patch("roboco.services.prompter.redis.from_url", return_value=_FakeRedis()):
        result = await service.confirm_live_batch(
            "Panel shape batch",
            drafts,
            ceo_id,
            project_ids=[project1, project2],
            route="main_pm",
            session_id=f"sess-panel-{uuid4().hex[:8]}",
        )

    ids = result["root_subtask_ids"]
    assert len(ids) == len(drafts)


@pytest.mark.asyncio
async def test_confirm_live_batch_panel_multicell_root_subtask(db_session: Any) -> None:
    """A batch draft that targets TWO cells (backend+frontend, one repo each) →
    the cell_projects persistence path on a batch root-subtask."""
    project1, ceo_id = await _seed_project_and_ceo(db_session)
    project2 = await _seed_second_project(db_session, ceo_id)
    service = get_prompter_service(db=db_session)

    drafts: list[dict[str, Any]] = [
        {
            "title": "A: cross-cell task",
            "acceptance_criteria": ["a"],
            "project_id": None,
            "the_work": [
                {
                    "team": "backend",
                    "summary": "api",
                    "items": ["u1"],
                    "project_id": str(project1),
                },
                {
                    "team": "frontend",
                    "summary": "ui",
                    "items": ["u2"],
                    "project_id": str(project2),
                },
            ],
        },
        {
            "title": "B: backend only",
            "acceptance_criteria": ["b"],
            "project_id": None,
            "the_work": [
                {
                    "team": "backend",
                    "summary": "more api",
                    "items": ["u1"],
                    "project_id": str(project1),
                },
            ],
        },
    ]
    with patch("roboco.services.prompter.redis.from_url", return_value=_FakeRedis()):
        result = await service.confirm_live_batch(
            "Cross-cell batch",
            drafts,
            ceo_id,
            project_ids=[project1, project2],
            route="main_pm",
            session_id=f"sess-mc-{uuid4().hex[:8]}",
        )
    assert len(result["root_subtask_ids"]) == len(drafts)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["main_pm", "board"])
async def test_confirm_live_batch_dense_same_repo_collisions(
    db_session: Any, route: str
) -> None:
    """The real panel-MegaTask shape: MANY tasks in the SAME repo with heavy
    file overlap → dense sequencing edges. Repro for the live confirm-batch 500."""
    project1, ceo_id = await _seed_project_and_ceo(db_session)
    project2 = await _seed_second_project(db_session, ceo_id)
    service = get_prompter_service(db=db_session)

    shared = "panel/src/app/layout.tsx"
    drafts: list[dict[str, Any]] = []
    # 6 backend/panel tasks in project1, overlapping on a shared file + migrations.
    for i in range(6):
        drafts.append(
            {
                "title": f"Panel task {i}",
                "acceptance_criteria": [f"c{i}"],
                "project_id": None,
                "the_work": [
                    {
                        "team": "frontend",
                        "summary": f"s{i}",
                        "items": ["u"],
                        "project_id": str(project1),
                    },
                ],
                "intends_to_touch": [shared, f"panel/src/f{i}.tsx"],
                "adds_migration": i % 2 == 0,
                "touches_shared": True,
            }
        )
    # 1 task in project2 so the batch spans ≥2 repos.
    drafts.append(
        {
            "title": "Other repo",
            "acceptance_criteria": ["c"],
            "project_id": None,
            "the_work": [
                {
                    "team": "frontend",
                    "summary": "s",
                    "items": ["u"],
                    "project_id": str(project2),
                },
            ],
            "intends_to_touch": ["src/other.ts"],
        }
    )
    with patch("roboco.services.prompter.redis.from_url", return_value=_FakeRedis()):
        result = await service.confirm_live_batch(
            "Panel MegaTask",
            drafts,
            ceo_id,
            project_ids=[project1, project2],
            route=cast("Literal['board', 'main_pm']", route),
            session_id=f"sess-dense-{route}-{uuid4().hex[:8]}",
        )
    assert len(result["root_subtask_ids"]) == len(drafts)


class _DeleteFakeRedis(_FakeRedis):
    """_FakeRedis + delete tracking, for the guard-release-on-failure test."""

    def __init__(self) -> None:
        super().__init__()
        self.deleted: list[str] = []

    async def delete(self, name: str) -> int:
        self.deleted.append(name)
        return int(self._store.pop(name, None) is not None)


@pytest.mark.asyncio
async def test_confirm_live_batch_releases_guard_on_build_failure(
    db_session: Any,
) -> None:
    """A build failure must DELETE the idempotency guard so a retry re-attempts,
    instead of the 1h key wedging every retry into a 500 ('already in progress').
    """
    project1, ceo_id = await _seed_project_and_ceo(db_session)
    project2 = await _seed_second_project(db_session, ceo_id)
    service = get_prompter_service(db=db_session)
    drafts: list[dict[str, Any]] = [
        {
            "title": "A",
            "acceptance_criteria": ["a"],
            "team": "backend",
            "project_id": str(project1),
        },
        {
            "title": "B",
            "acceptance_criteria": ["b"],
            "team": "frontend",
            "project_id": str(project2),
        },
    ]
    fake = _DeleteFakeRedis()
    session_id = f"sess-fail-{uuid4().hex[:8]}"

    async def _boom(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("build blew up")

    with patch("roboco.services.prompter.redis.from_url", return_value=fake):
        with (
            patch.object(service, "_build_confirm_batch", _boom),
            pytest.raises(RuntimeError, match="build blew up"),
        ):
            await service.confirm_live_batch(
                "Batch",
                drafts,
                ceo_id,
                project_ids=[project1, project2],
                route="main_pm",
                session_id=session_id,
            )
        # The guard was released, so the key no longer holds the session.
        assert f"roboco:megatask_confirm:{session_id}" in fake.deleted
        # A retry can now re-acquire the guard (not a permanent 'in progress').
        assert await service._acquire_confirm_guard(session_id) is None
